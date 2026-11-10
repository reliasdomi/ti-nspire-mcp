"""
Motor avanzado de generación y parseo de documentos .tns (TI-Nspire Document).
Soporta:
- Documentos multipágina y multiaplicación (Python, Notas Matemáticas, Hojas de Cálculo, Calculadora CAS).
- Conversión automática de expresiones LaTeX a etiquetas matemáticas nativas <expr> de TI-Nspire.
- Empaquetado de scripts en Lua (TI-Nspire Script App) y ejecutables nativos de Ndless (PRG/Zehn).
- Parseo e inspección estructural de archivos .tns existentes.
"""

from __future__ import annotations

import binascii
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from ti_nspire_mcp.latex_converter import latex_to_nspire_xml
from ti_nspire_mcp.models import TNSPageSpec

NDLESS_MAGIC = b"PRG\x00"

_INLINE_LATEX_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$")


def _convert_inline_latex_in_text(text: str) -> str:
    """
    Detecta bloques $...$ o $$...$$ dentro de un párrafo de notas y los convierte
    a etiquetas <expr> nativas de TI-Nspire, escapando el texto circundante.
    """
    parts: list[str] = []
    last_end = 0
    for match in _INLINE_LATEX_RE.finditer(text):
        start, end = match.span()
        if start > last_end:
            parts.append(escape(text[last_end:start]))
        latex_str = match.group(1) if match.group(1) is not None else match.group(2)
        parts.append(latex_to_nspire_xml(latex_str or "", evaluate=False))
        last_end = end
    if last_end < len(text):
        parts.append(escape(text[last_end:]))
    return "".join(parts)


def _render_card_xml(spec: TNSPageSpec, card_index: int) -> str:
    """Renderiza una tarjeta (<card>) XML para un problema de TI-Nspire según su tipo de aplicación."""
    safe_title = escape(spec.title)

    if spec.page_type == "python":
        return f"""    <card id="{card_index}">
      <is-hidden>0</is-hidden>
      <py-app version="1.0">
        <script-name>{safe_title}</script-name>
        <script-code><![CDATA[{spec.content}]]></script-code>
      </py-app>
    </card>"""

    if spec.page_type == "notes":
        paragraphs_xml: list[str] = []
        for raw_line in spec.content.splitlines() if spec.content else [spec.title]:
            if raw_line.strip():
                paragraphs_xml.append(f"        <p>{_convert_inline_latex_in_text(raw_line)}</p>")
        for latex_formula in spec.formulas_latex:
            expr_tag = latex_to_nspire_xml(latex_formula, evaluate=True)
            paragraphs_xml.append(f"        <p>{expr_tag}</p>")
        joined_paragraphs = "\n".join(paragraphs_xml)
        return f"""    <card id="{card_index}">
      <is-hidden>0</is-hidden>
      <notes-app version="1.0">
        <title>{safe_title}</title>
        <content>
{joined_paragraphs}
        </content>
      </notes-app>
    </card>"""

    if spec.page_type == "spreadsheet":
        cols_xml: list[str] = []
        for col_name, values in spec.spreadsheet_columns.items():
            safe_col = escape(str(col_name))
            cells_xml: list[str] = []
            for row_idx, val in enumerate(values, start=1):
                val_str = str(val)
                is_formula = "1" if val_str.startswith("=") else "0"
                cells_xml.append(
                    f'          <cell col="{safe_col}" row="{row_idx}" formula="{is_formula}">{escape(val_str)}</cell>'
                )
            joined_cells = "\n".join(cells_xml)
            cols_xml.append(f'        <column name="{safe_col}">\n{joined_cells}\n        </column>')
        joined_cols = "\n".join(cols_xml)
        return f"""    <card id="{card_index}">
      <is-hidden>0</is-hidden>
      <lists-and-spreadsheet-app version="1.0">
        <title>{safe_title}</title>
{joined_cols}
      </lists-and-spreadsheet-app>
    </card>"""

    if spec.page_type == "lua":
        return f"""    <card id="{card_index}">
      <is-hidden>0</is-hidden>
      <script-app version="1.0" type="lua" api-level="2.5">
        <script-name>{safe_title}</script-name>
        <script-code><![CDATA[{spec.content}]]></script-code>
      </script-app>
    </card>"""

    if spec.page_type == "ndless":
        raw_payload = spec.ndless_hex_payload or binascii.hexlify(NDLESS_MAGIC + spec.content.encode("utf-8")).decode(
            "ascii"
        )
        return f"""    <card id="{card_index}">
      <is-hidden>0</is-hidden>
      <ndless-app version="4.5">
        <binary-name>{safe_title}</binary-name>
        <magic>PRG</magic>
        <payload-hex>{escape(raw_payload)}</payload-hex>
      </ndless-app>
    </card>"""

    # calculator app
    exprs_xml: list[str] = []
    for formula in spec.formulas_latex:
        exprs_xml.append(f"        {latex_to_nspire_xml(formula, evaluate=True)}")
    if spec.content.strip():
        exprs_xml.append(f"        {latex_to_nspire_xml(spec.content.strip(), evaluate=True)}")
    joined_exprs = "\n".join(exprs_xml)
    return f"""    <card id="{card_index}">
      <is-hidden>0</is-hidden>
      <calculator-app version="1.0">
        <title>{safe_title}</title>
{joined_exprs}
      </calculator-app>
    </card>"""


