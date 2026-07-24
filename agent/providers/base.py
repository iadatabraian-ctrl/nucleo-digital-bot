"""
agent/providers/base.py
------------------------
Interfaz común para proveedores de WhatsApp.
"""
from abc import ABC, abstractmethod


class ProveedorWhatsApp(ABC):

    @abstractmethod
    def parsear_mensaje_entrante(self, payload: dict) -> dict | None:
        """
        Recibe el JSON crudo del webhook y devuelve:
        {"numero": str, "texto": str, "nombre": str}
        o None si no es un mensaje real (ej: status update).
        """
        raise NotImplementedError

    @abstractmethod
    def enviar_mensaje(self, numero: str, texto: str) -> None:
        """Envía un mensaje de texto al número indicado."""
        raise NotImplementedError
