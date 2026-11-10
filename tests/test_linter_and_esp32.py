"""Pruebas unitarias para el linter de MicroPython y el puente inalámbrico ESP32."""

from __future__ import annotations

from pathlib import Path

from ti_nspire_mcp.esp32_bridge import ESP32RemoteSession, generate_esp32_sketch
from ti_nspire_mcp.micropython_linter import validate_micropython_script
from ti_nspire_mcp.models import ESP32BridgeConfig


def test_micropython_linter_valid_code() -> None:
    code = """
import math
from ti_draw import *
import ti_system as ti

clear()
set_color(20, 120, 220)
fill_rect(0, 0, 320, 24)
draw_text(10, 5, "TI-Nspire OK")
"""
    report = validate_micropython_script(code)
    assert report.valid is True
    assert report.syntax_ok is True
    assert report.compatible_with_ti_nspire is True
    assert "math" in report.imported_modules
    assert "ti_draw" in report.imported_modules
    assert len(report.restricted_modules) == 0


def test_micropython_linter_detects_syntax_error() -> None:
    bad_code = "def broken(x, y)\n    return x + y\n"
    report = validate_micropython_script(bad_code)
    assert report.valid is False
    assert report.syntax_ok is False
    assert any(i.code == "MPY001_SYNTAX_ERROR" for i in report.issues)


def test_micropython_linter_detects_restricted_modules_and_bounds() -> None:
    code = """
import numpy as np
import matplotlib.pyplot as plt
from ti_draw import *

set_color(300, -5, 120)
draw_text(450, 290, "Fuera de pantalla")
"""
    report = validate_micropython_script(code)
    assert report.valid is False
    assert report.syntax_ok is True
    assert report.compatible_with_ti_nspire is False
    assert "numpy" in report.restricted_modules
    assert "matplotlib" in report.restricted_modules

    codes = {i.code for i in report.issues}
    assert "MPY002_RESTRICTED_MODULE" in codes
    assert "MPY003_COLOR_OUT_OF_RANGE" in codes
    assert "MPY004_SCREEN_BOUNDS_X" in codes
    assert "MPY004_SCREEN_BOUNDS_Y" in codes


def test_esp32_bridge_sketch_and_remote_session(tmp_path: Path) -> None:
    cfg = ESP32BridgeConfig(
        mode="iphone_remote_screen",
        ssid="Nspire-Hotspot",
        password="supersecretpassword",
        baud_rate=115200,
    )
    res = generate_esp32_sketch(config=cfg, output_dir=tmp_path)
    assert res["success"] is True
    assert "WebSocketsServer" in res["arduino_sketch_ino"]
    assert "Nspire-Hotspot" in res["arduino_sketch_ino"]
    assert len(res["written_files"]) == 3
    for fpath in res["written_files"]:
        assert Path(fpath).exists()

    session = ESP32RemoteSession(config=cfg)
    frame_sync = session.push_frame_to_iphone(image_base64="iVBORw0KGgoAAAANSUhEUg==", status_text="En vivo")
    assert frame_sync["success"] is True
    assert frame_sync["tx_queue_depth"] == 1

    cmd_sync = session.send_command("enter", {"source": "iphone_keypad"})
    assert cmd_sync["success"] is True
    assert cmd_sync["bridge_ack"]["status"] == "delivered"
