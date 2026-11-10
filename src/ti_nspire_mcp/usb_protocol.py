"""
Implementación del protocolo de paquetes USB de bajo nivel de Texas Instruments TI-Nspire
(NavNet / DocLink 0x54FD y contenedor NNSE para TI-Nspire CX II).

Incluye:
- Construcción y validación de paquetes NavNet de 16 bytes de cabecera + payload.
- Contenedor NNSE para la serie TI-Nspire CX II (0x0451:0xE022 / 0xE023).
- Comandos del servicio de archivos (0x4060): subida directa (.tns) y listado de directorios.
- Comandos del servicio de captura de pantalla (0x4021 / 0x4024): decodificación RGB565 (16 bits)
  a PNG / Base64 usando Pillow y descompresión RLE.
"""

from __future__ import annotations

import base64
import io
import struct
from dataclasses import dataclass, field
from typing import Final

from PIL import Image, ImageDraw

# Constantes de protocolo NavNet (Hackspire / libnspire specification)
NAVNET_MAGIC: Final[int] = 0x54FD
NNSE_MAGIC: Final[bytes] = b"NNSE"

HOST_ADDR: Final[int] = 0x6400
DEVICE_ADDR: Final[int] = 0x6401

# Servicios de dispositivo TI-Nspire
SERVICE_NULL_ECHO: Final[int] = 0x4001
SERVICE_DEVICE_INFO: Final[int] = 0x4002
SERVICE_ADDRESS_ASSIGN: Final[int] = 0x4003
SERVICE_SCREENSHOT_RAW: Final[int] = 0x4021
SERVICE_SCREENSHOT_RLE: Final[int] = 0x4024
SERVICE_FILE_SYNC: Final[int] = 0x4060
SERVICE_HOST_ACK: Final[int] = 0x00FF
SERVICE_HOST_PORT: Final[int] = 0x8001

# Comandos del servicio 0x4060 (File Management / DocLink)
CMD_FILE_PUT_REQ: Final[int] = 0x03
CMD_FILE_PUT_OK: Final[int] = 0x04
CMD_FILE_DATA_CHUNK: Final[int] = 0x05
CMD_FILE_EOF: Final[int] = 0x06
CMD_FILE_GET_REQ: Final[int] = 0x07
CMD_FILE_DELETE: Final[int] = 0x09
CMD_DIR_DELETE: Final[int] = 0x0B
CMD_DIR_LIST_START: Final[int] = 0x0D
CMD_DIR_LIST_NEXT: Final[int] = 0x0E
CMD_DIR_LIST_END: Final[int] = 0x0F
CMD_STATUS_OK: Final[int] = 0xFF00

# Dimensiones nativas de pantalla TI-Nspire CX / CX II
SCREEN_WIDTH: Final[int] = 320
SCREEN_HEIGHT: Final[int] = 240
SCREEN_BPP: Final[int] = 16
SCREEN_FRAME_BYTES: Final[int] = SCREEN_WIDTH * SCREEN_HEIGHT * 2  # 153,600 bytes


def compute_data_checksum(data: bytes) -> int:
    """
    Calcula el checksum de 16 bits del payload de datos NavNet.
    Algoritmo estándar NavNet: acumulador de 16 bits sobre cada byte.
    """
    acc = 0
    for b in data:
        acc = (acc + b) & 0xFFFF
    return acc


def compute_header_checksum(header_15_bytes: bytes) -> int:
    """
    Calcula el checksum de 8 bits de los primeros 15 bytes de la cabecera NavNet.
    """
    if len(header_15_bytes) != 15:
        raise ValueError(f"La cabecera parcial debe tener exactamente 15 bytes, recibidos: {len(header_15_bytes)}")
    return sum(header_15_bytes) & 0xFF


