"""
Driver de bajo nivel para comunicación USB con calculadoras Texas Instruments.
Soporta detección e interacción con la familia TI-Nspire CX / CX II y TI-84 Plus.
"""

from typing import Dict, Any, List, Optional
import usb.core
import usb.util

# Identificadores USB conocidos de Texas Instruments
TI_VENDOR_ID = 0x0451

TI_PRODUCT_IDS = {
    0xE022: "TI-Nspire(tm) CX II Handheld",
    0xE023: "TI-Nspire(tm) CX II CAS Handheld",
    0xE012: "TI-Nspire(tm) CX Handheld",
    0xE013: "TI-Nspire(tm) CX CAS Handheld",
    0xE003: "TI-84 Plus / Silver Edition",
    0xE008: "TI-84 Plus CE",
}


class TIUSBDevice:
    """Representa y gestiona la conexión con un dispositivo Texas Instruments."""

    def __init__(self, dev: usb.core.Device):
        self.dev = dev
        self.vendor_id = dev.idVendor
        self.product_id = dev.idProduct
        self.model_name = TI_PRODUCT_IDS.get(
            dev.idProduct, f"Dispositivo TI Desconocido (0x{dev.idProduct:04X})"
        )

    @classmethod
    def find_all(cls) -> List["TIUSBDevice"]:
        """Encuentra todos los dispositivos Texas Instruments conectados."""
        devices = []
        try:
            found = usb.core.find(find_all=True, idVendor=TI_VENDOR_ID)
            for d in found:
                devices.append(cls(d))
        except Exception as e:
            # En caso de permisos restringidos en macOS sin backend configurado
            pass
        return devices

    @classmethod
    def find_first(cls) -> Optional["TIUSBDevice"]:
        """Encuentra el primer dispositivo Texas Instruments conectado."""
        devices = cls.find_all()
        return devices[0] if devices else None

    def get_info(self) -> Dict[str, Any]:
        """Obtiene detalles del dispositivo."""
        info = {
            "model": self.model_name,
            "vendor_id": f"0x{self.vendor_id:04X}",
            "product_id": f"0x{self.product_id:04X}",
            "bus": self.dev.bus,
            "address": self.dev.address,
            "status": "Conectado",
        }
        try:
            if self.dev.manufacturer:
                info["manufacturer"] = self.dev.manufacturer
            if self.dev.product:
                info["product_string"] = self.dev.product
            if self.dev.serial_number:
                info["serial_number"] = self.dev.serial_number
        except Exception:
            # Los descriptores de cadenas pueden requerir permisos elevados en macOS
            pass
        return info
