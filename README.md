# 📟 ti-nspire-mcp (v0.2.0 - MCP 2.0)

[![CI](https://github.com/reliasdomi/ti-nspire-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/reliasdomi/ti-nspire-mcp/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-3776AB.svg)](https://www.python.org)
[![MCP 2.0](https://img.shields.io/badge/MCP-2.0-emerald.svg)](https://modelcontextprotocol.io)

**Servidor Model Context Protocol (MCP 2.0)** de código abierto para integrar calculadoras **Texas Instruments (TI-Nspire CX II / CX II CAS / CX)** con agentes de Inteligencia Artificial (**Claude Desktop, Cursor, Antigravity, Gemini CLI**).

---

## 🚀 Características Principales

### 1. Comunicación USB Nativa a Bajo Nivel (Protocolo NavNet / DocLink / NNSE)
* **Protocolo de Paquetes Completo (`0x54FD` / `NNSE`):** Comunicación directa sobre Bulk Endpoints (`IN 0x81` / `OUT 0x01`, `512 bytes`) para dispositivos `0x0451:0xE022` (CX II) y `0x0451:0xE012` (CX).
* **`direct_upload_tns`:** Transfiere archivos `.tns` directamente al almacenamiento interno de la calculadora (servicio `0x4060`) sin depender de un navegador web externo.
* **`capture_screen_live`:** Solicita el framebuffer en vivo (`320x240`, servicio `0x4024` RLE / `0x4021` Raw), decodifica píxeles **RGB565 (16 bits)** con Pillow y expone la imagen en **PNG / Base64** para modelos multimodales.
* **`list_remote_filesystem`:** Explora el árbol de directorios interno de la memoria Flash (`/`, `/PyLib`, `/MyLib`).

### 2. Motor Avanzado de Generación y Parseo de `.tns`
* **Soporte Multipágina y Multiaplicación:** Crea documentos `.tns` combinando páginas de **Python** (`<py-app>`), **Notas Matemáticas** (`<notes-app>`), **Hojas de Cálculo** (`<lists-and-spreadsheet-app>`) y **Calculadora CAS** (`<calculator-app>`).
* **Conversor Matemático LaTeX $\rightarrow$ XML Nativo (`<expr>`):** Transforma expresiones en LaTeX (`\frac`, `\sqrt[n]`, `\int`, `\sum`, matrices `\begin{pmatrix}`, letras griegas) a etiquetas matemáticas nativas `<expr>` de TI-Nspire OS.
* **Soporte para Lua y Ndless:** Empaqueta scripts en Lua (`<script-app type="lua">`) y binarios nativos de Ndless (`PRG\x00`), además de incluir un inspector/parser de archivos `.tns` existentes (`inspect_tns_document`).

### 3. Implementación Completa de MCP 2.0
* **MCP Resources:**
  * `ti://calculator/screenshot`: Recurso dinámico con la captura actual en PNG Base64 para que la IA "vea" la pantalla de la calculadora en tiempo real.
  * `ti://calculator/storage`: Estado en tiempo real de la memoria Flash, SDRAM, batería y directorio raíz.
* **MCP Tools con Pydantic V2 y Linter MicroPython:**
  * Validación estricta de esquemas, telemetría enriquecida de errores (`ErrorTelemetry`) y analizador estático AST (`validate_micropython_code`) que detecta módulos restringidos y coordenadas fuera del lienzo `320x240` antes de subir el código.
* **MCP Prompts Guiados:**
  * `crear-solucionador-paso-a-paso`
  * `generar-chuleta-formulas-tns`
  * `diagnosticar-bootloop`

### 4. Integración del Puente Inalámbrico ESP32 (Wi-Fi / Hotspot / iPhone)
* **Modo "Pantalla Remota desde iPhone":** Canal bidireccional por WebSockets (`ws://192.168.4.1:81/ws`) y UART (`ti_hub`) con interfaz web progresiva para visualizar la pantalla `320x240` y enviar teclas desde Safari en iOS.
* **Generador de Firmware (`generate_esp32_firmware`):** Genera sketches `.ino` de Arduino, `CMakeLists.txt` para ESP-IDF, el script compañero en MicroPython y los comandos listos para compilar con `arduino-cli`.

### 5. Calidad de Software, Mocks CI/CD y Tipado Estricto
* **Modo Mock (`TI_NSPIRE_MOCK=1`):** Simula el hardware USB (`0x0451:0xE022`), el framebuffer RGB565 y el sistema de archivos interno para ejecución determinista en GitHub Actions y pruebas sin calculadora física.
* **Verificación Continua:** Cobertura completa con `pytest`, tipado estricto con `mypy --strict` y linting/formateo con `ruff`.

---

## 📁 Estructura del Proyecto

```text
ti-nspire-mcp/
├── .github/workflows/ci.yml        # Pipeline CI/CD en GitHub Actions (Ruff, Mypy, Pytest)
├── pyproject.toml                  # Configuración de paquete, uv, ruff, mypy y pytest
├── README.md                       # Documentación principal
├── CONTRIBUTING.md                 # Guía para colaboradores
├── LICENSE                         # Licencia MIT
├── src/
│   └── ti_nspire_mcp/
│       ├── __init__.py             # Exportaciones públicas
│       ├── __main__.py             # Punto de entrada CLI
│       ├── models.py               # Modelos Pydantic V2 y telemetría de errores
│       ├── usb_protocol.py         # Protocolo de paquetes NavNet (0x54FD), NNSE y RGB565/RLE
│       ├── usb_driver.py           # Driver USB Bulk IN/OUT y dispositivo Mock para CI/CD
│       ├── latex_converter.py      # Conversor de LaTeX a XML matemático nativo <expr>
│       ├── tns_builder.py          # Generador y parser multipágina .tns (Python, Notas, Lua, Ndless)
│       ├── micropython_linter.py   # Analizador AST y validador de módulos MicroPython TI
│       ├── esp32_bridge.py         # Puente Wi-Fi/WebSockets ESP32 y generador arduino-cli
│       └── server.py               # Servidor MCP 2.0 (Resources, Tools y Prompts)
├── scripts/
│   └── test_device.py              # Script de diagnóstico integral por consola
└── tests/
    ├── test_usb_protocol.py        # Tests de tramas NavNet/NNSE, RGB565 y driver USB
    ├── test_tns_and_latex.py       # Tests de LaTeX <expr>, multipágina, Lua y Ndless
    ├── test_linter_and_esp32.py    # Tests de linter MicroPython y puente ESP32
    └── test_server.py              # Tests de integración MCP 2.0
```

---

## 🛠️ Instalación y Diagnóstico Rápido

### Requisitos Previos
* **macOS / Linux / Windows** con `libusb` instalado (en macOS: `brew install libusb`).
* Python `>=3.13` y [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/reliasdomi/ti-nspire-mcp.git
cd ti-nspire-mcp
uv sync --dev
uv run python scripts/test_device.py
```

---

## 🔌 Configuración en Clientes de IA

### 1. Claude Desktop (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "ti-nspire": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/Users/relias/Desktop/ti-nspire-mcp",
        "ti-nspire-mcp"
      ]
    }
  }
}
```

### 2. Antigravity / Gemini CLI
```bash
agy mcp add ti-nspire -- uv run --directory /Users/relias/Desktop/ti-nspire-mcp ti-nspire-mcp
```

---

## 🧰 Catálogo MCP 2.0

### Recursos (`Resources`)
| URI | MIME Type | Descripción |
| :--- | :--- | :--- |
| `ti://calculator/screenshot` | `application/json` | Framebuffer en vivo `320x240` convertido de RGB565 a PNG Base64. |
| `ti://calculator/storage` | `application/json` | Estado en tiempo real de Flash, RAM, batería y directorio raíz `/`. |

### Herramientas (`Tools`)
| Herramienta | Descripción |
| :--- | :--- |
| `get_calculator_status` | Detecta la calculadora por USB y reporta descriptores, endpoints y memoria. |
| `direct_upload_tns` | Sube un archivo `.tns` directamente por USB Bulk (protocolo NavNet `0x4060`). |
| `capture_screen_live` | Captura la pantalla de la TI-Nspire (RGB565 $\rightarrow$ PNG Base64). |
| `list_remote_filesystem` | Lista los archivos y carpetas de la memoria interna de la calculadora. |
| `create_python_tns_document` | Empaqueta código Python validado en un documento `.tns`. |
| `create_multipage_tns_document` | Genera documentos `.tns` multipágina (Notas LaTeX, Hojas de Cálculo, Python, Lua, Ndless). |
| `convert_latex_to_nspire_xml` | Convierte expresiones en LaTeX a etiquetas matemáticas `<expr>` de TI-Nspire. |
| `create_lua_or_ndless_tns` | Empaqueta scripts Lua o aplicaciones nativas Ndless (`PRG\x00`). |
| `inspect_tns_document` | Analiza e inspecciona las páginas y scripts dentro de cualquier archivo `.tns`. |
| `validate_micropython_code` | Depura sintaxis AST, módulos restringidos y límites `320x240` antes del envío. |
| `generate_esp32_firmware` | Genera sketches de Arduino/ESP-IDF listos para compilar con `arduino-cli`. |
| `sync_esp32_remote_screen` | Sincroniza frames y comandos bidireccionales con el visor de iPhone vía ESP32. |
| `convert_script_to_tns` | Convierte un archivo `.py` local a `.tns`. |
| `get_device_specs` | Devuelve especificaciones de pantalla, procesador y librerías de la TI-Nspire CX II. |
| `generate_python_template` | Genera plantillas base para solucionadores, gráficos 2D y enlace serie. |

### Prompts Guiados (`Prompts`)
* `crear-solucionador-paso-a-paso`: Diseña, valida y sube un solucionador interactivo con notas LaTeX y Python.
* `generar-chuleta-formulas-tns`: Crea un formulario multipágina con expresiones `<expr>`, hojas de cálculo y visor.
* `diagnosticar-bootloop`: Guía interactiva de recuperación de arranque y diagnóstico de memoria por USB.

---

## 🧪 Ejecución de Tests y Calidad de Código

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

---

## 📄 Licencia
Distribuido bajo la licencia **MIT**. Consulta el archivo [LICENSE](LICENSE) para más detalles.
