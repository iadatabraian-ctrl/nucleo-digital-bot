"""
agent/providers/ycloud.py
--------------------------
Implementación contra YCloud API (WhatsApp Coexistence).
Incluye transcripción de audios via Groq Whisper.
"""
import os
import io
import requests
from .base import ProveedorWhatsApp

YCLOUD_API_BASE = "https://api.ycloud.com/v2"


def _transcribir_audio(url: str, mime_type: str = "audio/ogg") -> str:
    groq_api_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_api_key:
        print("[audio] GROQ_API_KEY no configurada")
        return None

    try:
        headers_ycloud = {"X-API-Key": os.environ.get("YCLOUD_API_KEY", "")}
        resp = requests.get(url, headers=headers_ycloud, timeout=30)
        if resp.status_code != 200:
            print(f"[audio] Error descargando: {resp.status_code}")
            return None

        audio_bytes = resp.content

        ext_map = {
            "audio/ogg": "ogg",
            "audio/ogg; codecs=opus": "ogg",
            "audio/mpeg": "mp3",
            "audio/mp4": "mp4",
            "audio/wav": "wav",
            "audio/webm": "webm",
        }
        ext = ext_map.get(mime_type.lower().split(";")[0].strip(), "ogg")

        groq_resp = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {groq_api_key}"},
            files={"file": (f"audio.{ext}", io.BytesIO(audio_bytes), mime_type)},
            data={"model": "whisper-large-v3-turbo", "language": "es"},
            timeout=30,
        )

        if groq_resp.status_code == 200:
            texto = groq_resp.json().get("text", "").strip()
            print(f"[audio] Transcripto: {texto[:80]}")
            return texto if texto else None
        else:
            print(f"[audio] Error Groq: {groq_resp.status_code} — {groq_resp.text}")
            return None

    except Exception as e:
        print(f"[audio] Excepción: {e}")
        return None


class YCloudProvider(ProveedorWhatsApp):

    def __init__(self):
        self.api_key = os.environ.get("YCLOUD_API_KEY", "")
        self.from_number = os.environ.get("YCLOUD_PHONE_NUMBER", "")

    def parsear_mensaje_entrante(self, payload: dict) -> dict | None:
        try:
            if payload.get("type") != "whatsapp.inbound_message.received":
                return None

            msg = payload.get("whatsappInboundMessage", {})
            tipo = msg.get("type", "")
            numero = msg.get("from", "")
            nombre = msg.get("customerProfile", {}).get("name", "")

            if tipo == "text":
                texto = msg.get("text", {}).get("body", "")

            elif tipo == "audio":
                audio_obj = msg.get("audio", {})
                url = audio_obj.get("link", "")
                mime_type = audio_obj.get("mime_type", "audio/ogg")

                if url:
                    texto = _transcribir_audio(url, mime_type)
                    if not texto:
                        texto = "__AUDIO_NO_TRANSCRIPTO__"
                else:
                    texto = "__AUDIO_NO_TRANSCRIPTO__"

            else:
                return None

            if not texto or not numero:
                return None

            return {"numero": numero, "texto": texto, "nombre": nombre}

        except (KeyError, TypeError) as e:
            print(f"[parsear_mensaje] Error: {e}")
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
            print(f"[ERROR enviando] {resp.status_code}: {resp.text}")
