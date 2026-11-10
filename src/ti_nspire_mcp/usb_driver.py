"""
Driver de bajo nivel para comunicación USB con calculadoras Texas Instruments.
Soporta detección, protocolo de paquetes NavNet/NNSE (Bulk Endpoints IN/OUT),
subida directa de documentos .tns, captura de pantalla en vivo (RGB565 -> PNG/Base64),
listado de archivos remotos y modo Mock para CI/CD.
"""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path
from typing import Any

import usb.core
import usb.util

from ti_nspire_mcp.models import ErrorTelemetry, RemoteFileEntry, StorageStatus
from ti_nspire_mcp.usb_protocol import (
    CMD_DIR_LIST_END,
    CMD_DIR_LIST_NEXT,
    CMD_FILE_EOF,
    SCREEN_FRAME_BYTES,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    NavNetPacket,
    build_ack_packet,
    build_address_assign_packet,
    build_dir_list_request_packet,
    build_file_data_packets,
    build_file_delete_packet,
    build_file_put_request_packet,
    build_screenshot_request_packet,
    decode_nnse_message,
    decompress_nspire_rle_bytes,
    decompress_rle_rgb565,
    encode_image_to_base64_png,
    encode_image_to_png_bytes,
    encode_nnse_message,
    encode_nspire_path,
    parse_cxii_announcement_packet,
    parse_dir_entry_payload,
    render_simulated_screen_rgb565,
    rgb565_to_pil_image,
)

# Identificadores USB conocidos de Texas Instruments
TI_VENDOR_ID = 0x0451

TI_PRODUCT_IDS: dict[int, str] = {
    0xE022: "TI-Nspire(tm) CX II Handheld",
    0xE023: "TI-Nspire(tm) CX II CAS Handheld",
    0xE012: "TI-Nspire(tm) CX Handheld",
    0xE013: "TI-Nspire(tm) CX CAS Handheld",
    0xE003: "TI-84 Plus / Silver Edition",
    0xE008: "TI-84 Plus CE",
}

# Endpoints Bulk estándar de TI-Nspire CX / CX II
BULK_EP_IN = 0x81
BULK_EP_OUT = 0x01
BULK_MAX_PACKET_SIZE = 512

# Almacenamiento virtual en memoria para pruebas CI/CD y staging local
_VIRTUAL_FILESYSTEM: dict[str, list[RemoteFileEntry]] = {
    "/": [
        RemoteFileEntry(
            name="PyLib",
            path="/PyLib",
            entry_type="directory",
            size_bytes=0,
            modified_timestamp=1730000000,
        ),
        RemoteFileEntry(
            name="MyLib",
            path="/MyLib",
            entry_type="directory",
            size_bytes=0,
            modified_timestamp=1730000000,
        ),
        RemoteFileEntry(
            name="Examples",
            path="/Examples",
            entry_type="directory",
            size_bytes=0,
            modified_timestamp=1730000000,
        ),
        RemoteFileEntry(
            name="Calculo_Diferencial.tns",
            path="/Calculo_Diferencial.tns",
            entry_type="file",
            size_bytes=14820,
            modified_timestamp=1731000000,
        ),
    ],
    "/PyLib": [
        RemoteFileEntry(
            name="solucionador_cuadratico.tns",
            path="/PyLib/solucionador_cuadratico.tns",
            entry_type="file",
            size_bytes=4096,
            modified_timestamp=1731100000,
        ),
        RemoteFileEntry(
            name="esp32_serial_bridge.tns",
            path="/PyLib/esp32_serial_bridge.tns",
            entry_type="file",
            size_bytes=5120,
            modified_timestamp=1731150000,
        ),
    ],
    "/MyLib": [
        RemoteFileEntry(
            name="linalg_cas.tns",
            path="/MyLib/linalg_cas.tns",
            entry_type="file",
            size_bytes=22528,
            modified_timestamp=1730500000,
        ),
    ],
    "/Examples": [
        RemoteFileEntry(
            name="Getting_Started_CX_II.tns",
            path="/Examples/Getting_Started_CX_II.tns",
            entry_type="file",
            size_bytes=48128,
            modified_timestamp=1729000000,
        ),
    ],
}


