"""
agent/providers/meta.py
-------------------------
Implementación contra Meta Cloud API (WhatsApp Business Platform).
Soporta texto y audio (transcripción via Groq Whisper).
"""
import io
import os
import requests
from groq import Groq
from .base import ProveedorWhatsApp
from agent import config

GRAPH_URL = "https://graph.facebook.com/v20.0"

_groq_client = None


def _get_groq_client() -> Groq:
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
    return _groq_client


def _transcribir_audio(media_id: str, token: str) -> str:
    headers = {"Authorization": f"Bearer {token}"}

    media_info = requests.get(
        f"https://graph.facebook.com/v20.0/{media_id}",
        headers=headers,
        timeout=15,
    ).json()
    audio_url = media_info.get("url")
    if not audio_url:
        raise ValueError(f"No se pudo obtener URL del audio: {media_info}")

    audio_bytes = requests.get(audio_url, headers=headers, timeout=30).content
    print(f"[audio] descargado {len(audio_bytes)} bytes (media_id={media_id})")

    transcripcion = _get_groq_client().audio.transcriptions.create(
        file=("audio.ogg", io.BytesIO(audio_bytes)),
        model="whisper-large-v3",
        language="es",
    )
    return transcripcion.text


class MetaProvider(ProveedorWhatsApp):

    def __init__(self):
        self.token = config.META_ACCESS_TOKEN
        self.phone_number_id = config.META_PHONE_NUMBER_ID

    def parsear_mensaje_entrante(self, payload: dict) -> dict | None:
        try:
            entry = payload["entry"][0]["changes"][0]["value"]
            if "messages" not in entry:
                return None
            msg = entry["messages"][0]
            tipo = msg.get("type")
            numero = msg["from"]
            nombre = entry.get("contacts", [{}])[0].get("profile", {}).get("name", "")

            if tipo == "text":
                texto = msg["text"]["body"]
            elif tipo == "audio":
                media_id = msg["audio"]["id"]
                try:
                    texto = _transcribir_audio(media_id, self.token)
                    print(f"[audio] transcripcion de {numero}: {texto}")
                except Exception as e:
                    print(f"[audio] error transcribiendo: {e}")
                    texto = "__AUDIO_NO_TRANSCRIPTO__"
            else:
                return None

            return {"numero": numero, "texto": texto, "nombre": nombre}

        except (KeyError, IndexError):
            return None

    def enviar_mensaje(self, numero: str, texto: str) -> None:
        url = f"{GRAPH_URL}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        data = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "text",
            "text": {"body": texto},
        }
        resp = requests.post(url, headers=headers, json=data, timeout=15)
        if resp.status_code >= 300:
            print(f"[ERROR enviando mensaje] {resp.status_code}: {resp.text}")
