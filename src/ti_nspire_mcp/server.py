"""
Servidor MCP 2.0 (Model Context Protocol) para Texas Instruments TI-Nspire CX II.
Integra:
- Comunicación USB nativa a bajo nivel (Protocolo NavNet / DocLink / NNSE).
- Motor avanzado de generación y parseo de documentos .tns (Multipágina, LaTeX <expr>, Lua, Ndless).
- Recursos dinámicos MCP (ti://calculator/screenshot y ti://calculator/storage).
- Linter y validador estático de MicroPython.
- Prompts guiados para flujos de ingeniería, formularios matemáticos y diagnóstico de hardware.
- Puente bidireccional ESP32 (Wi-Fi Hotspot / WebSockets / Pantalla remota desde iPhone).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from ti_nspire_mcp.esp32_bridge import (
    default_esp32_session,
    generate_esp32_sketch,
)
from ti_nspire_mcp.latex_converter import latex_to_nspire_expr, latex_to_nspire_xml
from ti_nspire_mcp.micropython_linter import validate_micropython_script
from ti_nspire_mcp.models import (
    ErrorTelemetry,
    ESP32BridgeConfig,
    TNSPageSpec,
)
from ti_nspire_mcp.tns_builder import (
    create_lua_tns,
    create_multipage_tns,
    create_ndless_tns,
    create_python_tns,
    parse_tns_document,
)
from ti_nspire_mcp.usb_driver import (
    TIUSBDevice,
    build_usb_error_telemetry,
)

server = MCPServer(
    name="ti-nspire-mcp",
    version="0.2.0",
    description="Servidor MCP 2.0 para gestión, protocolo NavNet USB, .tns avanzado y puente ESP32 en calculadoras Texas Instruments",
)


# ==============================================================================
# 1. MCP RESOURCES (ti://calculator/screenshot, ti://calculator/storage)
# ==============================================================================


@server.resource(
    "ti://calculator/screenshot",
    name="calculator_screenshot",
    title="Captura de Pantalla en Vivo TI-Nspire (RGB565 -> PNG Base64)",
    description="Recurso dinámico que devuelve la pantalla actual 320x240 de la calculadora TI-Nspire para modelos multimodales.",
    mime_type="application/json",
)
def resource_calculator_screenshot() -> str:
    """Devuelve el estado gráfico actual de la pantalla de la calculadora en formato JSON con PNG Base64."""
    dev = TIUSBDevice.find_first(allow_mock_fallback=True)
    if dev is None:
        return json.dumps(
            {
                "connected": False,
                "telemetry": build_usb_error_telemetry("Calculadora desconectada al leer ti://calculator/screenshot"),
            }
        )
    capture = dev.capture_screen_live()
    return json.dumps(capture, ensure_ascii=False)


@server.resource(
    "ti://calculator/storage",
    name="calculator_storage",
    title="Estado del Almacenamiento Interno TI-Nspire",
    description="Recurso dinámico con métricas en tiempo real de memoria Flash, RAM, batería y sistema de archivos raíz.",
    mime_type="application/json",
)
def resource_calculator_storage() -> str:
    """Devuelve métricas detalladas de almacenamiento Flash/RAM y árbol de directorios raíz."""
    dev = TIUSBDevice.find_first(allow_mock_fallback=True)
    if dev is None:
        return json.dumps(
            {
                "connected": False,
                "telemetry": build_usb_error_telemetry("Calculadora desconectada al leer ti://calculator/storage"),
            }
        )
    storage = dev.get_storage_status()
    fs_root = dev.list_remote_filesystem("/")
    payload = {
        "connected": not dev.is_mock,
        "is_mock": dev.is_mock,
        "device": dev.get_info(),
        "storage": storage.model_dump(),
        "used_flash_percent": storage.used_flash_percent,
        "root_directory": fs_root,
    }
    return json.dumps(payload, ensure_ascii=False)


# ==============================================================================
# 2. MCP TOOLS - USB NATIVO (NavNet / DocLink) Y GESTIÓN DE DISPOSITIVO
# ==============================================================================


@server.tool()
def get_calculator_status() -> dict[str, Any]:
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
            "telemetry": build_usb_error_telemetry("No se encontró dispositivo con Vendor ID 0x0451 en el bus USB."),
        }

    info = dev.get_info()
    return {
        "connected": True,
        "device": info,
        "storage": dev.get_storage_status().model_dump(),
        "message": f"Calculadora detectada: {info.get('model', 'Texas Instruments')}",
    }


@server.tool()
def direct_upload_tns(
    filename: str,
    python_code: str | None = None,
    local_tns_path: str | None = None,
    remote_directory: str = "/PyLib",
) -> dict[str, Any]:
    """
    Sube un archivo .tns directamente al almacenamiento interno de la calculadora TI-Nspire por USB
    mediante el protocolo de paquetes NavNet/DocLink (servicio 0x4060, Bulk Endpoints IN/OUT)
    sin depender de ningún navegador web externo.

    :param filename: Nombre de destino del archivo (ej. 'solucionador.tns').
    :param python_code: Código Python opcional para empaquetar al vuelo antes de subir.
    :param local_tns_path: Ruta local opcional a un archivo .tns ya existente en disco.
    :param remote_directory: Carpeta de destino en la memoria de la calculadora (por defecto '/PyLib').
    """
    if not filename.endswith(".tns"):
        filename += ".tns"

    if local_tns_path:
        path_obj = Path(local_tns_path)
        if not path_obj.exists() or not path_obj.is_file():
            err = ErrorTelemetry(
                error_code="FILE_NOT_FOUND",
                category="filesystem",
                message=f"No existe el archivo local: {local_tns_path}",
                suggestions=["Verifica la ruta absoluta del archivo .tns o proporciona 'python_code'."],
            )
            return {"success": False, "telemetry": err.model_dump()}
        tns_bytes = path_obj.read_bytes()
    elif python_code is not None:
        lint = validate_micropython_script(python_code)
        if not lint.syntax_ok:
            err = ErrorTelemetry(
                error_code="INVALID_MICROPYTHON_SYNTAX",
                category="validation",
                message="El código Python contiene errores de sintaxis y no puede subirse.",
                details={"lint_report": lint.model_dump()},
            )
            return {"success": False, "telemetry": err.model_dump(), "lint_report": lint.model_dump()}
        tns_bytes = create_python_tns(script_name=filename.replace(".tns", ""), python_code=python_code)
    else:
        err = ErrorTelemetry(
            error_code="MISSING_UPLOAD_SOURCE",
            category="validation",
            message="Debes proporcionar 'python_code' o 'local_tns_path' para subir a la calculadora.",
            suggestions=["Envía el código fuente en 'python_code' o la ruta de un archivo .tns en 'local_tns_path'."],
        )
        return {"success": False, "telemetry": err.model_dump()}

    dev = TIUSBDevice.find_first(allow_mock_fallback=True)
    if dev is None:
        return {
            "success": False,
            "telemetry": build_usb_error_telemetry("No se pudo abrir sesión NavNet para subir el archivo .tns."),
        }

    return dev.direct_upload_tns(filename=filename, tns_bytes=tns_bytes, remote_dir=remote_directory)


@server.tool()
def capture_screen_live(save_path: str | None = None) -> dict[str, Any]:
    """
    Envía el paquete NavNet de solicitud de captura de framebuffer (0x4024 / 0x4021) a la TI-Nspire,
    recibe los píxeles en formato RGB565 (16 bits, 320x240), los convierte con Pillow a PNG
    y los devuelve codificados en Base64 para análisis visual por la IA.

    :param save_path: Ruta opcional en disco donde guardar también el archivo .png.
    """
    dev = TIUSBDevice.find_first(allow_mock_fallback=True)
    if dev is None:
        return {
            "success": False,
            "telemetry": build_usb_error_telemetry("No hay calculadora disponible para capturar la pantalla."),
        }
    return dev.capture_screen_live(save_path=save_path)


@server.tool()
def list_remote_filesystem(directory: str = "/") -> dict[str, Any]:
    """
    Lee el árbol de directorios interno de la memoria Flash de la calculadora TI-Nspire
    mediante el servicio NavNet 0x4060.

    :param directory: Directorio remoto a consultar (ej. '/', '/PyLib', '/MyLib').
    """
    dev = TIUSBDevice.find_first(allow_mock_fallback=True)
    if dev is None:
        return {
            "success": False,
            "telemetry": build_usb_error_telemetry("No hay calculadora disponible para listar el sistema de archivos."),
        }
    return dev.list_remote_filesystem(directory=directory)


@server.tool()
def factory_reset_calculator(
    mode: str = "delete_documents",
    reset_usb_bus: bool = True,
) -> dict[str, Any]:
    """
    Ejecuta la restauración de fábrica de documentos de la calculadora TI-Nspire CX II vía MCP
    (equivalente a la Opción 2 'Delete Document Folder Contents' del Menú de Mantenimiento):
    elimina todos los archivos .tns de usuario mediante paquetes NavNet 0x4060 (CMD_FILE_DELETE 0x09),
    conserva el Sistema Operativo instalado y ejecuta un reinicio físico del bus USB (dev.reset()).

    :param mode: 'delete_documents' (Opción 2: limpia todos los documentos conservando el OS).
    :param reset_usb_bus: Si es True, envía un USB Bus Reset al hardware al finalizar.
    """
    dev = TIUSBDevice.find_first(allow_mock_fallback=True)
    if dev is None:
        return {
            "success": False,
            "telemetry": build_usb_error_telemetry(
                "No se detectó calculadora conectada para ejecutar el reinicio de fábrica."
            ),
        }
    return dev.factory_reset_documents(mode=mode, reset_usb_bus=reset_usb_bus)


# ==============================================================================
# 3. MCP TOOLS - MOTOR AVANZADO .TNS, LATEX, LUA, NDLESS Y LINTER MICROPYTHON
# ==============================================================================


@server.tool()
def create_python_tns_document(
    filename: str,
    python_code: str,
    output_directory: str | None = None,
) -> dict[str, Any]:
    """
    Empaqueta código Python en un archivo de documento .tns listo para transferir
    y ejecutar directamente en la TI-Nspire CX II. Incluye verificación preventiva de MicroPython.

    :param filename: Nombre del archivo (ejemplo: 'solucionador.tns' o 'app')
    :param python_code: Código fuente en Python para la calculadora.
    :param output_directory: Carpeta de destino (por defecto el Escritorio del usuario).
    """
    if not filename.endswith(".tns"):
        filename += ".tns"

    lint_report = validate_micropython_script(python_code)
    out_dir = Path(output_directory) if output_directory else Path.home() / "Desktop"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_path = out_dir / filename

    tns_bytes = create_python_tns(
        script_name=filename.replace(".tns", ""),
        python_code=python_code,
    )
    target_path.write_bytes(tns_bytes)

    return {
        "success": True,
        "file_path": str(target_path.resolve()),
        "size_bytes": len(tns_bytes),
        "lint_report": lint_report.model_dump(),
        "message": f"Archivo '{filename}' generado correctamente. Puedes subirlo con direct_upload_tns o TI-Nspire Connect.",
    }


@server.tool()
def convert_script_to_tns(python_script_path: str) -> dict[str, Any]:
    """
    Convierte un archivo .py existente en tu computadora a formato .tns de TI-Nspire.

    :param python_script_path: Ruta absoluta al archivo .py a convertir.
    """
    p = Path(python_script_path)
    if not p.exists() or not p.is_file():
        err = ErrorTelemetry(
            error_code="SCRIPT_NOT_FOUND",
            category="filesystem",
            message=f"El archivo especificado no existe: {python_script_path}",
            suggestions=["Verifica que la ruta al archivo .py exista y tenga permisos de lectura."],
        )
        return {
            "success": False,
            "error": err.message,
            "telemetry": err.model_dump(),
        }

    code = p.read_text(encoding="utf-8")
    lint_report = validate_micropython_script(code)
    target_name = p.stem + ".tns"
    target_path = p.parent / target_name

    tns_bytes = create_python_tns(script_name=p.stem, python_code=code)
    target_path.write_bytes(tns_bytes)

    return {
        "success": True,
        "tns_path": str(target_path.resolve()),
        "size_bytes": len(tns_bytes),
        "lint_report": lint_report.model_dump(),
        "message": f"Convertido '{p.name}' a '{target_name}'.",
    }


@server.tool()
def create_multipage_tns_document(
    filename: str,
    pages: list[dict[str, Any]],
    document_title: str = "TI-Nspire MultiApp Document",
    output_directory: str | None = None,
) -> dict[str, Any]:
    """
    Genera un documento .tns avanzado con soporte multipágina y multiaplicación:
    hojas de cálculo ('spreadsheet'), notas matemáticas formateadas con LaTeX ('notes'),
    scripts de Python ('python'), scripts de Lua ('lua'), calculadoras CAS ('calculator')
    y binarios nativos de Ndless ('ndless').

    :param filename: Nombre del archivo .tns a crear.
    :param pages: Lista de especificaciones de página (TNSPageSpec: page_type, title, content, formulas_latex, spreadsheet_columns).
    :param document_title: Título global del documento.
    :param output_directory: Directorio donde guardar el archivo .tns.
    """
    if not filename.endswith(".tns"):
        filename += ".tns"

    try:
        validated_specs = [TNSPageSpec.model_validate(p) for p in pages]
        tns_bytes = create_multipage_tns(validated_specs, document_title=document_title)
    except (ValidationError, ValueError) as exc:
        err = ErrorTelemetry(
            error_code="INVALID_MULTIPAGE_SPEC",
            category="tns_packaging",
            message=f"Error validando la estructura multipágina: {exc}",
            suggestions=[
                "Asegúrate de que cada página incluya 'page_type' ('python', 'notes', 'spreadsheet', 'lua', 'ndless', 'calculator') y 'title'."
            ],
        )
        return {"success": False, "telemetry": err.model_dump()}

    out_dir = Path(output_directory) if output_directory else Path.home() / "Desktop"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_path = out_dir / filename
    target_path.write_bytes(tns_bytes)

    return {
        "success": True,
        "file_path": str(target_path.resolve()),
        "size_bytes": len(tns_bytes),
        "page_count": len(validated_specs),
        "page_types": [s.page_type for s in validated_specs],
        "message": f"Documento multipágina '{filename}' generado con {len(validated_specs)} páginas.",
    }


@server.tool()
def convert_latex_to_nspire_xml(latex_expression: str, evaluate: bool = False) -> dict[str, Any]:
    """
    Convierte expresiones matemáticas en LaTeX a etiquetas XML matemáticas nativas
    de TI-Nspire (<expr>) y a notación simbólica interna de TI-Nspire OS.

    :param latex_expression: Expresión en LaTeX (ej. '\\frac{-b \\pm \\sqrt{b^{2}-4ac}}{2a}').
    :param evaluate: Si es True, marca la etiqueta <expr eval="1"> para evaluación automática.
    """
    nspire_expr = latex_to_nspire_expr(latex_expression)
    xml_tag = latex_to_nspire_xml(latex_expression, evaluate=evaluate)
    return {
        "success": True,
        "input_latex": latex_expression,
        "nspire_expression": nspire_expr,
        "nspire_xml_tag": xml_tag,
    }


@server.tool()
def create_lua_or_ndless_tns(
    filename: str,
    mode: str = "lua",
    code_or_payload: str = "",
    output_directory: str | None = None,
) -> dict[str, Any]:
    """
    Empaqueta un script de Lua (motor TI-Nspire Script App) o una aplicación nativa de Ndless
    (cabecera PRG\\x00) en un archivo .tns ejecutable.

    :param filename: Nombre del archivo .tns a generar.
    :param mode: 'lua' para scripts Lua o 'ndless' para aplicaciones nativas Ndless.
    :param code_or_payload: Código fuente Lua o payload para Ndless.
    :param output_directory: Carpeta de salida opcional.
    """
    if not filename.endswith(".tns"):
        filename += ".tns"
    stem = filename[:-4]

    normalized_mode = mode.strip().lower()
    if normalized_mode == "lua":
        default_lua = (
            "platform.apilevel = '2.5'\n"
            "function on.paint(gc)\n"
            "    gc:setFont('sansserif', 'b', 12)\n"
            "    gc:drawString('TI-Nspire Lua App', 20, 30, 'top')\n"
            "end\n"
        )
        tns_bytes = create_lua_tns(stem, code_or_payload or default_lua)
    elif normalized_mode == "ndless":
        tns_bytes = create_ndless_tns(stem, code_or_payload or "NDLESS_ARM_PAYLOAD")
    else:
        err = ErrorTelemetry(
            error_code="UNSUPPORTED_PACKAGE_MODE",
            category="tns_packaging",
            message=f"Modo '{mode}' no reconocido. Usa 'lua' o 'ndless'.",
        )
        return {"success": False, "telemetry": err.model_dump()}

    out_dir = Path(output_directory) if output_directory else Path.home() / "Desktop"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_path = out_dir / filename
    target_path.write_bytes(tns_bytes)

    return {
        "success": True,
        "mode": normalized_mode,
        "file_path": str(target_path.resolve()),
        "size_bytes": len(tns_bytes),
    }


@server.tool()
def inspect_tns_document(tns_path: str) -> dict[str, Any]:
    """
    Parsea e inspecciona un archivo .tns existente, devolviendo sus páginas, tipos de aplicación
    (Python, Notas, Hoja de Cálculo, Lua, Ndless), código fuente embebido y fórmulas <expr>.

    :param tns_path: Ruta al archivo .tns que se desea analizar.
    """
    p = Path(tns_path)
    if not p.exists() or not p.is_file():
        err = ErrorTelemetry(
            error_code="TNS_FILE_NOT_FOUND",
            category="filesystem",
            message=f"No se encontró el archivo .tns en: {tns_path}",
        )
        return {"success": False, "telemetry": err.model_dump()}

    try:
        parsed = parse_tns_document(p)
        return {"success": True, **parsed}
    except Exception as exc:
        err = ErrorTelemetry(
            error_code="TNS_PARSE_ERROR",
            category="tns_packaging",
            message=f"No se pudo parsear el archivo .tns: {exc}",
        )
        return {"success": False, "telemetry": err.model_dump()}


@server.tool()
def validate_micropython_code(python_code: str) -> dict[str, Any]:
    """
    Herramienta de depuración preventiva de código MicroPython antes del envío a la TI-Nspire CX II.
    Verifica sintaxis AST, módulos restringidos, coordenadas fuera de pantalla (320x240),
    rangos de color RGB (0-255) y huella de RAM.

    :param python_code: Código Python a validar.
    """
    report = validate_micropython_script(python_code)
    return report.model_dump()


@server.tool()
def get_device_specs(model: str = "TI-Nspire CX II") -> dict[str, Any]:
    """
    Proporciona especificaciones técnicas completas, módulos de Python soportados
    y restricciones de hardware de la calculadora Texas Instruments.
    """
    return {
        "model": model,
        "screen": {
            "resolution": "320 x 240 píxeles",
            "colors": "16 bits (65,536 colores)",
            "pixel_format": "RGB565",
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
                "Sin acceso directo a sockets de red nativos sin hardware externo (requiere puente ESP32).",
                "Memoria de ejecución de script limitada por el heap de MicroPython.",
            ],
        },
    }


@server.tool()
def generate_python_template(template_type: str = "math_solver") -> dict[str, str]:
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


# ==============================================================================
# 4. MCP TOOLS - PUENTE ESP32 (Wi-Fi / Hotspot / Pantalla Remota desde iPhone)
# ==============================================================================


@server.tool()
def generate_esp32_firmware(
    mode: str = "iphone_remote_screen",
    ssid: str = "TI-Nspire-Bridge",
    password: str = "nspire1234",
    baud_rate: int = 115200,
    output_directory: str | None = None,
) -> dict[str, Any]:
    """
    Genera sketches de Arduino (.ino), configuración ESP-IDF, cliente MicroPython para TI-Nspire
    y comandos listos para compilar con `arduino-cli` para el puente inalámbrico ESP32.

    :param mode: 'iphone_remote_screen', 'serial_wifi_bridge' o 'ai_assistant_proxy'.
    :param ssid: Nombre de la red Wi-Fi / Hotspot creada por el ESP32.
    :param password: Contraseña WPA2 (mínimo 8 caracteres).
    :param baud_rate: Velocidad de puerto serie UART entre ESP32 y TI-Nspire.
    :param output_directory: Directorio opcional donde guardar los archivos listos para compilar.
    """
    try:
        valid_mode = (
            mode
            if mode in {"iphone_remote_screen", "serial_wifi_bridge", "ai_assistant_proxy"}
            else "iphone_remote_screen"
        )
        cfg = ESP32BridgeConfig(
            mode=valid_mode,  # type: ignore[arg-type]
            ssid=ssid,
            password=password,
            baud_rate=baud_rate,
        )
    except ValidationError as exc:
        err = ErrorTelemetry(
            error_code="INVALID_ESP32_CONFIG",
            category="esp32_bridge",
            message=f"Parámetros inválidos para el puente ESP32: {exc}",
        )
        return {"success": False, "telemetry": err.model_dump()}

    return generate_esp32_sketch(config=cfg, output_dir=output_directory)


@server.tool()
def sync_esp32_remote_screen(
    status_text: str = "TI-Nspire CX II Live",
    command: str | None = None,
) -> dict[str, Any]:
    """
    Sincroniza en tiempo real la captura de pantalla de la TI-Nspire CX II y comandos bidireccionales
    con la placa ESP32 para el modo "Pantalla Remota desde iPhone" (WebSockets / Serial).

    :param status_text: Texto de estado a mostrar en el visor remoto de iPhone.
    :param command: Comando o evento de tecla opcional a transmitir de forma bidireccional.
    """
    dev = TIUSBDevice.find_first(allow_mock_fallback=True)
    if dev is None:
        return {
            "success": False,
            "telemetry": build_usb_error_telemetry("No se encontró dispositivo TI-Nspire para sincronizar con ESP32."),
        }

    screen = dev.capture_screen_live()
    frame_sync = default_esp32_session.push_frame_to_iphone(
        image_base64=screen["image_base64"],
        status_text=status_text,
    )
    cmd_result = None
    if command:
        cmd_result = default_esp32_session.send_command(command=command)

    return {
        "success": True,
        "frame_sync": frame_sync,
        "command_sync": cmd_result,
        "iphone_viewer_url": f"http://192.168.4.1:{default_esp32_session.config.http_port}",
        "websocket_endpoint": f"ws://192.168.4.1:{default_esp32_session.config.websocket_port}/ws",
    }


# ==============================================================================
# 5. MCP PROMPTS (Flujos Guiados)
# ==============================================================================


@server.prompt(
    name="crear-solucionador-paso-a-paso",
    title="Crear Solucionador Matemático Paso a Paso (.tns)",
    description="Flujo guiado para diseñar, validar y subir un solucionador interactivo paso a paso a la TI-Nspire CX II.",
)
def prompt_crear_solucionador_paso_a_paso(
    tema: str = "Ecuaciones Diferenciales y Álgebra Lineal",
    nivel: str = "Ingeniería / Universitario",
) -> list[dict[str, str]]:
    """Genera un flujo guiado para construir un documento .tns con notas LaTeX y script MicroPython."""
    return [
        {
            "role": "user",
            "content": (
                f"Necesito crear un solucionador paso a paso en mi calculadora TI-Nspire CX II sobre el tema: '{tema}' "
                f"(nivel: {nivel}).\n\n"
                "Por favor sigue este flujo de trabajo usando las herramientas MCP de `ti-nspire-mcp`:\n"
                "1. Diseña un script en MicroPython compatible con TI-Nspire CX II que pida los datos de entrada y "
                "muestre cada paso algebraico y numérico en pantalla (320x240).\n"
                "2. Ejecuta `validate_micropython_code` para garantizar 0 errores de sintaxis y que no use módulos restringidos.\n"
                "3. Usa `create_multipage_tns_document` para empaquetar una Página 1 de Notas ('notes') con las fórmulas "
                "en LaTeX convertidas a `<expr>` y una Página 2 con la aplicación interactiva en Python ('python').\n"
                "4. Sube el archivo directamente por USB usando `direct_upload_tns`."
            ),
        }
    ]


@server.prompt(
    name="generar-chuleta-formulas-tns",
    title="Generar Chuleta / Formulario Matemático en .tns",
    description="Crea un documento .tns multipágina con fórmulas en LaTeX (<expr>), tablas y visor gráfico rápido.",
)
def prompt_generar_chuleta_formulas_tns(
    materia: str = "Cálculo Vectorial y Electromagnetismo",
    incluir_python: str = "si",
) -> list[dict[str, str]]:
    """Flujo guiado para generar un formulario multipágina con expresiones matemáticas nativas <expr>."""
    return [
        {
            "role": "user",
            "content": (
                f"Genera un formulario / chuleta de estudio completa en formato `.tns` para la materia '{materia}' "
                f"(incluir visor Python: {incluir_python}).\n\n"
                "Pasos requeridos:\n"
                "1. Convierte las ecuaciones clave desde LaTeX a etiquetas nativas `<expr>` con `convert_latex_to_nspire_xml`.\n"
                "2. Construye un documento multipágina con `create_multipage_tns_document` que contenga:\n"
                "   - Página 1 ('notes'): Resumen de teoremas y fórmulas principales renderizadas con `<expr>`.\n"
                "   - Página 2 ('spreadsheet'): Tabla de constantes físicas y factores de conversión.\n"
                "   - Página 3 ('python'): Buscador interactivo y evaluador rápido renderizado con `ti_draw` en 320x240.\n"
                "3. Transfiere el documento resultante a `/PyLib` con `direct_upload_tns`."
            ),
        }
    ]


@server.prompt(
    name="diagnosticar-bootloop",
    title="Diagnosticar Bootloop o Fallo de Enlace en TI-Nspire",
    description="Guía interactiva para diagnosticar problemas de arranque, memoria llena o conflictos de Ndless/OS en TI-Nspire.",
)
def prompt_diagnosticar_bootloop(
    modelo: str = "TI-Nspire CX II",
    sintoma: str = "Reinicio continuo al cargar el sistema operativo o bloqueo USB",
) -> list[dict[str, str]]:
    """Flujo guiado de diagnóstico de hardware, protocolo NavNet y recuperación del sistema de archivos."""
    return [
        {
            "role": "user",
            "content": (
                f"Mi calculadora {modelo} presenta el siguiente síntoma: '{sintoma}'.\n\n"
                "Ayúdame a diagnosticar y recuperar el dispositivo ejecutando:\n"
                "1. `get_calculator_status` para verificar si el descriptor USB (`0x0451:0xE022` / `0xE012`) responde en el bus.\n"
                "2. `list_remote_filesystem` en `/` y `/PyLib` para comprobar si el almacenamiento Flash está saturado o si hay "
                "un binario de inicio corrupto.\n"
                "3. Si la calculadora no monta el sistema de archivos normal, indícame la combinación exacta de teclas del "
                "Menú de Mantenimiento de TI-Nspire (`[doc] + [enter] + [EE]` mientras se presiona `Reset`) para eliminar "
                "documentos problemáticos sin perder la licencia del CAS/OS."
            ),
        }
    ]


def main() -> None:
    """Punto de entrada principal para ejecutar el servidor MCP sobre stdio."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
