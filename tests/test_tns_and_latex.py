"""Pruebas unitarias para el conversor LaTeX y el motor multipágina/multiaplicación .tns."""

from __future__ import annotations

from ti_nspire_mcp.latex_converter import latex_to_nspire_expr, latex_to_nspire_xml
from ti_nspire_mcp.tns_builder import (
    create_lua_tns,
    create_multipage_tns,
    create_ndless_tns,
    create_python_tns,
    parse_tns_document,
)


def test_latex_to_nspire_conversions() -> None:
    expr = latex_to_nspire_expr(r"$$\frac{-b \pm \sqrt{b^{2}-4ac}}{2a}$$")
    assert "((" in expr
    assert "±" in expr
    assert "√(" in expr
    assert "^(2)" in expr

    nth_root = latex_to_nspire_expr(r"\sqrt[3]{x^{2}+1}")
    assert "root(x^(2)+1,3)" in nth_root

    integral = latex_to_nspire_expr(r"\int_{0}^{\pi} \sin(x) dx")
    assert "∫(" in integral
    assert "π" in integral

    summation = latex_to_nspire_expr(r"\sum_{k=1}^{n} k^{2}")
    assert "∑(" in summation

    matrix = latex_to_nspire_expr(r"\begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}")
    assert matrix == "[1,2;3,4]"

    xml_tag = latex_to_nspire_xml(r"\alpha + \beta \leq \theta", evaluate=True)
    assert xml_tag.startswith('<expr eval="1"')
    assert "α + β ≤ θ" in xml_tag
    assert xml_tag.endswith("</expr>")


def test_multipage_tns_generation_and_parsing() -> None:
    pages = [
        {
            "page_type": "notes",
            "title": "Teoría de Circuitos",
            "content": "Ley de Ohm fasorial: $V = I \\cdot Z$",
            "formulas_latex": [r"Z = \sqrt{R^{2} + (X_{L} - X_{C})^{2}}"],
        },
        {
            "page_type": "spreadsheet",
            "title": "Mediciones",
            "spreadsheet_columns": {
                "A": [10, 20, 30],
                "B": ["=a1*2", "=a2*2", "=a3*2"],
            },
        },
        {
            "page_type": "python",
            "title": "simulador_rlc",
            "content": "import math\nprint('RLC Listo')\n",
        },
        {
            "page_type": "calculator",
            "title": "Evaluador CAS",
            "formulas_latex": [r"\frac{1}{2\pi f C}"],
        },
    ]

    tns_bytes = create_multipage_tns(pages, document_title="Ingenieria_Electrica")
    assert tns_bytes[:4] == b"PK\x03\x04"

    parsed = parse_tns_document(tns_bytes)
    assert parsed["document_title"] == "Ingenieria_Electrica"
    assert parsed["page_count"] == 4

    app_types = [p["app_type"] for p in parsed["pages"]]
    assert app_types == ["notes", "spreadsheet", "python", "calculator"]
    assert len(parsed["pages"][0]["math_expressions"]) >= 2
    assert parsed["pages"][1]["columns"]["A"] == ["10", "20", "30"]
    assert "RLC Listo" in parsed["pages"][2]["code"]


def test_lua_and_ndless_packaging() -> None:
    lua_tns = create_lua_tns("graficador_lua", "function on.paint(gc) gc:drawString('Lua', 10, 10) end")
    parsed_lua = parse_tns_document(lua_tns)
    assert parsed_lua["page_count"] == 1
    assert parsed_lua["pages"][0]["app_type"] == "lua"
    assert "drawString" in parsed_lua["pages"][0]["code"]

    ndless_tns = create_ndless_tns("doom_nspire", b"\x7fELF_ARM_BINARY")
    parsed_ndless = parse_tns_document(ndless_tns)
    assert parsed_ndless["page_count"] == 1
    assert parsed_ndless["pages"][0]["app_type"] == "ndless"
    assert any(f.endswith(".prg") for f in parsed_ndless["archive_files"])

    # También verificar que create_python_tns clásico sigue siendo parseable
    classic_py = create_python_tns("classic", "print(42)")
    parsed_classic = parse_tns_document(classic_py)
    assert parsed_classic["page_count"] == 1
    assert parsed_classic["pages"][0]["app_type"] == "python"