def create_python_tns(script_name: str, python_code: str) -> bytes:
    """
    Crea un documento .tns de TI-Nspire que contiene un script ejecutable de Python.
    Un archivo .tns es internamente un archivo ZIP con archivos XML descriptores.
    """
    buffer = io.BytesIO()

    doc_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<prob xmlns="urn:TI.Problem" version="1.0">
  <sym-import>0</sym-import>
  <card>
    <is-hidden>0</is-hidden>
    <py-app version="1.0">
      <script-name>{escape(script_name)}</script-name>
      <script-code><![CDATA[{python_code}]]></script-code>
    </py-app>
  </card>
</prob>
"""

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Problem1.xml", doc_xml.encode("utf-8"))

    return buffer.getvalue()


def create_multipage_tns(
    pages: Sequence[TNSPageSpec | dict[str, Any]],
    document_title: str = "TI-Nspire MultiApp Document",
) -> bytes:
    """
    Genera un documento .tns multipágina y multiaplicación (Python, Notas Matemáticas con LaTeX,
    Hojas de Cálculo, Lua y Ndless).
    """
    validated_pages: list[TNSPageSpec] = [
        p if isinstance(p, TNSPageSpec) else TNSPageSpec.model_validate(p) for p in pages
    ]
    if not validated_pages:
        raise ValueError("Se requiere al menos una página para generar un documento .tns.")

    cards_xml = "\n".join(_render_card_xml(spec, idx) for idx, spec in enumerate(validated_pages, start=1))

    problem_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<prob xmlns="urn:TI.Problem" version="1.0">
  <sym-import>0</sym-import>
{cards_xml}
</prob>
"""

    document_meta_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<document xmlns="urn:TI.Document" version="5.4">
  <title>{escape(document_title)}</title>
  <page-count>{len(validated_pages)}</page-count>
  <problems>
    <problem file="Problem1.xml" cards="{len(validated_pages)}" />
  </problems>
</document>
"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Document.xml", document_meta_xml.encode("utf-8"))
        z.writestr("Problem1.xml", problem_xml.encode("utf-8"))
        # Si incluye páginas Ndless, adjuntamos también el binario PRG nativo dentro del contenedor
        for idx, spec in enumerate(validated_pages, start=1):
            if spec.page_type == "ndless":
                raw_bin = (
                    binascii.unhexlify(spec.ndless_hex_payload)
                    if spec.ndless_hex_payload
                    else NDLESS_MAGIC + spec.content.encode("utf-8")
                )
                if not raw_bin.startswith(NDLESS_MAGIC):
                    raw_bin = NDLESS_MAGIC + raw_bin
                z.writestr(f"ndless_{idx}_{spec.title}.prg", raw_bin)

    return buffer.getvalue()


def create_lua_tns(script_name: str, lua_code: str) -> bytes:
    """Empaqueta un script en lenguaje Lua para el motor Lua nativo de TI-Nspire (OS 3.0 - 5.4)."""
    spec = TNSPageSpec(page_type="lua", title=script_name, content=lua_code)
    return create_multipage_tns([spec], document_title=script_name)


def create_ndless_tns(app_name: str, payload: bytes | str) -> bytes:
    """
    Empaqueta una aplicación nativa de Ndless (cabecera PRG\\x00 + binario ARM/Zehn) en un contenedor .tns.
    """
    raw_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not raw_bytes.startswith(NDLESS_MAGIC):
        raw_bytes = NDLESS_MAGIC + raw_bytes
    hex_str = binascii.hexlify(raw_bytes).decode("ascii")
    spec = TNSPageSpec(
        page_type="ndless",
        title=app_name,
        content=f"Ndless binary ({len(raw_bytes)} bytes)",
        ndless_hex_payload=hex_str,
    )
    return create_multipage_tns([spec], document_title=app_name)