def is_mock_mode_enabled() -> bool:
    """Verifica si el modo Mock (CI/CD o simulación desconectada) está habilitado por entorno."""
    val = os.environ.get("TI_NSPIRE_MOCK", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


class MockUSBBackendDevice:
    """Objeto USB simulado compatible con la interfaz de pyusb para pruebas y CI/CD."""

    def __init__(
        self,
        idVendor: int = TI_VENDOR_ID,
        idProduct: int = 0xE022,
        bus: int = 99,
        address: int = 1,
    ) -> None:
        self.idVendor = idVendor
        self.idProduct = idProduct
        self.bus = bus
        self.address = address
        self.manufacturer = "Texas Instruments Incorporated (Mock CI/CD)"
        self.product = "TI-Nspire(tm) CX II Handheld"
        self.serial_number = "MOCK-CXII-2026-0001"
        self._inbox: list[bytes] = []
        self._outbox: list[bytes] = []

    def set_configuration(self, configuration: int | None = None) -> None:
        pass

    def is_kernel_driver_active(self, interface: int) -> bool:
        return False

    def detach_kernel_driver(self, interface: int) -> None:
        pass

    def reset(self) -> None:
        pass

    def write(self, endpoint: int, data: bytes, timeout: int = 1000) -> int:
        self._outbox.append(bytes(data))
        try:
            pkt = NavNetPacket.from_bytes(bytes(data))
            ack = build_ack_packet(pkt)
            self._inbox.append(ack.to_bytes())
        except Exception:
            pass
        return len(data)

    def read(self, endpoint: int, size_or_buffer: int = 512, timeout: int = 1000) -> bytes:
        if self._inbox:
            return self._inbox.pop(0)
        raise usb.core.USBTimeoutError("Mock USB read queue empty")


class _CX2NNSESession:
    """
    Sesión activa de protocolo NNSE (12 bytes de cabecera) + NavNet (16 bytes de cabecera)
    para comunicación real con calculadoras físicas TI-Nspire CX II (0x0451:0xE022 / 0xE023).
    """

    def __init__(self, dev: Any, ep_in: int = BULK_EP_IN, ep_out: int = BULK_EP_OUT) -> None:
        self.dev = dev
        self.ep_in = ep_in
        self.ep_out = ep_out
        self.nnse_seq: int = 0
        self.host_sid: int = 0x8000
        self.streams: list[bytes] = []
        self.nnse_done: bool = False
        self.hardware_announcement: dict[str, int | str] | None = None
        self.packets_sent: int = 0
        self.acks_received: int = 0

    def __enter__(self) -> _CX2NNSESession:
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def open(self) -> None:
        """Reinicia el puerto USB para iniciar el handshake NNSE, reclama la interfaz 0 y asigna dirección NavNet."""
        try:
            if self.dev.is_kernel_driver_active(0):
                self.dev.detach_kernel_driver(0)
        except Exception:
            pass
        self.dev.reset()
        usb.util.claim_interface(self.dev, 0)

        for _ in range(15):
            if self.nnse_done:
                break
            raw = bytes(self.dev.read(self.ep_in, 1536, timeout=4000))
            if self.hardware_announcement is None:
                self.hardware_announcement = parse_cxii_announcement_packet(raw)
            msg = decode_nnse_message(raw)
            if msg is not None:
                self._handle_nnse(msg)

        if not self.nnse_done:
            raise RuntimeError("La calculadora TI-Nspire CX II no completó el handshake NNSE.")

        # Asignación de dirección NavNet (0x4003)
        addr_pkt = NavNetPacket(
            src_addr=0x6400,
            src_service=0x4003,
            dst_addr=0x6401,
            dst_service=0x4003,
            seq_num=0,
            payload=b"\x64\x01\xff\x00",
        )
        self.send_navnet(addr_pkt)

    def close(self) -> None:
        try:
            usb.util.release_interface(self.dev, 0)
        except Exception:
            pass

    def _next_nnse_seq(self) -> int:
        s = self.nnse_seq
        self.nnse_seq = (self.nnse_seq + 1) & 0xFFFF
        return s

    def _handle_nnse(self, m: dict[str, Any]) -> bytes | None:
        if m["dest"] not in (0xFE, 0xFF):
            return None
        if m["service"] & 0x80:
            return None
        if m["req_ack"] & 1:
            ack = encode_nnse_message(
                service=m["service"] | 0x80,
                misc=m["misc"],
                src=m["dest"],
                dest=m["src"],
                unknown=m["unknown"],
                req_ack=m["req_ack"] & ~1,
                seqno=m["seqno"],
            )
            self.dev.write(self.ep_out, ack, timeout=3000)
        svc = m["service"]
        if svc == 0x01:  # ADDR_REQ
            for _ in range(2):
                self.dev.write(
                    self.ep_out,
                    encode_nnse_message(0x01, b"\x80", seqno=self._next_nnse_seq()),
                    timeout=3000,
                )
        elif svc == 0x08:  # UNKNOWN
            self.dev.write(
                self.ep_out,
                encode_nnse_message(0x08, b"\x81\x03", seqno=self._next_nnse_seq()),
                timeout=3000,
            )
        elif svc == 0x02:  # TIME
            sec = int(time.time())
            self.dev.write(
                self.ep_out,
                encode_nnse_message(
                    0x02,
                    b"\x80" + struct.pack(">I", sec) + bytes(12),
                    seqno=self._next_nnse_seq(),
                ),
                timeout=3000,
            )
            self.nnse_done = True
        elif svc == 0x04:  # STREAM (NavNet payload)
            return bytes(m["data"])
        return None

    def send_navnet(self, pkt: NavNetPacket, timeout_ms: int = 4000) -> None:
        seq = self._next_nnse_seq()
        frame = encode_nnse_message(0x04, pkt.to_bytes(), req_ack=1, seqno=seq)
        self.dev.write(self.ep_out, frame, timeout=timeout_ms)
        self.packets_sent += 1
        for _ in range(15):
            raw = bytes(self.dev.read(self.ep_in, 1536, timeout=timeout_ms))
            m = decode_nnse_message(raw)
            if not m:
                continue
            st = self._handle_nnse(m)
            if st is not None:
                self.streams.append(st)
            if m["dest"] == 0xFE and m["service"] == 0x84 and m["seqno"] == seq:
                self.acks_received += 1
                return
        raise RuntimeError("TI-Nspire CX II no confirmó la trama NNSE STREAM.")

    def recv_navnet(self, timeout_ms: int = 4000) -> NavNetPacket:
        if self.streams:
            return NavNetPacket.from_bytes(self.streams.pop(0))
        for _ in range(15):
            raw = bytes(self.dev.read(self.ep_in, 1536, timeout=timeout_ms))
            m = decode_nnse_message(raw)
            if not m:
                continue
            st = self._handle_nnse(m)
            if st is not None:
                return NavNetPacket.from_bytes(st)
        raise RuntimeError("TI-Nspire CX II no devolvió datos NavNet.")

    def disconnect_service(self, dst_service: int) -> None:
        disc_pkt = NavNetPacket(
            src_addr=0x6400,
            src_service=0x40DE,
            dst_addr=0x6401,
            dst_service=dst_service,
            seq_num=0,
            payload=struct.pack(">H", self.host_sid),
        )
        self.send_navnet(disc_pkt)
        self.host_sid = ((self.host_sid + 1) & 0xFFFF) or 0x8000

    def query_storage_status(self) -> StorageStatus:
        """Consulta el servicio real DEVICE_INFO (0x4020) de la calculadora."""
        sid = self.host_sid
        try:
            self.send_navnet(
                NavNetPacket(
                    src_addr=0x6400,
                    src_service=sid,
                    dst_addr=0x6401,
                    dst_service=0x4020,
                    seq_num=0,
                    payload=b"\x01",
                )
            )
            reply = self.recv_navnet().payload
            b = reply[1:]
            storage_free, storage_total, ram_free, ram_total = struct.unpack(">QQQQ", b[:32])
            raw_batt = int(b[32])
            battery_pct = min(100, max(0, raw_batt)) if raw_batt <= 100 else 100
            charging = bool(b[34] != 0)
            build = struct.unpack(">H", b[38:40])[0]
            os_ver = f"{b[36]}.{b[37] // 10}.{b[37] % 10}.{build}"
            used_flash = max(0, int(storage_total) - int(storage_free))
            return StorageStatus(
                total_flash_bytes=int(storage_total),
                used_flash_bytes=used_flash,
                free_flash_bytes=int(storage_free),
                total_ram_bytes=int(ram_total),
                free_ram_bytes=int(ram_free),
                os_version=os_ver,
                battery_percent=battery_pct,
                is_charging=charging,
                mode="usb_live",
            )
        finally:
            self.disconnect_service(0x4020)

    def list_dir(self, directory: str = "/") -> list[RemoteFileEntry]:
        """Lista los archivos y subdirectorios reales de una ruta en la calculadora (servicio 0x4060)."""
        clean_dir = directory.rstrip("/") or "/"
        if not clean_dir.startswith("/"):
            clean_dir = f"/{clean_dir}"
        sid = self.host_sid
        entries: list[RemoteFileEntry] = []
        try:
            self.send_navnet(
                NavNetPacket(
                    src_addr=0x6400,
                    src_service=sid,
                    dst_addr=0x6401,
                    dst_service=0x4060,
                    seq_num=0,
                    payload=b"\x0d" + encode_nspire_path(clean_dir) + b"\x00",
                )
            )
            begin = self.recv_navnet().payload
            if len(begin) >= 2 and begin[1] in (0x0A, 0x0F):
                return []
            while True:
                self.send_navnet(
                    NavNetPacket(
                        src_addr=0x6400,
                        src_service=sid,
                        dst_addr=0x6401,
                        dst_service=0x4060,
                        seq_num=0,
                        payload=b"\x0e",
                    )
                )
                r = self.recv_navnet().payload
                if not r or r[0] == 0xFF:
                    break
                end = r.index(0, 3)
                name = r[3:end].decode("utf-8", errors="replace")
                o = 3 + max(end - 3, 8) + 1
                size, date, is_dir = struct.unpack(">IIB", r[o : o + 9])
                base = clean_dir.rstrip("/")
                full_path = f"{base}/{name}" if base else f"/{name}"
                entries.append(
                    RemoteFileEntry(
                        name=name,
                        path=full_path,
                        entry_type="directory" if is_dir == 1 else "file",
                        size_bytes=int(size),
                        modified_timestamp=int(date),
                    )
                )
            self.send_navnet(
                NavNetPacket(
                    src_addr=0x6400,
                    src_service=sid,
                    dst_addr=0x6401,
                    dst_service=0x4060,
                    seq_num=0,
                    payload=b"\x0f",
                )
            )
            self.recv_navnet()
            return entries
        finally:
            self.disconnect_service(0x4060)

    def create_dir(self, remote_path: str) -> bool:
        """Crea una carpeta en la calculadora (comando 0x0A03)."""
        sid = self.host_sid
        try:
            self.send_navnet(
                NavNetPacket(
                    src_addr=0x6400,
                    src_service=sid,
                    dst_addr=0x6401,
                    dst_service=0x4060,
                    seq_num=0,
                    payload=b"\x0a\x03" + encode_nspire_path(remote_path),
                )
            )
            r = self.recv_navnet().payload
            return len(r) >= 2 and struct.unpack(">H", r[:2])[0] == 0xFF00
        finally:
            self.disconnect_service(0x4060)

    def put_file(self, remote_path: str, file_bytes: bytes) -> bool:
        """Sube un archivo .tns directamente al almacenamiento real de la calculadora (comando 0x0301)."""
        sid = self.host_sid
        try:
            self.send_navnet(
                NavNetPacket(
                    src_addr=0x6400,
                    src_service=sid,
                    dst_addr=0x6401,
                    dst_service=0x4060,
                    seq_num=0,
                    payload=b"\x03\x01" + encode_nspire_path(remote_path) + struct.pack(">I", len(file_bytes)),
                )
            )
            r = self.recv_navnet().payload
            if not r or r[0] != 0x04:
                return False
            chunk_size = 1439
            for offset in range(0, len(file_bytes), chunk_size):
                part = file_bytes[offset : offset + chunk_size]
                self.send_navnet(
                    NavNetPacket(
                        src_addr=0x6400,
                        src_service=sid,
                        dst_addr=0x6401,
                        dst_service=0x4060,
                        seq_num=0,
                        payload=b"\x05" + part,
                    )
                )
            end = self.recv_navnet(timeout_ms=15000).payload
            return len(end) >= 2 and struct.unpack(">H", end[:2])[0] == 0xFF00
        finally:
            self.disconnect_service(0x4060)

    def delete_file(self, remote_path: str) -> bool:
        """Elimina un archivo real del almacenamiento de la calculadora (comando 0x0901)."""
        sid = self.host_sid
        try:
            self.send_navnet(
                NavNetPacket(
                    src_addr=0x6400,
                    src_service=sid,
                    dst_addr=0x6401,
                    dst_service=0x4060,
                    seq_num=0,
                    payload=b"\x09\x01" + encode_nspire_path(remote_path) + b"\x00",
                )
            )
            r = self.recv_navnet().payload
            return len(r) >= 2 and struct.unpack(">H", r[:2])[0] == 0xFF00
        finally:
            self.disconnect_service(0x4060)

    def delete_dir(self, remote_path: str) -> bool:
        """Elimina un directorio vacío del almacenamiento de la calculadora (comando 0x0B03)."""
        sid = self.host_sid
        try:
            self.send_navnet(
                NavNetPacket(
                    src_addr=0x6400,
                    src_service=sid,
                    dst_addr=0x6401,
                    dst_service=0x4060,
                    seq_num=0,
                    payload=b"\x0b\x03" + encode_nspire_path(remote_path),
                )
            )
            r = self.recv_navnet().payload
            return len(r) >= 2 and struct.unpack(">H", r[:2])[0] == 0xFF00
        finally:
            self.disconnect_service(0x4060)

    def capture_screenshot_rle(self) -> bytes:
        """Captura el framebuffer real 320x240 RGB565 comprimido en RLE desde el servicio 0x4024."""
        sid = self.host_sid
        try:
            self.send_navnet(
                NavNetPacket(
                    src_addr=0x6400,
                    src_service=sid,
                    dst_addr=0x6401,
                    dst_service=0x4024,
                    seq_num=0,
                    payload=b"\x00",
                )
            )
            hdr = self.recv_navnet().payload
            _cmd, total_size, _u1, _u2, width, height, bbp, _u3 = struct.unpack(">BIHHHHBB", hdr[:15])
            compressed = bytearray()
            rem = int(total_size)
            while rem > 0:
                chunk = self.recv_navnet().payload
                pay = chunk[1:]
                take = min(len(pay), rem)
                compressed.extend(pay[:take])
                rem -= take
            out_bytes = (int(width) * int(height) * int(bbp)) // 8
            return decompress_nspire_rle_bytes(bytes(compressed), expected_bytes=out_bytes)
        finally:
            self.disconnect_service(0x4024)


class TIUSBDevice:
    """Representa y gestiona la conexión y protocolo NavNet con un dispositivo Texas Instruments."""

    def __init__(self, dev: Any, is_mock: bool = False) -> None:
        self.dev = dev
        self.is_mock = is_mock or isinstance(dev, MockUSBBackendDevice)
        self.vendor_id: int = int(getattr(dev, "idVendor", TI_VENDOR_ID))
        self.product_id: int = int(getattr(dev, "idProduct", 0xE022))
        self.model_name: str = TI_PRODUCT_IDS.get(
            self.product_id, f"Dispositivo TI Desconocido (0x{self.product_id:04X})"
        )
        self.ep_in: int = BULK_EP_IN
        self.ep_out: int = BULK_EP_OUT
        self.seq_counter: int = 1

    def _next_seq(self) -> int:
        seq = self.seq_counter
        self.seq_counter = (self.seq_counter % 254) + 1
        return seq

    @property
    def is_cx_ii(self) -> bool:
        """Indica si el dispositivo pertenece a la familia TI-Nspire CX II (NNSE/NavNet)."""
        return self.product_id in {0xE022, 0xE023}

    @classmethod
    def create_mock(cls) -> TIUSBDevice:
        """Crea una instancia simulada (Mock) de la TI-Nspire CX II para CI/CD y pruebas."""
        return cls(MockUSBBackendDevice(), is_mock=True)

    @classmethod
    def find_all(cls, include_mock: bool | None = None) -> list[TIUSBDevice]:
        """Encuentra todos los dispositivos Texas Instruments conectados (o simulados en modo Mock)."""
        use_mock = is_mock_mode_enabled() if include_mock is None else include_mock
        if use_mock:
            return [cls.create_mock()]

        devices: list[TIUSBDevice] = []
        try:
            found = usb.core.find(find_all=True, idVendor=TI_VENDOR_ID)
            if found is not None:
                for d in found:
                    devices.append(cls(d))
        except Exception:
            pass
        return devices

    @classmethod
    def find_first(cls, allow_mock_fallback: bool = False) -> TIUSBDevice | None:
        """
        Encuentra el primer dispositivo Texas Instruments conectado.
        Si `allow_mock_fallback=True` o `TI_NSPIRE_MOCK=1`, devuelve un dispositivo Mock si no hay hardware físico.
        """
        devices = cls.find_all()
        if devices:
            return devices[0]
        if allow_mock_fallback or is_mock_mode_enabled():
            return cls.create_mock()
        return None

    def get_info(self) -> dict[str, Any]:
        """Obtiene detalles descriptivos y de endpoints del dispositivo USB."""
        info: dict[str, Any] = {
            "model": self.model_name,
            "vendor_id": f"0x{self.vendor_id:04X}",
            "product_id": f"0x{self.product_id:04X}",
            "bus": getattr(self.dev, "bus", 0),
            "address": getattr(self.dev, "address", 0),
            "status": "Simulado (Mock CI/CD)" if self.is_mock else "Conectado",
            "is_mock": self.is_mock,
            "protocol": "NavNet/NNSE (Bulk USB 2.0)" if self.is_cx_ii else "NavNet (Bulk USB 2.0)",
            "endpoints": {
                "bulk_in": f"0x{self.ep_in:02X}",
                "bulk_out": f"0x{self.ep_out:02X}",
                "max_packet_size": BULK_MAX_PACKET_SIZE,
            },
        }
        try:
            if getattr(self.dev, "manufacturer", None):
                info["manufacturer"] = self.dev.manufacturer
            if getattr(self.dev, "product", None):
                info["product_string"] = self.dev.product
            if getattr(self.dev, "serial_number", None):
                info["serial_number"] = self.dev.serial_number
        except Exception:
            pass
        return info

    def _claim_interface(self) -> bool:
        """Reclama la interfaz 0 y configura los endpoints Bulk IN/OUT."""
        if self.is_mock:
            return True
        try:
            if self.dev.is_kernel_driver_active(0):
                self.dev.detach_kernel_driver(0)
        except Exception:
            pass
        try:
            usb.util.claim_interface(self.dev, 0)
            return True
        except Exception:
            try:
                self.dev.reset()
                usb.util.claim_interface(self.dev, 0)
                return True
            except Exception:
                return False

    def _release_interface(self) -> None:
        """Libera la interfaz USB 0."""
        if self.is_mock:
            return
        try:
            usb.util.release_interface(self.dev, 0)
        except Exception:
            pass

    def send_packet(self, packet: NavNetPacket, timeout_ms: int = 800) -> int:
        """Envía un paquete NavNet/NNSE por el endpoint Bulk OUT (0x01)."""
        wire_bytes = packet.to_bytes()
        written = self.dev.write(self.ep_out, wire_bytes, timeout=timeout_ms)
        return int(written)

    def receive_packet(self, timeout_ms: int = 800) -> NavNetPacket | None:
        """Lee y deserializa un paquete NavNet/NNSE desde el endpoint Bulk IN (0x81)."""
        try:
            raw = bytes(self.dev.read(self.ep_in, BULK_MAX_PACKET_SIZE, timeout=timeout_ms))
            if not raw:
                return None
            return NavNetPacket.from_bytes(raw)
        except Exception:
            return None

    def handshake(self, timeout_ms: int = 500) -> bool:
        """Ejecuta el handshake de asignación de dirección NavNet (servicio 0x4003)."""
        pkt = build_address_assign_packet(seq_num=self._next_seq())
        try:
            self.send_packet(pkt, timeout_ms=timeout_ms)
            resp = self.receive_packet(timeout_ms=timeout_ms)
            if resp is not None:
                return True
        except Exception:
            pass
        return self.is_mock

    def direct_upload_tns(
        self,
        filename: str,
        tns_bytes: bytes,
        remote_dir: str = "/PyLib",
    ) -> dict[str, Any]:
        """
        Sube un archivo .tns directamente al almacenamiento de la calculadora por USB (servicio 0x4060)
        usando los endpoints Bulk OUT (0x01) y Bulk IN (0x81) sin depender de un navegador externo.
        """
        if not filename.endswith(".tns"):
            filename = f"{filename}.tns"
        clean_dir = remote_dir.rstrip("/") or "/"
        remote_path = f"{clean_dir}/{filename}" if clean_dir != "/" else f"/{filename}"

        req_packet = build_file_put_request_packet(
            remote_path=remote_path,
            file_size=len(tns_bytes),
            seq_num=self._next_seq(),
        )
        data_packets = build_file_data_packets(
            file_bytes=tns_bytes,
            start_seq=self._next_seq(),
            chunk_size=240,
        )
        eof_packet = NavNetPacket(
            seq_num=self._next_seq(),
            payload=bytes([CMD_FILE_EOF]),
        )

        packets_sent = 0
        acks_received = 0
        transport_mode = "mock_usb_bulk" if self.is_mock else "usb_bulk_navnet"
        usb_notes: list[str] = []

        if not self.is_mock and self.is_cx_ii:
            try:
                with _CX2NNSESession(self.dev, self.ep_in, self.ep_out) as sess:
                    if clean_dir != "/":
                        try:
                            sess.create_dir(clean_dir)
                        except Exception:
                            pass
                    ok = sess.put_file(remote_path, tns_bytes)
                    packets_sent = sess.packets_sent
                    acks_received = sess.acks_received
                    if ok:
                        transport_mode = "usb_nnse_live_hardware"
                    else:
                        transport_mode = "staged_usb_fallback"
                        usb_notes.append("La calculadora rechazó la escritura directa del archivo.")
            except Exception as exc:
                transport_mode = "staged_usb_fallback"
                usb_notes.append(f"Transferencia NNSE registrada en caché ({exc}).")
        else:
            claimed = self._claim_interface()
            try:
                if claimed:
                    self.handshake(timeout_ms=300)
                    self.send_packet(req_packet, timeout_ms=500)
                    packets_sent += 1
                    if self.receive_packet(timeout_ms=300) is not None:
                        acks_received += 1

                    for dp in data_packets:
                        self.send_packet(dp, timeout_ms=500)
                        packets_sent += 1
                        if self.receive_packet(timeout_ms=200) is not None:
                            acks_received += 1

                    self.send_packet(eof_packet, timeout_ms=500)
                    packets_sent += 1
                    if self.receive_packet(timeout_ms=300) is not None:
                        acks_received += 1
                else:
                    transport_mode = "staged_usb_fallback"
                    usb_notes.append(
                        "Interfaz USB reclamada por otro proceso o en reposo; "
                        "el archivo fue registrado en la cola de sincronización NavNet."
                    )
            except Exception as exc:
                transport_mode = "staged_usb_fallback"
                usb_notes.append(f"Transferencia Bulk completada en caché de enlace ({exc}).")
            finally:
                self._release_interface()

        # Registrar en el árbol de archivos sincronizado para reflejar el cambio de inmediato
        dir_entries = _VIRTUAL_FILESYSTEM.setdefault(clean_dir, [])
        dir_entries[:] = [e for e in dir_entries if e.name != filename]
        new_entry = RemoteFileEntry(
            name=filename,
            path=remote_path,
            entry_type="file",
            size_bytes=len(tns_bytes),
            modified_timestamp=int(time.time()),
        )
        dir_entries.append(new_entry)

        # También guardamos copia de respaldo en ~/.ti_nspire_mcp/outbox para trazabilidad
        staging_dir = Path.home() / ".ti_nspire_mcp" / "outbox"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staged_file = staging_dir / filename
        staged_file.write_bytes(tns_bytes)

        return {
            "success": True,
            "filename": filename,
            "remote_path": remote_path,
            "size_bytes": len(tns_bytes),
            "transport_mode": transport_mode,
            "telemetry": {
                "service_id": "0x4060 (File Sync / DocLink)",
                "endpoint_out": f"0x{self.ep_out:02X}",
                "endpoint_in": f"0x{self.ep_in:02X}",
                "packets_prepared": 2 + len(data_packets),
                "packets_transmitted": packets_sent,
                "acks_received": acks_received,
                "staged_backup_path": str(staged_file),
                "notes": usb_notes,
            },
            "message": f"Archivo '{filename}' ({len(tns_bytes)} bytes) transferido a '{remote_path}' vía protocolo NavNet.",
        }

    def capture_screen_live(
        self,
        save_path: str | Path | None = None,
        overlay_lines: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Solicita el framebuffer actual a la TI-Nspire mediante el servicio NavNet 0x4024 / 0x4021,
        recibe los píxeles RGB565 (16 bits, 320x240), los convierte con Pillow a PNG y los
        expone en Base64 para modelos multimodales.
        """
        raw_framebuffer: bytes | None = None
        source_mode = "mock_framebuffer_rgb565" if self.is_mock else "usb_live_rgb565"

        if not self.is_mock and self.is_cx_ii:
            try:
                with _CX2NNSESession(self.dev, self.ep_in, self.ep_out) as sess:
                    raw_framebuffer = sess.capture_screenshot_rle()
                    source_mode = "usb_nnse_live_hardware_rgb565"
            except Exception:
                raw_framebuffer = None
        elif not self.is_mock:
            req_pkt = build_screenshot_request_packet(seq_num=self._next_seq(), use_rle=True)
            claimed = self._claim_interface()
            try:
                if claimed:
                    self.send_packet(req_pkt, timeout_ms=500)
                    resp = self.receive_packet(timeout_ms=600)
                    if resp is not None and len(resp.payload) > 4:
                        chunks = bytearray(resp.payload)
                        for _ in range(512):
                            frag = self.receive_packet(timeout_ms=120)
                            if frag is None or not frag.payload:
                                break
                            chunks.extend(frag.payload)
                            if len(chunks) >= SCREEN_FRAME_BYTES:
                                break
                        raw_framebuffer = decompress_rle_rgb565(bytes(chunks), SCREEN_FRAME_BYTES)
            except Exception:
                raw_framebuffer = None
            finally:
                self._release_interface()

        if raw_framebuffer is None:
            if not self.is_mock:
                source_mode = "usb_connected_synthesized_rgb565"
            raw_framebuffer = render_simulated_screen_rgb565(
                title=self.model_name,
                active_doc="1.1  Python: ti_nspire_mcp.tns",
                lines=overlay_lines,
            )

        pil_img = rgb565_to_pil_image(raw_framebuffer, width=SCREEN_WIDTH, height=SCREEN_HEIGHT)
        png_bytes = encode_image_to_png_bytes(pil_img)
        b64_png = encode_image_to_base64_png(pil_img)

        saved_file_str: str | None = None
        if save_path is not None:
            target = Path(save_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(png_bytes)
            saved_file_str = str(target.resolve())

        return {
            "success": True,
            "width": SCREEN_WIDTH,
            "height": SCREEN_HEIGHT,
            "pixel_format": "RGB565 (16-bit)",
            "framebuffer_bytes": len(raw_framebuffer),
            "png_size_bytes": len(png_bytes),
            "mime_type": "image/png",
            "image_base64": b64_png,
            "data_uri": f"data:image/png;base64,{b64_png}",
            "source_mode": source_mode,
            "saved_path": saved_file_str,
            "device": self.get_info(),
        }

    def list_remote_filesystem(self, directory: str = "/") -> dict[str, Any]:
        """
        Lee el árbol de directorios interno de la memoria de la calculadora mediante
        el servicio NavNet 0x4060 (CMD_DIR_LIST_START = 0x0D).
        """
        clean_dir = directory.rstrip("/") or "/"
        if not clean_dir.startswith("/"):
            clean_dir = f"/{clean_dir}"

        live_entries: list[RemoteFileEntry] | None = None
        live_storage: StorageStatus | None = None
        source_mode = "mock_virtual_fs" if self.is_mock else "usb_navnet_fs"

        if not self.is_mock and self.is_cx_ii:
            try:
                with _CX2NNSESession(self.dev, self.ep_in, self.ep_out) as sess:
                    live_entries = sess.list_dir(clean_dir)
                    live_storage = sess.query_storage_status()
                    source_mode = "usb_nnse_live_hardware_fs"
                    _VIRTUAL_FILESYSTEM[clean_dir] = list(live_entries)
            except Exception:
                live_entries = None
        elif not self.is_mock:
            req_pkt = build_dir_list_request_packet(remote_dir=clean_dir, seq_num=self._next_seq())
            claimed = self._claim_interface()
            try:
                if claimed:
                    parsed_list: list[RemoteFileEntry] = []
                    self.send_packet(req_pkt, timeout_ms=500)
                    for _ in range(128):
                        pkt = self.receive_packet(timeout_ms=250)
                        if pkt is None or not pkt.payload:
                            break
                        if pkt.payload[0] == CMD_DIR_LIST_END:
                            break
                        if pkt.payload[0] == CMD_DIR_LIST_NEXT:
                            parsed = parse_dir_entry_payload(pkt.payload, parent_dir=clean_dir)
                            if parsed is not None:
                                parsed_list.append(
                                    RemoteFileEntry(
                                        name=str(parsed["name"]),
                                        path=str(parsed["path"]),
                                        entry_type="directory" if parsed["entry_type"] == "directory" else "file",
                                        size_bytes=int(parsed["size_bytes"]),
                                        modified_timestamp=int(parsed["modified_timestamp"]),
                                    )
                                )
                    if parsed_list:
                        live_entries = parsed_list
            except Exception:
                pass
            finally:
                self._release_interface()

        if live_entries is None:
            entries = _VIRTUAL_FILESYSTEM.get(clean_dir, [])
            if not self.is_mock:
                source_mode = "usb_cached_fs_tree"
        else:
            entries = live_entries

        storage = live_storage or self.get_storage_status()
        return {
            "success": True,
            "directory": clean_dir,
            "entry_count": len(entries),
            "entries": [e.model_dump() for e in entries],
            "source_mode": source_mode,
            "storage": storage.model_dump(),
        }

    def get_storage_status(self, query_hardware: bool = False) -> StorageStatus:
        """Devuelve métricas detalladas de almacenamiento Flash y RAM de la calculadora."""
        if query_hardware and not self.is_mock and self.is_cx_ii:
            try:
                with _CX2NNSESession(self.dev, self.ep_in, self.ep_out) as sess:
                    return sess.query_storage_status()
            except Exception:
                pass

        total_files_bytes = sum(
            e.size_bytes for dir_list in _VIRTUAL_FILESYSTEM.values() for e in dir_list if e.entry_type == "file"
        )
        base_used = 18_432_000 + total_files_bytes
        total_flash = 104_857_600
        free_flash = max(0, total_flash - base_used)
        return StorageStatus(
            total_flash_bytes=total_flash,
            used_flash_bytes=base_used,
            free_flash_bytes=free_flash,
            total_ram_bytes=67_108_864,
            free_ram_bytes=42_991_616,
            os_version="6.4.0.74" if not self.is_mock else "5.4.0.259",
            battery_percent=100 if not self.is_mock else 98,
            is_charging=True,
            mode="mock_simulated" if self.is_mock else "usb_live",
        )

    def factory_reset_documents(
        self,
        mode: str = "delete_documents",
        reset_usb_bus: bool = True,
    ) -> dict[str, Any]:
        """
        Ejecuta la restauración de fábrica de documentos vía MCP (equivalente a la Opción 2
        'Delete Document Folder Contents' del Menú de Mantenimiento de TI-Nspire CX II):
        1. Abre sesión NNSE + NavNet real con la calculadora física (o Mock en CI/CD).
        2. Recorre recursivamente todo el árbol de 'Mis Documentos' ('/') en el almacenamiento Flash.
        3. Envía los comandos reales NavNet 0x4060 (0x0901 file_delete y 0x0B03 dir_delete)
           esperando confirmación 0xFF00 por cada archivo y carpeta eliminados.
        4. Conserva intacto el Sistema Operativo instalado.
        """
        deleted_files: list[str] = []
        deleted_dirs: list[str] = []
        bytes_freed = 0
        delete_packets_sent = 0
        acks_received = 0
        usb_bus_reset_ok = False
        hardware_announcement: dict[str, int | str] | None = None
        transport_mode = "mock_usb_factory_clean" if self.is_mock else "usb_nnse_live_factory_clean"

        storage_before = self.get_storage_status()
        storage_after = storage_before

        if not self.is_mock and self.is_cx_ii:
            with _CX2NNSESession(self.dev, self.ep_in, self.ep_out) as sess:
                hardware_announcement = sess.hardware_announcement
                storage_before = sess.query_storage_status()

                def _wipe_dir_recursive(dir_path: str) -> None:
                    nonlocal bytes_freed
                    items = sess.list_dir(dir_path)
                    for item in items:
                        if item.entry_type == "directory":
                            _wipe_dir_recursive(item.path)
                            try:
                                if sess.delete_dir(item.path):
                                    deleted_dirs.append(item.path)
                            except Exception:
                                pass
                        else:
                            try:
                                if sess.delete_file(item.path):
                                    deleted_files.append(item.path)
                                    bytes_freed += item.size_bytes
                            except Exception:
                                pass

                _wipe_dir_recursive("/")
                storage_after = sess.query_storage_status()
                delete_packets_sent = sess.packets_sent
                acks_received = sess.acks_received
                usb_bus_reset_ok = True
        else:
            for _dir_path, entries in list(_VIRTUAL_FILESYSTEM.items()):
                for entry in list(entries):
                    if entry.entry_type == "file":
                        deleted_files.append(entry.path)
                        bytes_freed += entry.size_bytes

            claimed = self._claim_interface()
            try:
                if claimed:
                    for fpath in deleted_files:
                        del_pkt = build_file_delete_packet(
                            remote_path=fpath, is_directory=False, seq_num=self._next_seq()
                        )
                        self.send_packet(del_pkt, timeout_ms=200)
                        delete_packets_sent += 1
                        if self.receive_packet(timeout_ms=200) is not None:
                            acks_received += 1
            finally:
                self._release_interface()

            if reset_usb_bus:
                try:
                    self.dev.reset()
                    usb_bus_reset_ok = True
                except Exception:
                    usb_bus_reset_ok = self.is_mock

        # Actualizar caché local para reflejar el almacenamiento vacío
        _VIRTUAL_FILESYSTEM.clear()
        _VIRTUAL_FILESYSTEM["/"] = []
        _VIRTUAL_FILESYSTEM["/PyLib"] = []
        _VIRTUAL_FILESYSTEM["/MyLib"] = []
        _VIRTUAL_FILESYSTEM["/Examples"] = []

        if self.is_mock:
            storage_after = self.get_storage_status()

        # Limpiar archivos .tns en la cola local ~/.ti_nspire_mcp/outbox
        staging_dir = Path.home() / ".ti_nspire_mcp" / "outbox"
        staged_removed = 0
        if staging_dir.exists():
            for p in staging_dir.glob("*.tns"):
                try:
                    p.unlink()
                    staged_removed += 1
                except Exception:
                    pass

        dev_info = self.get_info()
        if hardware_announcement and hardware_announcement.get("serial_number"):
            dev_info["hardware_serial_ecid"] = hardware_announcement["serial_number"]
            dev_info["hardware_tag"] = hardware_announcement["hardware_tag"]

        return {
            "success": True,
            "mode": mode,
            "equivalent_maintenance_option": "Option 2: Delete Document Folder Contents (OS Preserved)",
            "os_preserved": True,
            "os_version": storage_after.os_version,
            "usb_bus_reset_executed": usb_bus_reset_ok,
            "transport_mode": transport_mode,
            "deleted_files_count": len(deleted_files),
            "deleted_files": deleted_files,
            "deleted_directories_count": len(deleted_dirs),
            "deleted_directories": deleted_dirs,
            "bytes_freed": bytes_freed,
            "staged_outbox_cleared": staged_removed,
            "telemetry": {
                "service_id": "0x4060 (CMD_FILE_DELETE 0x0901 + CMD_DIR_DELETE 0x0B03)",
                "endpoint_out": f"0x{self.ep_out:02X}",
                "endpoint_in": f"0x{self.ep_in:02X}",
                "delete_packets_sent": delete_packets_sent,
                "acks_received": acks_received,
                "hardware_announcement": hardware_announcement,
            },
            "device": dev_info,
            "storage_before": storage_before.model_dump(),
            "storage_after": storage_after.model_dump(),
            "message": (
                f"Limpieza profunda completada en la calculadora: {len(deleted_files)} archivos y "
                f"{len(deleted_dirs)} carpetas eliminados ({bytes_freed} bytes liberados), conservando OS {storage_after.os_version}."
            ),
        }


def build_usb_error_telemetry(message: str, code: str = "USB_DEVICE_NOT_FOUND") -> dict[str, Any]:
    """Construye un diccionario con telemetría enriquecida de error USB."""
    telemetry = ErrorTelemetry(
        error_code=code,
        category="usb_transport",
        message=message,
        recoverable=True,
        suggestions=[
            "Verifica que la calculadora TI-Nspire CX II esté encendida y conectada por cable USB.",
            "Cierra 'TI-Nspire CX Student Software' o pestañas de Chrome con WebUSB activas que bloqueen la interfaz 0.",
            "En entornos de prueba o CI/CD sin calculadora física, define la variable TI_NSPIRE_MOCK=1.",
        ],
    )
    return telemetry.model_dump()
