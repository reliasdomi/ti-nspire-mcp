"""Pruebas unitarias para ti-nspire-mcp."""

import pytest
from ti_nspire_mcp.tns_builder import create_python_tns
from ti_nspire_mcp.server import get_device_specs, generate_python_template


def test_tns_builder():
    data = create_python_tns("test_script", "print('hello')")
    assert len(data) > 0
    # Los primeros 4 bytes de un archivo ZIP deben ser PK\x03\x04
    assert data[:4] == b"PK\x03\x04"


def test_get_device_specs():
    specs = get_device_specs("TI-Nspire CX II")
    assert specs["screen"]["resolution"] == "320 x 240 píxeles"
    assert "ti_draw" in str(specs["python_environment"]["built_in_modules"])


def test_generate_python_template():
    tpl = generate_python_template("math_solver")
    assert "resolver_cuadratica" in tpl["code"]
