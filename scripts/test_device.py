#!/usr/bin/env python3
"""
Script de diagnóstico integral para verificar la conexión USB con la Texas Instruments,
el protocolo de paquetes NavNet/NNSE, la generación avanzada de archivos .tns y la captura
de pantalla RGB565.
"""

from ti_nspire_mcp.latex_converter import latex_to_nspire_xml
from ti_nspire_mcp.micropython_linter import validate_micropython_script
from ti_nspire_mcp.tns_builder import create_multipage_tns, create_python_tns, parse_tns_document
from ti_nspire_mcp.usb_driver import TIUSBDevice

print("==================================================")
print("  DIAGNÓSTICO USB NAVNET - TEXAS INSTRUMENTS")
print("==================================================")

devices = TIUSBDevice.find_all()
if not devices:
    print("ℹ️ No se detectó calculadora física por USB; activando modo Mock de diagnóstico.")
    devices = [TIUSBDevice.create_mock()]

for i, dev in enumerate(devices, 1):
    info = dev.get_info()
    print(f"✅ Dispositivo #{i}: {info['model']}")
    print(f"   - Vendor ID:  {info['vendor_id']}")
    print(f"   - Product ID: {info['product_id']}")
    print(f"   - Bus/Addr:   Bus {info['bus']}, Addr {info['address']}")
    print(f"   - Protocolo:  {info['protocol']}")
    print(f"   - Endpoints:  IN={info['endpoints']['bulk_in']}, OUT={info['endpoints']['bulk_out']}")

print("\n--------------------------------------------------")
print("  PRUEBA DE GENERACIÓN Y PARSEO DE .TNS AVANZADO")
print("--------------------------------------------------")
test_code = "import math\nfrom ti_draw import *\nclear()\ndraw_text(10, 20, 'Hola TI-Nspire CX II!')\n"
lint = validate_micropython_script(test_code)
print(f"✅ Linter MicroPython: válido={lint.valid}, RAM estimada={lint.estimated_ram_bytes} bytes")

tns_data = create_python_tns("prueba", test_code)
multi_data = create_multipage_tns(
    [
        {
            "page_type": "notes",
            "title": "Fórmula General",
            "content": "Solución de ecuación cuadrática: $x = \\frac{-b \\pm \\sqrt{b^{2}-4ac}}{2a}$",
            "formulas_latex": [r"\int_{0}^{\pi} \sin(x) dx = 2"],
        },
        {
            "page_type": "python",
            "title": "solver",
            "content": test_code,
        },
    ]
)
parsed = parse_tns_document(multi_data)
print(
    f"✅ Documento .tns básico: {len(tns_data)} bytes | Multipágina: {len(multi_data)} bytes ({parsed['page_count']} páginas)"
)
print(f"✅ Conversión LaTeX -> XML: {latex_to_nspire_xml(r'\frac{a}{b}')}")

print("\n--------------------------------------------------")
print("  PRUEBA DE CAPTURA RGB565 Y SISTEMA DE ARCHIVOS")
print("--------------------------------------------------")
active_dev = devices[0]
screen = active_dev.capture_screen_live()
fs_tree = active_dev.list_remote_filesystem("/")
print(
    f"✅ Captura de pantalla: {screen['width']}x{screen['height']} ({screen['pixel_format']}) -> PNG {screen['png_size_bytes']} bytes"
)
print(f"✅ Sistema de archivos remoto ('/'): {fs_tree['entry_count']} entradas detectadas.")
print("==================================================")
