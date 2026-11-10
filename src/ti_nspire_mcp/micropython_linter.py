"""
Analizador estático y depurador preventivo para código MicroPython de TI-Nspire CX II.
Verifica:
- Sintaxis válida de Python mediante AST.
- Módulos importados permitidos vs restringidos en el firmware MicroPython v1.9+ de TI-Nspire.
- Límites de pantalla (320x240) y rangos de color RGB (0-255) en llamadas a `ti_draw`.
- Estimación de huella en RAM (heap de MicroPython).
"""

from __future__ import annotations

import ast
from typing import Final

from ti_nspire_mcp.models import LintIssue, MicroPythonLintReport

ALLOWED_TI_MODULES: Final[frozenset[str]] = frozenset(
    {
        "math",
        "cmath",
        "random",
        "time",
        "utime",
        "sys",
        "usys",
        "gc",
        "uos",
        "ure",
        "ujson",
        "ustruct",
        "micropython",
        "array",
        "ti_system",
        "ti_draw",
        "ti_plotlib",
        "ti_hub",
        "ti_rover",
        "ti_image",
    }
)

MODULE_REPLACEMENT_HINTS: Final[dict[str, str]] = {
    "numpy": "Usa los módulos nativos 'math' y 'cmath' o listas estándar de Python.",
    "scipy": "Implementa métodos numéricos directos (Newton-Raphson, Simpson) con 'math'.",
    "matplotlib": "Usa 'ti_plotlib' para gráficas cartesianas o 'ti_draw' para dibujo 2D en 320x240.",
    "pyplot": "Usa 'ti_plotlib' en lugar de matplotlib.pyplot.",
    "pygame": "Usa 'ti_draw' (fill_rect, fill_circle, draw_text) y 'ti_system.get_key()'.",
    "tkinter": "Usa 'ti_draw' o 'input()' de consola en lugar de interfaces Tkinter.",
    "socket": "El entorno nativo no tiene sockets TCP/IP; usa 'ti_hub' con el puente ESP32 por UART.",
    "requests": "Usa el módulo puente ESP32 ('ti_hub') para realizar peticiones HTTP externas.",
    "urllib": "Usa el módulo puente ESP32 ('ti_hub') para conectividad web.",
    "os": "Usa 'ti_system' (recall_value / store_value) o 'uos' limitado.",
    "subprocess": "No existen subprocesos en el firmware de la TI-Nspire CX II.",
    "threading": "MicroPython en TI-Nspire es single-threaded; usa un bucle de eventos con time.sleep().",
    "asyncio": "No disponible en MicroPython v1.9 de TI-Nspire; usa bucles síncronos.",
    "pandas": "Usa listas de tuplas o páginas de Hoja de Cálculo (.tns spreadsheet).",
}