@dataclass(frozen=True)
class NavNetPacket:
    """
    Paquete del protocolo NavNet de TI-Nspire.

    Estructura de cabecera (16 bytes, Big Endian):
      0..1:   Magic (0x54FD)
      2..3:   Source Address (uint16)
      4..5:   Source Service ID (uint16)
      6..7:   Destination Address (uint16)
      8..9:   Destination Service ID (uint16)
      10..11: Data Checksum (uint16)
      12:     Data Size (uint8, hasta 254 bytes por trama clásica)
      13:     ACK Flag (uint8, 0x00 = normal, 0x0A = ACK)
      14:     Sequence Number (uint8)
      15:     Header Checksum (uint8, suma de bytes 0..14 mod 256)
      16..N:  Payload (bytes)
    """

    src_addr: int = HOST_ADDR
    src_service: int = SERVICE_HOST_PORT
    dst_addr: int = DEVICE_ADDR
    dst_service: int = SERVICE_FILE_SYNC
    ack_flag: int = 0x00
    seq_num: int = 0x01
    payload: bytes = field(default=b"")

    def to_bytes(self) -> bytes:
        """Serializa el paquete NavNet (cabecera de 16 bytes + payload)."""
        size_field = min(len(self.payload), 0xFF)
        data_chk = compute_data_checksum(self.payload)
        header_15 = struct.pack(
            ">HHHHHHBBB",
            NAVNET_MAGIC,
            self.src_addr & 0xFFFF,
            self.src_service & 0xFFFF,
            self.dst_addr & 0xFFFF,
            self.dst_service & 0xFFFF,
            data_chk & 0xFFFF,
            size_field,
            self.ack_flag & 0xFF,
            self.seq_num & 0xFF,
        )
        hdr_chk = compute_header_checksum(header_15)
        return header_15 + bytes([hdr_chk]) + self.payload

    def to_nnse_bytes(self) -> bytes:
        """
        Envuelve el paquete NavNet en una trama NNSE para la familia TI-Nspire CX II (0xE022).
        Cabecera NNSE: b'NNSE' (4 bytes) + versión 0x0001 (2 bytes) + longitud payload (2 bytes).
        """
        raw = self.to_bytes()
        nnse_hdr = struct.pack(">4sHH", NNSE_MAGIC, 0x0001, len(raw))
        return nnse_hdr + raw

    @classmethod
    def from_bytes(cls, raw: bytes) -> NavNetPacket:
        """Deserializa y valida un paquete NavNet o NNSE desde bytes crudos."""
        if raw.startswith(NNSE_MAGIC):
            if len(raw) < 8:
                raise ValueError("Trama NNSE truncada (menos de 8 bytes).")
            _, _ver, inner_len = struct.unpack(">4sHH", raw[:8])
            raw = raw[8 : 8 + inner_len]

        if len(raw) < 16:
            raise ValueError(f"Paquete NavNet demasiado corto ({len(raw)} bytes, mínimo 16).")

        header_15 = raw[:15]
        expected_hdr_chk = raw[15]
        actual_hdr_chk = compute_header_checksum(header_15)
        if expected_hdr_chk != actual_hdr_chk:
            raise ValueError(
                f"Checksum de cabecera NavNet inválido: esperado 0x{expected_hdr_chk:02X}, "
                f"calculado 0x{actual_hdr_chk:02X}"
            )

        (
            magic,
            src_addr,
            src_service,
            dst_addr,
            dst_service,
            data_chk,
            size_field,
            ack_flag,
            seq_num,
        ) = struct.unpack(">HHHHHHBBB", header_15)

        if magic != NAVNET_MAGIC:
            raise ValueError(f"Magic NavNet inválido: 0x{magic:04X} (esperado 0x54FD).")

        payload = raw[16:]
        if size_field < 0xFF and len(payload) > size_field:
            payload = payload[:size_field]

        calc_data_chk = compute_data_checksum(payload)
        if data_chk != calc_data_chk:
            raise ValueError(
                f"Checksum de datos NavNet inválido: esperado 0x{data_chk:04X}, calculado 0x{calc_data_chk:04X}"
            )

        return cls(
            src_addr=src_addr,
            src_service=src_service,
            dst_addr=dst_addr,
            dst_service=dst_service,
            ack_flag=ack_flag,
            seq_num=seq_num,
            payload=payload,
        )


def build_address_assign_packet(seq_num: int = 1) -> NavNetPacket:
    """Construye el paquete inicial de asignación de dirección NavNet (0x4003)."""
    payload = struct.pack(">HH", DEVICE_ADDR, 0xFF00)
    return NavNetPacket(
        src_addr=0x0000,
        src_service=0x00FF,
        dst_addr=0x0000,
        dst_service=SERVICE_ADDRESS_ASSIGN,
        ack_flag=0x00,
        seq_num=seq_num,
        payload=payload,
    )