def parse_tns_document(source: bytes | str | Path) -> dict[str, Any]:
    """
    Analiza e inspecciona un archivo .tns (desde ruta en disco o bytes en memoria),
    extrayendo sus páginas, aplicaciones (Python, Notas, Hoja de Cálculo, Lua, Ndless)
    y expresiones matemáticas <expr>.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        raw_bytes = path.read_bytes()
    else:
        raw_bytes = source

    # Caso especial: ejecutable binario puro de Ndless con cabecera PRG\x00
    if raw_bytes.startswith(NDLESS_MAGIC):
        return {
            "format": "ndless_raw_prg",
            "size_bytes": len(raw_bytes),
            "page_count": 1,
            "pages": [
                {
                    "page_number": 1,
                    "app_type": "ndless",
                    "magic": "PRG\\x00",
                    "payload_size": len(raw_bytes) - 4,
                }
            ],
        }

    buf = io.BytesIO(raw_bytes)
    if not zipfile.is_zipfile(buf):
        raise ValueError("El archivo .tns no es un contenedor ZIP válido ni un binario PRG de Ndless.")

    buf.seek(0)
    pages_info: list[dict[str, Any]] = []
    archive_files: list[str] = []
    doc_title = "Problem1"

    with zipfile.ZipFile(buf, "r") as z:
        archive_files = z.namelist()
        if "Document.xml" in archive_files:
            try:
                doc_root = ET.fromstring(z.read("Document.xml").decode("utf-8"))
                for elem in doc_root.iter():
                    if elem.tag.endswith("title") and elem.text:
                        doc_title = elem.text
                        break
            except Exception:
                pass

        problem_files = sorted(f for f in archive_files if f.startswith("Problem") and f.endswith(".xml"))
        for prob_file in problem_files:
            xml_text = z.read(prob_file).decode("utf-8")
            root = ET.fromstring(xml_text)

            for card in root.iter():
                if not card.tag.endswith("card"):
                    continue
                for child in card:
                    tag_local = child.tag.split("}")[-1]
                    if tag_local == "py-app":
                        script_name = ""
                        script_code = ""
                        for sub in child:
                            sub_tag = sub.tag.split("}")[-1]
                            if sub_tag == "script-name" and sub.text:
                                script_name = sub.text
                            elif sub_tag == "script-code" and sub.text:
                                script_code = sub.text
                        pages_info.append(
                            {
                                "page_number": len(pages_info) + 1,
                                "app_type": "python",
                                "title": script_name,
                                "code": script_code,
                            }
                        )
                    elif tag_local == "notes-app":
                        title = ""
                        expressions: list[str] = []
                        paragraphs: list[str] = []
                        for sub in child.iter():
                            sub_tag = sub.tag.split("}")[-1]
                            if sub_tag == "title" and sub.text:
                                title = sub.text
                            elif sub_tag == "expr" and sub.text:
                                expressions.append(sub.text)
                            elif sub_tag == "p":
                                p_text = "".join(sub.itertext()).strip()
                                if p_text:
                                    paragraphs.append(p_text)
                        pages_info.append(
                            {
                                "page_number": len(pages_info) + 1,
                                "app_type": "notes",
                                "title": title,
                                "paragraphs": paragraphs,
                                "math_expressions": expressions,
                            }
                        )
                    elif tag_local == "lists-and-spreadsheet-app":
                        title = ""
                        columns: dict[str, list[str]] = {}
                        for sub in child.iter():
                            sub_tag = sub.tag.split("}")[-1]
                            if sub_tag == "title" and sub.text:
                                title = sub.text
                            elif sub_tag == "cell":
                                col = sub.attrib.get("col", "A")
                                columns.setdefault(col, []).append(sub.text or "")
                        pages_info.append(
                            {
                                "page_number": len(pages_info) + 1,
                                "app_type": "spreadsheet",
                                "title": title,
                                "columns": columns,
                            }
                        )
                    elif tag_local == "script-app":
                        script_name = ""
                        script_code = ""
                        for sub in child:
                            sub_tag = sub.tag.split("}")[-1]
                            if sub_tag == "script-name" and sub.text:
                                script_name = sub.text
                            elif sub_tag == "script-code" and sub.text:
                                script_code = sub.text
                        pages_info.append(
                            {
                                "page_number": len(pages_info) + 1,
                                "app_type": child.attrib.get("type", "lua"),
                                "title": script_name,
                                "code": script_code,
                            }
                        )
                    elif tag_local == "ndless-app":
                        bin_name = ""
                        payload_hex = ""
                        for sub in child:
                            sub_tag = sub.tag.split("}")[-1]
                            if sub_tag == "binary-name" and sub.text:
                                bin_name = sub.text
                            elif sub_tag == "payload-hex" and sub.text:
                                payload_hex = sub.text
                        pages_info.append(
                            {
                                "page_number": len(pages_info) + 1,
                                "app_type": "ndless",
                                "title": bin_name,
                                "payload_bytes": len(payload_hex) // 2,
                            }
                        )
                    elif tag_local == "calculator-app":
                        title = ""
                        expressions = []
                        for sub in child.iter():
                            sub_tag = sub.tag.split("}")[-1]
                            if sub_tag == "title" and sub.text:
                                title = sub.text
                            elif sub_tag == "expr" and sub.text:
                                expressions.append(sub.text)
                        pages_info.append(
                            {
                                "page_number": len(pages_info) + 1,
                                "app_type": "calculator",
                                "title": title,
                                "math_expressions": expressions,
                            }
                        )

    return {
        "format": "tns_zip_xml",
        "document_title": doc_title,
        "size_bytes": len(raw_bytes),
        "archive_files": archive_files,
        "page_count": len(pages_info),
        "pages": pages_info,
    }
