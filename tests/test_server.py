"""Pruebas integrales para el servidor MCP 2.0 (Herramientas, Recursos y Prompts) de ti-nspire-mcp."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ti_nspire_mcp.server import (
    capture_screen_live,
    convert_latex_to_nspire_xml,
    convert_script_to_tns,
    create_lua_or_ndless_tns,
    create_multipage_tns_document,
    create_python_tns_document,
    direct_upload_tns,
    generate_esp32_firmware,
    generate_python_template,
    get_calculator_status,
    get_device_specs,
    inspect_tns_document,
    list_remote_filesystem,
    prompt_crear_solucionador_paso_a_paso,
    prompt_diagnosticar_bootloop,
    prompt_generar_chuleta_formulas_tns,
    resource_calculator_screenshot,
    resource_calculator_storage,
    sync_esp32_remote_screen,
    validate_micropython_code,
)
from ti_nspire_mcp.tns_builder import create_python_tns


def test_tns_builder() -> None:
    data = create_python_tns("test_script", "print('hello')")
    assert len(data) > 0
    assert data[:4] == b"PK\x03\x04"


def test_get_device_specs() -> None:
    specs = get_device_specs("TI-Nspire CX II")
    assert specs["screen"]["resolution"] == "320 x 240 píxeles"
    assert "ti_draw" in str(specs["python_environment"]["built_in_modules"])


def test_generate_python_template() -> None:
    tpl = generate_python_template("math_solver")
    assert "resolver_cuadratica" in tpl["code"]


def test_mcp_resources_in_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TI_NSPIRE_MOCK", "1")

    screenshot_json = resource_calculator_screenshot()
    screenshot_data = json.loads(screenshot_json)
    assert screenshot_data["success"] is True
    assert screenshot_data["width"] == 320
    assert screenshot_data["height"] == 240
    assert screenshot_data["image_base64"].startswith("iVBOR")

    storage_json = resource_calculator_storage()
    storage_data = json.loads(storage_json)
    assert storage_data["is_mock"] is True
    assert storage_data["storage"]["total_flash_bytes"] == 104_857_600
    assert storage_data["root_directory"]["success"] is True


def test_mcp_usb_and_tns_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TI_NSPIRE_MOCK", "1")

    status = get_calculator_status()
    assert status["connected"] is True
    assert status["device"]["vendor_id"] == "0x0451"

    py_doc = create_python_tns_document(
        filename="demo_app.tns",
        python_code="import math\nprint(math.pi)\n",
        output_directory=str(tmp_path),
    )
    assert py_doc["success"] is True
    assert Path(py_doc["file_path"]).exists()

    py_file = tmp_path / "script_in.py"
    py_file.write_text("print('convertido')", encoding="utf-8")
    conv = convert_script_to_tns(str(py_file))
    assert conv["success"] is True
    assert Path(conv["tns_path"]).exists()

    upload = direct_upload_tns(
        filename="subido_directo.tns",
        python_code="print('USB Bulk Directo')",
        remote_directory="/PyLib",
    )
    assert upload["success"] is True
    assert upload["remote_path"] == "/PyLib/subido_directo.tns"

    fs_list = list_remote_filesystem("/PyLib")
    assert fs_list["success"] is True
    assert any(e["name"] == "subido_directo.tns" for e in fs_list["entries"])

    live_scr = capture_screen_live(save_path=str(tmp_path / "cap.png"))
    assert live_scr["success"] is True
    assert (tmp_path / "cap.png").exists()


def test_advanced_tns_latex_lua_ndless_and_inspector_tools(tmp_path: Path) -> None:
    latex_res = convert_latex_to_nspire_xml(r"\frac{x^{2}+1}{\sqrt{y}}", evaluate=True)
    assert latex_res["success"] is True
    assert "<expr" in latex_res["nspire_xml_tag"]

    multi_res = create_multipage_tns_document(
        filename="multi_test.tns",
        pages=[
            {
                "page_type": "notes",
                "title": "Notas",
                "content": "Fórmula: $a^{2}+b^{2}=c^{2}$",
                "formulas_latex": [r"\sum_{i=1}^{n} i"],
            },
            {
                "page_type": "python",
                "title": "script",
                "content": "import math\nprint(math.sqrt(2))\n",
            },
        ],
        document_title="Proyecto Completo",
        output_directory=str(tmp_path),
    )
    assert multi_res["success"] is True
    assert multi_res["page_count"] == 2

    inspected = inspect_tns_document(multi_res["file_path"])
    assert inspected["success"] is True
    assert inspected["page_count"] == 2

    lua_res = create_lua_or_ndless_tns(
        filename="app_lua.tns",
        mode="lua",
        output_directory=str(tmp_path),
    )
    assert lua_res["success"] is True

    ndless_res = create_lua_or_ndless_tns(
        filename="app_ndless.tns",
        mode="ndless",
        output_directory=str(tmp_path),
    )
    assert ndless_res["success"] is True


def test_linter_esp32_and_prompts_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TI_NSPIRE_MOCK", "1")

    lint_ok = validate_micropython_code("import math\nprint(math.cos(0))\n")
    assert lint_ok["valid"] is True

    lint_bad = validate_micropython_code("import numpy as np\n")
    assert lint_bad["valid"] is False
    assert "numpy" in lint_bad["restricted_modules"]

    esp_fw = generate_esp32_firmware(
        mode="iphone_remote_screen",
        ssid="TI-Hotspot",
        password="password123",
        output_directory=str(tmp_path),
    )
    assert esp_fw["success"] is True
    assert len(esp_fw["written_files"]) == 3

    sync_res = sync_esp32_remote_screen(status_text="Conectado desde pytest", command="enter")
    assert sync_res["success"] is True
    assert sync_res["command_sync"]["bridge_ack"]["status"] == "delivered"

    p1 = prompt_crear_solucionador_paso_a_paso("Cálculo Integral", "Universitario")
    p2 = prompt_generar_chuleta_formulas_tns("Termodinámica", "si")
    p3 = prompt_diagnosticar_bootloop("TI-Nspire CX II", "Bootloop en logo")
    assert len(p1) == 1 and "direct_upload_tns" in p1[0]["content"]
    assert len(p2) == 1 and "convert_latex_to_nspire_xml" in p2[0]["content"]
    assert len(p3) == 1 and "list_remote_filesystem" in p3[0]["content"]
