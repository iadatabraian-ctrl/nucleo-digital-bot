"""
agent/providers/ycloud.py
--------------------------
Implementación contra YCloud API (WhatsApp Coexistence).
"""
import os
import requests
from .base import ProveedorWhatsApp

YCLOUD_API_BASE = "https://api.ycloud.com/v2"


class YCloudProvider(ProveedorWhatsApp):

    def __init__(self):
        self.api_key = os.environ.get("YCLOUD_API_KEY", "")
        self.from_number = os.environ.get("YCLOUD_PHONE_NUMBER", "")

    def parsear_mensaje_entrante(self, payload: dict) -> dict | None:
        try:
            event_type = payload.get("type", "")
            if event_type != "whatsapp.inbound_message.received":
                return None

            msg = payload.get("whatsappInboundMessage", {})
            tipo = msg.get("type", "")
            numero = msg.get("from", "")
            nombre = msg.get("customerProfile", {}).get("name", "")

            if tipo == "text":
                texto = msg.get("text", {}).get("body", "")
            elif tipo == "audio":
                texto = "__AUDIO_NO_TRANSCRIPTO__"
            else:
                return None

            if not texto or not numero:
                return None

            return {"numero": numero, "texto": texto, "nombre": nombre}

        except (KeyError, TypeError):
            return None

    def enviar_mensaje(self, numero: str, texto: str) -> None:
        url = f"{YCLOUD_API_BASE}/whatsapp/messages"
        headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }
        data = {
            "from": self.from_number,
            "to": numero,
            "type": "text",
            "text": {"body": texto},
        }
        resp = requests.post(url, headers=headers, json=data, timeout=15)
        if resp.status_code >= 300:
            print(f"[ERROR enviando mensaje YCloud] {resp.status_code}: {resp.text}")
