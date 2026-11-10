# 📟 ti-nspire-mcp

**Servidor Model Context Protocol (MCP)** para integrar calculadoras **Texas Instruments (TI-Nspire CX II / CX)** con asistentes de Inteligencia Artificial (Claude, Cursor, Antigravity, etc.).

---

## 🚀 Características

* **Detección Automática por USB:** Identifica al instante si tu calculadora está conectada, reconociendo el modelo (ej. *TI-Nspire(tm) CX II Handheld*), Vendor ID (`0x0451`), Product ID (`0xE022`), bus y dirección USB.
* **Empaquetado a formato `.tns`:** Convierte código Python generado por la IA en un documento `.tns` listo para transferir y ejecutar en la calculadora.
* **Conversión de Scripts Existentes:** Convierte cualquier archivo `.py` de tu ordenador a un archivo `.tns`.
* **Especificaciones y Límites de Hardware:** Proporciona a la IA la resolución exacta (320x240), módulos soportados (`ti_draw`, `ti_system`, `ti_hub`, `ti_plotlib`) y restricciones de memoria.
* **Generador de Plantillas:** Plantillas preconstruidas para gráficos 2D, solucionadores de ecuaciones, visores de notas y comunicación serie con microcontroladores (ESP32).

---

## 📁 Estructura del Proyecto

```text
ti-nspire-mcp/
├── pyproject.toml              # Dependencias y configuración con uv
├── README.md                   # Esta guía
├── src/
│   └── ti_nspire_mcp/
│       ├── __init__.py
│       ├── __main__.py         # Punto de entrada CLI
│       ├── server.py           # Servidor MCP y registro de herramientas
│       ├── usb_driver.py       # Driver de bajo nivel USB (pyusb / libusb)
│       └── tns_builder.py      # Generador de paquetes de documentos .tns
├── scripts/
│   └── test_device.py          # Script de prueba y diagnóstico rápido
└── tests/
    └── test_server.py          # Pruebas unitarias automatizadas
```

---

## 🛠️ Requisitos Previos

* **macOS** con `libusb` instalado (`brew install libusb`).
* Python `>=3.13` administrado preferentemente con `uv`.

---

## ⚡ Diagnóstico Rápido

Para comprobar que tu calculadora conectada es reconocida:

```bash
cd ~/Desktop/ti-nspire-mcp
uv run python scripts/test_device.py
```

Deberás ver una salida como:
```text
==================================================
  DIAGNÓSTICO USB - TEXAS INSTRUMENTS
==================================================
✅ Dispositivo #1: TI-Nspire(tm) CX II Handheld
   - Vendor ID:  0x0451
   - Product ID: 0xE022
   - Bus/Addr:   Bus 2, Addr 4
```

---

## 🔌 Configuración en Clientes de IA

### 1. Claude Desktop
Edita el archivo de configuración en `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ti-nspire": {
      "command": "/Users/relias/.local/bin/uv",
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

### 2. Cursor IDE
1. Ve a **Settings** $\rightarrow$ **Features** $\rightarrow$ **MCP Servers**.
2. Haz clic en **Add New MCP Server**.
3. Configuración:
   * **Name:** `ti-nspire`
   * **Type:** `command`
   * **Command:** `/Users/relias/.local/bin/uv run --directory /Users/relias/Desktop/ti-nspire-mcp ti-nspire-mcp`

### 3. Antigravity / Gemini CLI
Puedes añadirlo directamente con el comando de MCP:
```bash
agy mcp add ti-nspire -- uv run --directory /Users/relias/Desktop/ti-nspire-mcp ti-nspire-mcp
```

---

## 🧰 Herramientas MCP Disponibles

| Herramienta | Descripción |
| :--- | :--- |
| `get_calculator_status` | Detecta si la TI-Nspire está enchufada y lista por USB. |
| `create_python_tns_document` | Crea un archivo `.tns` con código Python en tu escritorio. |
| `convert_script_to_tns` | Transforma un archivo `.py` en un documento ejecutable `.tns`. |
| `get_device_specs` | Devuelve especificaciones de pantalla (320x240), RAM (64 MB) y librerías TI. |
| `generate_python_template` | Devuelve plantillas optimizadas para gráficos, notas o comunicación serie. |

---

## 📄 Licencia
MIT License - Hecho para la comunidad de desarrolladores y modders de Texas Instruments.
