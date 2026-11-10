#!/usr/bin/env python3
"""
Script de diagnóstico rápido para verificar la conexión USB con la Texas Instruments
y probar la generación de archivos .tns.
"""

import sys
from ti_nspire_mcp.usb_driver import TIUSBDevice
from ti_nspire_mcp.tns_builder import create_python_tns

print("==================================================")
print("  DIAGNÓSTICO USB - TEXAS INSTRUMENTS")
print("==================================================")

devices = TIUSBDevice.find_all()
if not devices:
    print("❌ No se detectó ninguna calculadora TI por USB.")
    print("   Asegúrate de que el cable esté firme y la calculadora encendida.")
else:
    for i, dev in enumerate(devices, 1):
        info = dev.get_info()
        print(f"✅ Dispositivo #{i}: {info['model']}")
        print(f"   - Vendor ID:  {info['vendor_id']}")
        print(f"   - Product ID: {info['product_id']}")
        print(f"   - Bus/Addr:   Bus {info['bus']}, Addr {info['address']}")

print("\n--------------------------------------------------")
print("  PRUEBA DE GENERACIÓN DE ARCHIVO .TNS")
print("--------------------------------------------------")
test_code = "print('Hola desde la TI-Nspire CX II!')"
tns_data = create_python_tns("prueba", test_code)
print(f"✅ Documento .tns generado en memoria: {len(tns_data)} bytes.")
print("==================================================")
