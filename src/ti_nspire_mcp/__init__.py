"""ti-nspire-mcp: Servidor Model Context Protocol 2.0 para calculadoras Texas Instruments."""

from ti_nspire_mcp.esp32_bridge import ESP32RemoteSession, generate_esp32_sketch
from ti_nspire_mcp.latex_converter import latex_to_nspire_expr, latex_to_nspire_xml
from ti_nspire_mcp.micropython_linter import validate_micropython_script
from ti_nspire_mcp.server import main, server
from ti_nspire_mcp.tns_builder import (
    create_lua_tns,
    create_multipage_tns,
    create_ndless_tns,
    create_python_tns,
    parse_tns_document,
)
from ti_nspire_mcp.usb_driver import TIUSBDevice
from ti_nspire_mcp.usb_protocol import NavNetPacket

__version__ = "0.2.0"

__all__ = [
    "ESP32RemoteSession",
    "NavNetPacket",
    "TIUSBDevice",
    "__version__",
    "create_lua_tns",
    "create_multipage_tns",
    "create_ndless_tns",
    "create_python_tns",
    "generate_esp32_sketch",
    "latex_to_nspire_expr",
    "latex_to_nspire_xml",
    "main",
    "parse_tns_document",
    "server",
    "validate_micropython_script",
]