def build_ack_packet(received: NavNetPacket) -> NavNetPacket:
    """Construye un paquete ACK (0x0A) en respuesta a un paquete recibido."""
    return NavNetPacket(
        src_addr=HOST_ADDR,
        src_service=SERVICE_HOST_ACK,
        dst_addr=received.src_addr,
        dst_service=received.src_service,
        ack_flag=0x0A,
        seq_num=received.seq_num,
        payload=struct.pack(">H", received.dst_service),
    )


def build_file_put_request_packet(remote_path: str, file_size: int, seq_num: int = 2) -> NavNetPacket:
    """
    Construye el paquete NavNet de solicitud de subida de archivo (.tns) al servicio 0x4060.
    Formato del payload:
      0x03 (CMD_FILE_PUT_REQ) + 0x01 + path UTF-8 terminado en NUL + uint32 Big-Endian file_size.
    """
    clean_path = remote_path if remote_path.startswith("/") else f"/{remote_path}"
    path_bytes = clean_path.encode("utf-8") + b"\x00"
    payload = struct.pack(">BB", CMD_FILE_PUT_REQ, 0x01) + path_bytes + struct.pack(">I", file_size)
    return NavNetPacket(
        src_addr=HOST_ADDR,
        src_service=SERVICE_HOST_PORT,
        dst_addr=DEVICE_ADDR,
        dst_service=SERVICE_FILE_SYNC,
        seq_num=seq_num,
        payload=payload,
    )


def build_file_data_packets(file_bytes: bytes, start_seq: int = 3, chunk_size: int = 240) -> list[NavNetPacket]:
    """
    Fragmenta los bytes de un archivo .tns en paquetes de datos NavNet (CMD_FILE_DATA_CHUNK = 0x05).
    """
    packets: list[NavNetPacket] = []
    seq = start_seq
    for offset in range(0, len(file_bytes), chunk_size):
        chunk = file_bytes[offset : offset + chunk_size]
        payload = bytes([CMD_FILE_DATA_CHUNK]) + chunk
        packets.append(
            NavNetPacket(
                src_addr=HOST_ADDR,
                src_service=SERVICE_HOST_PORT,
                dst_addr=DEVICE_ADDR,
                dst_service=SERVICE_FILE_SYNC,
                seq_num=seq & 0xFF,
                payload=payload,
            )
        )
        seq = (seq + 1) & 0xFF or 1
    return packets


def build_dir_list_request_packet(remote_dir: str = "/", seq_num: int = 2) -> NavNetPacket:
    """Construye el paquete de solicitud de listado de directorio en el servicio 0x4060."""
    clean_dir = remote_dir if remote_dir.startswith("/") else f"/{remote_dir}"
    payload = bytes([CMD_DIR_LIST_START]) + clean_dir.encode("utf-8") + b"\x00"
    return NavNetPacket(
        src_addr=HOST_ADDR,
        src_service=SERVICE_HOST_PORT,
        dst_addr=DEVICE_ADDR,
        dst_service=SERVICE_FILE_SYNC,
        seq_num=seq_num,
        payload=payload,
    )


def build_file_delete_packet(remote_path: str, is_directory: bool = False, seq_num: int = 2) -> NavNetPacket:
    """
    Construye el paquete NavNet de eliminación de archivo o directorio en el servicio 0x4060
    (usado en la limpieza de documentos tipo Opción 2 'Delete Document Folder Contents').
    """
    clean_path = remote_path if remote_path.startswith("/") else f"/{remote_path}"
    cmd = CMD_DIR_DELETE if is_directory else CMD_FILE_DELETE
    payload = bytes([cmd, 0x01]) + clean_path.encode("utf-8") + b"\x00"
    return NavNetPacket(
        src_addr=HOST_ADDR,
        src_service=SERVICE_HOST_PORT,
        dst_addr=DEVICE_ADDR,
        dst_service=SERVICE_FILE_SYNC,
        seq_num=seq_num,
        payload=payload,
    )


