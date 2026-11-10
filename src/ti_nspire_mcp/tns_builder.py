"""
Generador y empaquetador de archivos .tns (TI-Nspire Document) para scripts de Python.
"""

import io
import zipfile
from xml.sax.saxutils import escape


def create_python_tns(script_name: str, python_code: str) -> bytes:
    """
    Crea un documento .tns de TI-Nspire que contiene un script ejecutable de Python.
    Un archivo .tns es internamente un archivo ZIP con archivos XML descriptores.
    """
    buffer = io.BytesIO()

    # Document.xml básico para TI-Nspire CX II con módulo Python
    doc_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<prob xmlns="urn:TI.Problem" version="1.0">
  <sym-import>0</sym-import>
  <card>
    <is-hidden>0</is-hidden>
    <py-app version="1.0">
      <script-name>{escape(script_name)}</script-name>
      <script-code><![CDATA[{python_code}]]></script-code>
    </py-app>
  </card>
</prob>
"""

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Problem1.xml", doc_xml.encode("utf-8"))

    return buffer.getvalue()