def validate_micropython_script(code: str) -> MicroPythonLintReport:
    """
    Analiza un script de Python antes de empaquetarlo o enviarlo a la TI-Nspire CX II.
    Devuelve un reporte estructurado `MicroPythonLintReport`.
    """
    issues: list[LintIssue] = []
    imported_modules: list[str] = []
    restricted_modules: list[str] = []

    # Estimación aproximada de RAM (código fuente + sobrecarga de bytecode en heap MicroPython)
    estimated_ram = len(code.encode("utf-8")) * 3 + 2048

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        issues.append(
            LintIssue(
                severity="error",
                code="MPY001_SYNTAX_ERROR",
                message=f"Error de sintaxis en MicroPython: {exc.msg}",
                line=exc.lineno,
                column=exc.offset,
                suggestion="Corrige la sintaxis antes de empaquetar el archivo .tns.",
            )
        )
        return MicroPythonLintReport(
            valid=False,
            syntax_ok=False,
            compatible_with_ti_nspire=False,
            imported_modules=[],
            restricted_modules=[],
            estimated_ram_bytes=estimated_ram,
            issues=issues,
        )

    for node in ast.walk(tree):
        # 1. Revisar imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_mod = alias.name.split(".")[0]
                if root_mod not in imported_modules:
                    imported_modules.append(root_mod)
                if root_mod not in ALLOWED_TI_MODULES:
                    if root_mod not in restricted_modules:
                        restricted_modules.append(root_mod)
                    hint = MODULE_REPLACEMENT_HINTS.get(
                        root_mod,
                        f"El módulo '{root_mod}' no está incluido en el firmware MicroPython de TI-Nspire CX II.",
                    )
                    issues.append(
                        LintIssue(
                            severity="error",
                            code="MPY002_RESTRICTED_MODULE",
                            message=f"Módulo no soportado en TI-Nspire: '{alias.name}'.",
                            line=node.lineno,
                            column=node.col_offset,
                            suggestion=hint,
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_mod = node.module.split(".")[0]
                if root_mod not in imported_modules:
                    imported_modules.append(root_mod)
                if root_mod not in ALLOWED_TI_MODULES:
                    if root_mod not in restricted_modules:
                        restricted_modules.append(root_mod)
                    hint = MODULE_REPLACEMENT_HINTS.get(
                        root_mod,
                        f"El módulo '{root_mod}' no está disponible en TI-Nspire CX II.",
                    )
                    issues.append(
                        LintIssue(
                            severity="error",
                            code="MPY002_RESTRICTED_MODULE",
                            message=f"Importación desde módulo no soportado: '{node.module}'.",
                            line=node.lineno,
                            column=node.col_offset,
                            suggestion=hint,
                        )
                    )

        # 2. Revisar llamadas a ti_draw (set_color, draw_text, fill_rect, draw_rect)
        elif isinstance(node, ast.Call):
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name == "set_color" and len(node.args) >= 3:
                for idx, arg in enumerate(node.args[:3]):
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                        if not (0 <= arg.value <= 255):
                            issues.append(
                                LintIssue(
                                    severity="warning",
                                    code="MPY003_COLOR_OUT_OF_RANGE",
                                    message=f"Componente RGB #{idx + 1} fuera de rango (0..255): {arg.value}.",
                                    line=node.lineno,
                                    column=node.col_offset,
                                    suggestion="Usa valores enteros entre 0 y 255 en set_color(r, g, b).",
                                )
                            )

            elif (
                func_name in {"draw_text", "fill_rect", "draw_rect", "fill_circle", "draw_circle"}
                and len(node.args) >= 2
            ):
                x_arg, y_arg = node.args[0], node.args[1]
                if isinstance(x_arg, ast.Constant) and isinstance(x_arg.value, (int, float)):
                    if x_arg.value < 0 or x_arg.value > 320:
                        issues.append(
                            LintIssue(
                                severity="warning",
                                code="MPY004_SCREEN_BOUNDS_X",
                                message=f"Coordenada X ({x_arg.value}) fuera del ancho de pantalla (0..320).",
                                line=node.lineno,
                                column=node.col_offset,
                                suggestion="Ajusta la coordenada X dentro de la resolución 320x240.",
                            )
                        )
                if isinstance(y_arg, ast.Constant) and isinstance(y_arg.value, (int, float)):
                    if y_arg.value < 0 or y_arg.value > 240:
                        issues.append(
                            LintIssue(
                                severity="warning",
                                code="MPY004_SCREEN_BOUNDS_Y",
                                message=f"Coordenada Y ({y_arg.value}) fuera del alto de pantalla (0..240).",
                                line=node.lineno,
                                column=node.col_offset,
                                suggestion="Ajusta la coordenada Y dentro de la resolución 320x240.",
                            )
                        )

    if estimated_ram > 48_000:
        issues.append(
            LintIssue(
                severity="warning",
                code="MPY005_HIGH_MEMORY_USAGE",
                message=f"El script tiene una huella estimada alta ({estimated_ram} bytes) para el heap de MicroPython.",
                line=1,
                column=0,
                suggestion="Divide los datos estáticos grandes o llama a gc.collect() periódicamente.",
            )
        )

    has_errors = any(issue.severity == "error" for issue in issues)
    return MicroPythonLintReport(
        valid=not has_errors,
        syntax_ok=True,
        compatible_with_ti_nspire=len(restricted_modules) == 0,
        imported_modules=imported_modules,
        restricted_modules=restricted_modules,
        estimated_ram_bytes=estimated_ram,
        issues=issues,
    )
