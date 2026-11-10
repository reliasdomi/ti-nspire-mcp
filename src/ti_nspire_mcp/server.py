"""
Servidor MCP (Model Context Protocol) para Texas Instruments TI-Nspire CX II.
Permite a modelos de lenguaje (Claude, Cursor, Antigravity) interactuar con la calculadora.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional

from mcp.server.mcpserver import MCPServer
from ti_nspire_mcp.usb_driver import TIUSBDevice
from ti_nspire_mcp.tns_builder import create_python_tns

server = MCPServer(
    name="ti-nspire-mcp",
    version="0.1.0",
    description="Servidor MCP para gestión y enlace con calculadoras Texas Instruments",
)


@server.tool()
def get_calculator_status() -> Dict[str, Any]:
    """
    Verifica el estado de conexión USB de la calculadora Texas Instruments en el ordenador.
    Detecta modelos como TI-Nspire CX II, TI-Nspire CX, TI-84 Plus CE, etc.
    """
    dev = TIUSBDevice.find_first()
    if not dev:
        return {
            "connected": False,
            "message": "No se detectó ninguna calculadora Texas Instruments conectada por USB.",
            "troubleshooting": [
                "Verifica que el cable USB esté bien conectado a la calculadora y a la Mac.",
                "Enciende la calculadora pulsando la tecla [ON].",
                "Asegúrate de que el puerto USB no esté en uso exclusivo por otro programa.",
            ],
        }

    info = dev.get_info()
    return {
        "connected": True,
        "device": info,
        "message": f"Calculadora detectada: {info.get('model', 'Texas Instruments')}",
    }


@server.tool()
def create_python_tns_document(
    filename: str, python_code: str, output_directory: Optional[str] = None
) -> Dict[str, Any]:
    """
    Empaqueta código Python en un archivo de documento .tns listo para transferir
    y ejecutar directamente en la TI-Nspire CX II.

    :param filename: Nombre del archivo (ejemplo: 'solucionador.tns' o 'app')
    :param python_code: Código fuente en Python para la calculadora.
    :param output_directory: Carpeta de destino (por defecto la carpeta del usuario).
    """
    if not filename.endswith(".tns"):
        filename += ".tns"

    out_dir = Path(output_directory) if output_directory else Path.home() / "Desktop"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_path = out_dir / filename

    tns_bytes = create_python_tns(
        script_name=filename.replace(".tns", ""),
        python_code=python_code,
    )

    with open(target_path, "wb") as f:
        f.write(tns_bytes)

    return {
        "success": True,
        "file_path": str(target_path.resolve()),
        "size_bytes": len(tns_bytes),
        "message": f"Archivo '{filename}' generado correctamente. Puedes subirlo mediante TI-Nspire CX II Connect.",
    }


@server.tool()
def convert_script_to_tns(python_script_path: str) -> Dict[str, Any]:
    """
    Convierte un archivo .py existente en tu computadora a formato .tns de TI-Nspire.

    :param python_script_path: Ruta absoluta al archivo .py a convertir.
    """
    p = Path(python_script_path)
    if not p.exists() or not p.is_file():
        return {
            "success": False,
            "error": f"El archivo especificado no existe: {python_script_path}",
        }

    code = p.read_text(encoding="utf-8")
    target_name = p.stem + ".tns"
    target_path = p.parent / target_name

    tns_bytes = create_python_tns(script_name=p.stem, python_code=code)
    with open(target_path, "wb") as f:
        f.write(tns_bytes)

    return {
        "success": True,
        "tns_path": str(target_path.resolve()),
        "size_bytes": len(tns_bytes),
        "message": f"Convertido '{p.name}' a '{target_name}'.",
    }


@server.tool()
def get_device_specs(model: str = "TI-Nspire CX II") -> Dict[str, Any]:
    """
    Proporciona especificaciones técnicas completas, módulos de Python soportados
    y restricciones de hardware de la calculadora Texas Instruments.
    """
    return {
        "model": model,
        "screen": {
            "resolution": "320 x 240 píxeles",
            "colors": "16 bits (65,536 colores)",
            "dpi": "~125 DPI",
        },
        "hardware": {
            "processor": "ARM9 @ 396 MHz",
            "ram": "64 MB SDRAM",
            "storage_flash": "128 MB (aproximadamente 100 MB utilizables para usuario)",
            "battery": "3.7V 1200mAh Li-ion recargable por USB",
        },
        "python_environment": {
            "engine": "MicroPython v1.9+",
            "built_in_modules": [
                "math",
                "random",
                "time",
                "cmath",
                "ti_system (teclado, portapapeles y variables)",
                "ti_draw (gráficos 2D en pantalla 320x240)",
                "ti_plotlib (gráficas cartesianas)",
                "ti_hub (comunicación con periféricos/microcontroladores)",
                "ti_rover (robótica)",
                "ti_image (renderizado de imágenes básicas)",
            ],
            "limitations": [
                "Sin acceso directo a sockets de red nativos sin hardware externo.",
                "Memoria de ejecución de script limitada por el heap de MicroPython.",
            ],
        },
    }


@server.tool()
def generate_python_template(template_type: str = "math_solver") -> Dict[str, str]:
    """
    Genera plantillas de código listas y optimizadas para el entorno Python de la TI-Nspire CX II.

    :param template_type: 'math_solver', 'graphics', 'serial_receiver' o 'notes_viewer'
    """
    templates = {
        "graphics": """# Plantilla Gráfica para TI-Nspire CX II (320x240)
