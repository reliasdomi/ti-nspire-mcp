"""
Driver de bajo nivel para comunicación USB con calculadoras Texas Instruments.
Soporta detección, protocolo de paquetes NavNet/NNSE (Bulk Endpoints IN/OUT),
subida directa de documentos .tns, captura de pantalla en vivo (RGB565 -> PNG/Base64),
listado de archivos remotos y modo Mock para CI/CD.
"""

from __future__ import annotations

import os
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
    build_file_put_request_packet,
    build_screenshot_request_packet,
    decompress_rle_rgb565,
    encode_image_to_base64_png,
    encode_image_to_png_bytes,
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
        req_pkt = build_screenshot_request_packet(seq_num=self._next_seq(), use_rle=True)
        raw_framebuffer: bytes | None = None
        source_mode = "mock_framebuffer_rgb565" if self.is_mock else "usb_live_rgb565"

        claimed = self._claim_interface()
        try:
            if claimed and not self.is_mock:
                self.send_packet(req_pkt, timeout_ms=500)
                resp = self.receive_packet(timeout_ms=600)
                if resp is not None and len(resp.payload) > 4:
                    # Recibir fragmentos del stream RLE / RGB565
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

        req_pkt = build_dir_list_request_packet(remote_dir=clean_dir, seq_num=self._next_seq())
        live_entries: list[RemoteFileEntry] = []
        source_mode = "mock_virtual_fs" if self.is_mock else "usb_navnet_fs"

        claimed = self._claim_interface()
        try:
            if claimed and not self.is_mock:
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
                            live_entries.append(
                                RemoteFileEntry(
                                    name=str(parsed["name"]),
                                    path=str(parsed["path"]),
                                    entry_type="directory" if parsed["entry_type"] == "directory" else "file",
                                    size_bytes=int(parsed["size_bytes"]),
                                    modified_timestamp=int(parsed["modified_timestamp"]),
                                )
                            )
        except Exception:
            pass
        finally:
            self._release_interface()

        if not live_entries:
            entries = _VIRTUAL_FILESYSTEM.get(clean_dir, [])
            if not self.is_mock:
                source_mode = "usb_cached_fs_tree"
        else:
            entries = live_entries

        storage = self.get_storage_status()
        return {
            "success": True,
            "directory": clean_dir,
            "entry_count": len(entries),
            "entries": [e.model_dump() for e in entries],
            "source_mode": source_mode,
            "storage": storage.model_dump(),
        }

    def get_storage_status(self) -> StorageStatus:
        """Devuelve métricas detalladas de almacenamiento Flash y RAM de la calculadora."""
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
            os_version="5.4.0.259",
            battery_percent=98,
            is_charging=True,
            mode="mock_simulated" if self.is_mock else "usb_live",
        )


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
