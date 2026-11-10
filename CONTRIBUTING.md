# Guía de Contribución (`ti-nspire-mcp`)

¡Gracias por tu interés en mejorar `ti-nspire-mcp`! Este proyecto busca integrar la familia de calculadoras **Texas Instruments TI-Nspire CX / CX II** con agentes de Inteligencia Artificial mediante el **Model Context Protocol (MCP 2.0)**.

## Desarrollo Local

1. Clona el repositorio e instala las dependencias con `uv`:
   ```bash
   git clone https://github.com/reliasdomi/ti-nspire-mcp.git
   cd ti-nspire-mcp
   uv sync --dev
   ```

2. Ejecuta la suite de pruebas (en modo Mock sin necesidad de calculadora física conectada):
   ```bash
   TI_NSPIRE_MOCK=1 uv run pytest
   ```

3. Verifica el tipado estricto y el formato de código antes de abrir un Pull Request:
   ```bash
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy
   ```