from ti_draw import *
import time

clear()
set_color(30, 144, 255)
fill_rect(0, 0, 320, 30)

set_color(255, 255, 255)
draw_text(10, 8, "Texas Instruments - Gráficos")

set_color(220, 20, 60)
fill_circle(160, 130, 40)

set_color(0, 0, 0)
draw_text(90, 200, "Presiona [esc] para salir")
""",
        "math_solver": """# Plantilla Solucionador Interactivo TI-Nspire
import math
import ti_system as ti

def resolver_cuadratica(a, b, c):
    d = b**2 - 4*a*c
    if d > 0:
        x1 = (-b + math.sqrt(d)) / (2*a)
        x2 = (-b - math.sqrt(d)) / (2*a)
        return f"Dos soluciones: x1={x1}, x2={x2}"
    elif d == 0:
        return f"Una solucion doble: x={-b/(2*a)}"
    else:
        return "Sin soluciones reales (complejas)"

print("=== Solucionador Cuadrático ===")
a = float(input("Valor de a: "))
b = float(input("Valor de b: "))
c = float(input("Valor de c: "))
print(resolver_cuadratica(a, b, c))
""",
        "notes_viewer": """# Visor de Apuntes y Fórmulas
from ti_draw import *

apuntes = [
    "1. Ley de Ohm: V = I * R",
    "2. Potencia: P = V * I = I^2 * R",
    "3. Energia cinetica: Ec = 1/2 * m * v^2",
    "4. Gravedad: g = 9.81 m/s^2",
]

clear()
set_color(0, 0, 128)
fill_rect(0, 0, 320, 25)
set_color(255, 255, 255)
draw_text(10, 5, "Visor de Notas Rapidas")

set_color(0, 0, 0)
y = 45
for nota in apuntes:
    draw_text(10, y, nota)
    y += 25
""",
        "serial_receiver": """# Receptor de Datos desde ESP32 / Hub
import ti_hub as hub
import time

print("Esperando datos desde ESP32...")
while True:
    # Lee datos recibidos a traves del puerto de enlace
    try:
        msg = hub.read()
        if msg:
            print("Recibido:", msg)
    except Exception:
        pass
    time.sleep(0.1)
""",
    }

    code = templates.get(template_type, templates["math_solver"])
    return {
        "template_type": template_type,
        "code": code,
        "description": f"Plantilla generada para '{template_type}'.",
    }


def main():
    """Punto de entrada principal para ejecutar el servidor MCP sobre stdio."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
