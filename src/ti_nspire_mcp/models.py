"""
Modelos de datos Pydantic V2 para validación estricta y telemetría enriquecida
en el servidor MCP de Texas Instruments TI-Nspire.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ErrorTelemetry(BaseModel):
    """Telemetría estructurada para diagnóstico de errores de hardware o protocolo."""

    model_config = ConfigDict(extra="forbid")

    error_code: str = Field(..., description="Código identificador del error")
    category: Literal["usb_transport", "navnet_protocol", "tns_packaging", "validation", "esp32_bridge", "filesystem"]
    message: str = Field(..., description="Descripción legible del error")
    recoverable: bool = Field(default=True, description="Indica si la operación puede reintentarse")
    timestamp_utc: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="Marca de tiempo ISO-8601 en UTC",
    )
    suggestions: list[str] = Field(default_factory=list, description="Pasos sugeridos para resolver el problema")
    details: dict[str, Any] = Field(default_factory=dict, description="Contexto técnico adicional")


class RemoteFileEntry(BaseModel):
    """Representa un archivo o directorio dentro del almacenamiento interno de la TI-Nspire."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Nombre del archivo o carpeta (ej. 'algebra.tns')")
    path: str = Field(..., description="Ruta absoluta en la calculadora (ej. '/PyLib/algebra.tns')")
    entry_type: Literal["file", "directory"] = Field(..., description="Tipo de entrada en el sistema de archivos")
    size_bytes: int = Field(default=0, ge=0, description="Tamaño en bytes")
    modified_timestamp: int = Field(default=0, ge=0, description="Timestamp Unix de última modificación")


class StorageStatus(BaseModel):
    """Estado del almacenamiento Flash y RAM en tiempo real de la calculadora."""

    model_config = ConfigDict(extra="forbid")

    total_flash_bytes: int = Field(default=104_857_600, ge=0)
    used_flash_bytes: int = Field(default=18_432_000, ge=0)
    free_flash_bytes: int = Field(default=86_425_600, ge=0)
    total_ram_bytes: int = Field(default=67_108_864, ge=0)
    free_ram_bytes: int = Field(default=42_991_616, ge=0)
    os_version: str = Field(default="5.4.0.259")
    battery_percent: int = Field(default=95, ge=0, le=100)
    is_charging: bool = Field(default=True)
    mode: Literal["usb_live", "mock_simulated"] = Field(default="usb_live")

    @property
    def used_flash_percent(self) -> float:
        if self.total_flash_bytes == 0:
            return 0.0
        return round((self.used_flash_bytes / self.total_flash_bytes) * 100.0, 2)


class TNSPageSpec(BaseModel):
    """Especificación de una página (card) dentro de un documento .tns multipágina."""

    model_config = ConfigDict(extra="forbid")

    page_type: Literal["python", "notes", "spreadsheet", "lua", "ndless", "calculator"] = Field(
        default="python",
        description="Tipo de aplicación en la página del documento .tns",
    )
    title: str = Field(default="Page", min_length=1, max_length=128, description="Título o nombre del script/página")
    content: str = Field(default="", description="Código fuente (Python/Lua) o texto principal de notas")
    formulas_latex: list[str] = Field(
        default_factory=list,
        description="Lista de expresiones en LaTeX para insertar como etiquetas <expr> nativas",
    )
    spreadsheet_columns: dict[str, list[str | int | float]] = Field(
        default_factory=dict,
        description="Columnas para hojas de cálculo (ej. {'A': [1, 2, 3], 'B': ['=a1*2', 4, 6]})",
    )
    ndless_hex_payload: str | None = Field(
        default=None,
        description="Payload binario codificado en hexadecimal para aplicaciones nativas Ndless",
    )

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("El título de la página no puede estar vacío.")
        return cleaned


class LintIssue(BaseModel):
    """Problema o advertencia detectado en un script MicroPython para TI-Nspire."""

    model_config = ConfigDict(extra="forbid")

    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    line: int | None = None
    column: int | None = None
    suggestion: str | None = None


class MicroPythonLintReport(BaseModel):
    """Reporte completo de validación de código MicroPython antes de enviarlo a la TI-Nspire."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    syntax_ok: bool
    compatible_with_ti_nspire: bool
    imported_modules: list[str] = Field(default_factory=list)
    restricted_modules: list[str] = Field(default_factory=list)
    estimated_ram_bytes: int = Field(default=0, ge=0)
    issues: list[LintIssue] = Field(default_factory=list)


class ESP32BridgeConfig(BaseModel):
    """Configuración del puente inalámbrico ESP32 para TI-Nspire."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["iphone_remote_screen", "serial_wifi_bridge", "ai_assistant_proxy"] = Field(
        default="iphone_remote_screen"
    )
    ssid: str = Field(default="TI-Nspire-Bridge", min_length=1, max_length=32)
    password: str = Field(default="nspire1234", min_length=8, max_length=64)
    baud_rate: int = Field(default=115200, ge=9600, le=921600)
    websocket_port: int = Field(default=81, ge=1, le=65535)
    http_port: int = Field(default=80, ge=1, le=65535)
    serial_port: str = Field(default="/dev/cu.usbserial-0001")
    board_fqbn: str = Field(default="esp32:esp32:esp32")