def parse_cxii_announcement_packet(raw: bytes) -> dict[str, int | str] | None:
    """
    Parsea el paquete de anuncio de hardware de 77 bytes enviado por la TI-Nspire CX II
    en el endpoint Bulk IN (0x81) tras un reinicio de bus USB (cabecera 00 01 ff ff 00 00 00 4d).
    Extrae el número de serie / ECID de hardware real de 27 caracteres (ej. '1C50000000000F25F37EB12CD83').
    """
    if len(raw) < 32 or raw[:4] != b"\x00\x01\xff\xff":
        return None
    pkt_len = struct.unpack(">I", raw[4:8])[0]
    hw_tag = raw[8:12].hex().upper()
    ascii_segment = raw[16:]
    # Buscar cadena alfanumérica ASCII de serie/ECID
    serial_chars: list[str] = []
    for b in ascii_segment:
        if 0x30 <= b <= 0x39 or 0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A:
            serial_chars.append(chr(b))
        elif serial_chars:
            break
    serial_str = "".join(serial_chars)
    return {
        "packet_length": pkt_len,
        "hardware_tag": f"0x{hw_tag}",
        "serial_number": serial_str or "UNKNOWN",
    }


def build_screenshot_request_packet(seq_num: int = 2, use_rle: bool = True) -> NavNetPacket:
    """
    Construye el paquete de solicitud de captura de pantalla en tiempo real (0x4024 RLE o 0x4021 Raw).
    """
    service = SERVICE_SCREENSHOT_RLE if use_rle else SERVICE_SCREENSHOT_RAW
    # Comando 0x00 solicita volcado inmediato del framebuffer actual (320x240x16bpp)
    return NavNetPacket(
        src_addr=HOST_ADDR,
        src_service=SERVICE_HOST_PORT,
        dst_addr=DEVICE_ADDR,
        dst_service=service,
        seq_num=seq_num,
        payload=b"\x00",
    )


def parse_dir_entry_payload(payload: bytes, parent_dir: str = "/") -> dict[str, int | str] | None:
    """
    Parsea un payload de entrada de directorio devuelto por el servicio 0x4060.
    Formato soportado:
      byte 0: CMD_DIR_LIST_NEXT (0x0E)
      bytes 1..2: offset/padding
      bytes 3..N: nombre terminado en NUL
      seguido de uint32 size_bytes, uint32 timestamp, uint8 is_dir (1=directorio, 0=archivo)
    """
    if not payload or payload[0] != CMD_DIR_LIST_NEXT:
        return None
    body = payload[3:] if len(payload) > 3 else payload[1:]
    nul_idx = body.find(b"\x00")
    if nul_idx == -1:
        return None
    name = body[:nul_idx].decode("utf-8", errors="replace")
    rest = body[nul_idx + 1 :]
    size_bytes = 0
    timestamp = 0
    is_dir = 0
    if len(rest) >= 9:
        size_bytes, timestamp, is_dir = struct.unpack(">IIB", rest[:9])
    base = parent_dir.rstrip("/")
    full_path = f"{base}/{name}" if base else f"/{name}"
    return {
        "name": name,
        "path": full_path,
        "entry_type": "directory" if is_dir else "file",
        "size_bytes": size_bytes,
        "modified_timestamp": timestamp,
    }


def decompress_rle_rgb565(data: bytes, expected_bytes: int = SCREEN_FRAME_BYTES) -> bytes:
    """
    Descomprime un flujo RLE de 16 bits (pares de píxeles RGB565) de TI-Nspire CX / CX II.
    Formato RLE:
      - Si byte de control signed b >= 0: repite el siguiente par de bytes (b + 1) veces.
      - Si byte de control signed b < 0: copia literalmente (-b) pares de bytes siguientes.
    Si los datos ya tienen el tamaño completo sin comprimir, los devuelve directamente.
    """
    if len(data) == expected_bytes:
        return data

    out = bytearray()
    idx = 0
    n = len(data)
    while idx < n and len(out) < expected_bytes:
        ctrl = struct.unpack("b", data[idx : idx + 1])[0]
        idx += 1
        if ctrl >= 0:
            count = ctrl + 1
            if idx + 2 > n:
                break
            pixel = data[idx : idx + 2]
            idx += 2
            out.extend(pixel * count)
        else:
            count = -ctrl
            literal_len = count * 2
            out.extend(data[idx : idx + literal_len])
            idx += literal_len

    if len(out) < expected_bytes:
        out.extend(b"\x00" * (expected_bytes - len(out)))
    return bytes(out[:expected_bytes])


