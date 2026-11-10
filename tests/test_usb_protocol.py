"""Pruebas unitarias para el protocolo USB NavNet/NNSE y el driver TIUSBDevice en modo Mock."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from PIL import Image

from ti_nspire_mcp.usb_driver import TIUSBDevice
from ti_nspire_mcp.usb_protocol import (
    CMD_DIR_LIST_NEXT,
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
    decompress_rle_rgb565,
    parse_cxii_announcement_packet,
    parse_dir_entry_payload,
    pil_image_to_rgb565,
    render_simulated_screen_rgb565,
    rgb565_to_pil_image,
)


def test_navnet_packet_serialization_and_validation() -> None:
    pkt = NavNetPacket(seq_num=7, payload=b"Hello NavNet")
    wire = pkt.to_bytes()
    assert len(wire) == 16 + len(b"Hello NavNet")
    assert wire[:2] == b"\x54\xfd"

    parsed = NavNetPacket.from_bytes(wire)
    assert parsed.seq_num == 7
    assert parsed.payload == b"Hello NavNet"
    assert parsed.src_addr == pkt.src_addr
    assert parsed.dst_service == pkt.dst_service


def test_nnse_packet_wrapping_for_cx_ii() -> None:
    pkt = build_address_assign_packet(seq_num=3)
    nnse_wire = pkt.to_nnse_bytes()
    assert nnse_wire.startswith(b"NNSE")

    parsed = NavNetPacket.from_bytes(nnse_wire)
    assert parsed.seq_num == 3
    assert parsed.payload == pkt.payload


def test_invalid_navnet_checksum_raises() -> None:
    pkt = NavNetPacket(seq_num=1, payload=b"test")
    corrupted = bytearray(pkt.to_bytes())
    corrupted[15] ^= 0xFF
    with pytest.raises(ValueError, match="Checksum de cabecera NavNet inválido"):
        NavNetPacket.from_bytes(bytes(corrupted))


def test_file_transfer_and_dir_packets() -> None:
    put_req = build_file_put_request_packet("/PyLib/demo.tns", 512, seq_num=2)
    assert b"/PyLib/demo.tns\x00" in put_req.payload

    chunks = build_file_data_packets(b"A" * 500, start_seq=3, chunk_size=240)
    assert len(chunks) == 3

    ack = build_ack_packet(put_req)
    assert ack.ack_flag == 0x0A

    dir_req = build_dir_list_request_packet("/PyLib", seq_num=5)
    assert b"/PyLib\x00" in dir_req.payload

    scr_req = build_screenshot_request_packet(seq_num=6, use_rle=True)
    assert scr_req.dst_service == 0x4024

    del_req = build_file_delete_packet("/PyLib/demo.tns", is_directory=False, seq_num=7)
    assert b"/PyLib/demo.tns\x00" in del_req.payload

    raw_ann = bytes.fromhex(
        "0001ffff0000004d040813b70000000002000000000000314335303030303030303030304632354633374542313243443833"
        "000000000000000000000000000000000000000000000000000011"
    )
    ann_info = parse_cxii_announcement_packet(raw_ann)
    assert ann_info is not None
    assert ann_info["serial_number"] == "1C50000000000F25F37EB12CD83"
    assert ann_info["packet_length"] == 77


def test_parse_dir_entry_payload() -> None:
    raw_entry = (
        bytes([CMD_DIR_LIST_NEXT, 0x00, 0x00]) + b"matrix_solver.tns\x00" + struct.pack(">IIB", 2048, 1731000000, 0)
    )
    parsed = parse_dir_entry_payload(raw_entry, parent_dir="/PyLib")
    assert parsed is not None
    assert parsed["name"] == "matrix_solver.tns"
    assert parsed["path"] == "/PyLib/matrix_solver.tns"
    assert parsed["size_bytes"] == 2048
    assert parsed["entry_type"] == "file"


def test_rgb565_and_rle_conversion() -> None:
    sim_buf = render_simulated_screen_rgb565(title="Test Screen")
    assert len(sim_buf) == SCREEN_FRAME_BYTES

    img = rgb565_to_pil_image(sim_buf)
    assert isinstance(img, Image.Image)
    assert img.size == (SCREEN_WIDTH, SCREEN_HEIGHT)

    roundtrip = pil_image_to_rgb565(img)
    assert len(roundtrip) == SCREEN_FRAME_BYTES

    # Probar RLE con repetición de píxeles
    rle_sample = bytes([10, 0x1F, 0x00, -2 & 0xFF, 0xFF, 0xFF, 0x00, 0xF8])
    decompressed = decompress_rle_rgb565(rle_sample, expected_bytes=64)
    assert len(decompressed) == 64


def test_mock_usb_device_operations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TI_NSPIRE_MOCK", "1")
    dev = TIUSBDevice.find_first()
    assert dev is not None
    assert dev.is_mock is True

    info = dev.get_info()
    assert info["vendor_id"] == "0x0451"
    assert info["product_id"] == "0xE022"

    upload_res = dev.direct_upload_tns("unit_test_doc.tns", b"PK\x03\x04dummy", remote_dir="/PyLib")
    assert upload_res["success"] is True
    assert upload_res["remote_path"] == "/PyLib/unit_test_doc.tns"

    fs_res = dev.list_remote_filesystem("/PyLib")
    assert fs_res["success"] is True
    names = [e["name"] for e in fs_res["entries"]]
    assert "unit_test_doc.tns" in names

    png_out = tmp_path / "live_screen.png"
    scr_res = dev.capture_screen_live(save_path=png_out)
    assert scr_res["success"] is True
    assert scr_res["width"] == 320
    assert scr_res["height"] == 240
    assert png_out.exists()