def rgb565_to_pil_image(
    rgb565_bytes: bytes,
    width: int = SCREEN_WIDTH,
    height: int = SCREEN_HEIGHT,
) -> Image.Image:
    """
    Convierte un buffer de píxeles RGB565 (16 bits por píxel, Little-Endian) de TI-Nspire
    en un objeto PIL.Image en modo RGB de 24 bits.
    """
    expected_len = width * height * 2
    if len(rgb565_bytes) < expected_len:
        rgb565_bytes = rgb565_bytes + b"\x00" * (expected_len - len(rgb565_bytes))

    rgb888 = bytearray(width * height * 3)
    out_idx = 0
    for i in range(0, expected_len, 2):
        val = rgb565_bytes[i] | (rgb565_bytes[i + 1] << 8)
        r5 = (val >> 11) & 0x1F
        g6 = (val >> 5) & 0x3F
        b5 = val & 0x1F
        rgb888[out_idx] = (r5 << 3) | (r5 >> 2)
        rgb888[out_idx + 1] = (g6 << 2) | (g6 >> 4)
        rgb888[out_idx + 2] = (b5 << 3) | (b5 >> 2)
        out_idx += 3

    return Image.frombytes("RGB", (width, height), bytes(rgb888))


def pil_image_to_rgb565(image: Image.Image) -> bytes:
    """
    Convierte una imagen PIL.Image a un buffer RGB565 (Little-Endian, 16 bits por píxel)
    de las dimensiones de la imagen.
    """
    rgb_img = image.convert("RGB")
    raw_rgb = rgb_img.tobytes()
    out = bytearray((len(raw_rgb) // 3) * 2)
    out_idx = 0
    for i in range(0, len(raw_rgb), 3):
        r = raw_rgb[i]
        g = raw_rgb[i + 1]
        b = raw_rgb[i + 2]
        val = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        out[out_idx] = val & 0xFF
        out[out_idx + 1] = (val >> 8) & 0xFF
        out_idx += 2
    return bytes(out)


def encode_image_to_png_bytes(image: Image.Image) -> bytes:
    """Codifica una imagen PIL como bytes PNG."""
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def encode_image_to_base64_png(image: Image.Image) -> str:
    """Codifica una imagen PIL como cadena Base64 PNG."""
    return base64.b64encode(encode_image_to_png_bytes(image)).decode("ascii")


def render_simulated_screen_rgb565(
    title: str = "TI-Nspire CX II",
    active_doc: str = "1.1  Python: main.py",
    lines: list[str] | None = None,
) -> bytes:
    """
    Genera un framebuffer RGB565 auténtico de 320x240 píxeles que representa
    la interfaz gráfica de la calculadora TI-Nspire CX II (útil en modo Mock/CI
    o cuando el servicio de pantalla del dispositivo está en reposo).
    """
    img = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), color=(245, 247, 250))
    draw = ImageDraw.Draw(img)

    # Barra superior oscura de estado TI-Nspire CX II
    draw.rectangle([(0, 0), (SCREEN_WIDTH, 22)], fill=(28, 37, 54))
    draw.text((8, 5), title, fill=(255, 255, 255))
    draw.text((235, 5), "RAD   98%", fill=(110, 231, 183))

    # Pestaña de documento activo
    draw.rectangle([(0, 23), (SCREEN_WIDTH, 44)], fill=(219, 228, 240))
    draw.rectangle([(6, 26), (175, 44)], fill=(255, 255, 255), outline=(160, 174, 192))
    draw.text((12, 30), active_doc, fill=(26, 32, 44))

    # Área de trabajo MicroPython / Shell
    content_lines = lines or [
        ">>> # TI-Nspire CX II MicroPython v1.9.4",
        ">>> import math, ti_system, ti_draw",
        ">>> print('Enlace MCP 2.0 Activo')",
        "Enlace MCP 2.0 Activo",
        ">>> ",
    ]
    y = 56
    for line in content_lines[:8]:
        color = (0, 102, 204) if line.startswith(">>>") else (20, 20, 20)
        draw.text((12, y), line, fill=color)
        y += 20

    # Barra inferior de estado
    draw.rectangle([(0, 220), (SCREEN_WIDTH, 240)], fill=(226, 232, 240))
    draw.text((10, 224), "NavNet USB: 0x0451:0xE022 | RGB565 320x240", fill=(74, 85, 104))

    return pil_image_to_rgb565(img)
