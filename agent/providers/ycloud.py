"""
agent/providers/ycloud.py
--------------------------
Implementación contra YCloud API (WhatsApp Coexistence).
Incluye transcripción de audios via Groq Whisper, deduplicación de mensajes
y validación de firma HMAC del webhook.
"""
import os
import io
import hmac
import hashlib
import requests
from .base import ProveedorWhatsApp
from .. import memory

YCLOUD_API_BASE = "https://api.ycloud.com/v2"

# Deduplicación: wamids ya procesados (se limpia al reiniciar)
_wamids_procesados: set[str] = set()

# ── Catálogo de productos: semilla inicial ──────────────────────────────────
# Solo se usa si Redis todavía no tiene nada guardado para ese ID. Una vez
# que el mapeo real vive en Redis (vía /producto desde WhatsApp), esta
# semilla deja de importar — el catálogo se administra 100% por WhatsApp.
_CATALOGO_SEMILLA: dict[str, str] = {
    "an7zf2hqy6": "Automatizaciones de procesos",
    "tmtbpywd8b": "Agentes de IA personalizados",
    "5oj1894qyt": "Bots de WhatsApp con IA",
}


def _resolver_nombre_producto(pid: str) -> str | None:
    catalogo_dinamico = memory.obtener_catalogo()
    if pid in catalogo_dinamico:
        return catalogo_dinamico[pid]
    return _CATALOGO_SEMILLA.get(pid)


def _texto_desde_order(order: dict) -> tuple[str, list[str]]:
    """
    Convierte el bloque 'order' del webhook (carrito del catálogo) en un
    texto natural, para que entre al mismo flujo que un mensaje de texto
    normal y Claude lo conteste con el system prompt de siempre.
    Devuelve (texto_para_claude, ids_sin_identificar).
    """
    items = order.get("product_items", [])
    nombres = []
    ids_desconocidos: list[str] = []

    for item in items:
        pid = item.get("product_retailer_id", "")
        nombre = _resolver_nombre_producto(pid)
        cantidad = item.get("quantity", 1)

        if nombre is None:
            ids_desconocidos.append(pid)
            nombre = f"producto no identificado ({pid})"

        if cantidad and cantidad > 1:
            nombres.append(f"{nombre} (x{cantidad})")
        else:
            nombres.append(nombre)

    if not nombres:
        texto = "El cliente envió un pedido del catálogo, pero no se pudo leer el contenido."
    elif len(nombres) == 1:
        texto = f"[PEDIDO DEL CATÁLOGO] El cliente agregó al carrito: {nombres[0]}."
    else:
        texto = "[PEDIDO DEL CATÁLOGO] El cliente agregó al carrito: " + ", ".join(nombres) + "."

    return texto, ids_desconocidos


def verificar_firma(payload_bytes: bytes, firma_header: str, secret: str) -> bool:
    """
    Valida el header 'YCloud-Signature: t={timestamp},s={signature}'.
    Formato real documentado por YCloud: HMAC-SHA256("{timestamp}.{body}", secret).
    """
    if not firma_header or not secret:
        return False

    try:
        partes = dict(p.split("=", 1) for p in firma_header.split(","))
        timestamp = partes.get("t", "")
        signature = partes.get("s", "")
    except Exception:
        return False

    if not timestamp or not signature:
        return False

    signed_payload = f"{timestamp}.".encode() + payload_bytes
    esperada = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    return hmac.compare_digest(esperada, signature)


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

    def parsear_eco_manual(self, payload: dict) -> str | None:
        """
        Si el dueño le contestó a un cliente a mano desde la app de WhatsApp
        Business (Coexistence), YCloud manda un evento 'echo'. Devuelve el
        número del CLIENTE al que le escribió el dueño, o None si no aplica.
        """
        if payload.get("type") != "whatsapp.smb.message.echoes":
            return None
        msg = payload.get("whatsappMessage", {})
        numero_cliente = msg.get("to", "")
        return numero_cliente or None

    def parsear_mensaje_entrante(self, payload: dict) -> dict | None:
        try:
            if payload.get("type") != "whatsapp.inbound_message.received":
                return None

            msg = payload.get("whatsappInboundMessage", {})
            wamid = msg.get("wamid", "")

            # Deduplicación: ignorar mensajes ya procesados
            if wamid and wamid in _wamids_procesados:
                print(f"[dedup] Mensaje duplicado ignorado: {wamid[:30]}")
                return None
            if wamid:
                _wamids_procesados.add(wamid)
                # Evitar que el set crezca infinito
                if len(_wamids_procesados) > 1000:
                    _wamids_procesados.clear()

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

            elif tipo == "order":
                order = msg.get("order", {})
                texto, ids_desconocidos = _texto_desde_order(order)

                if ids_desconocidos:
                    lineas_comando = "\n".join(
                        f"/producto {pid} <nombre del producto>" for pid in ids_desconocidos
                    )
                    aviso_admin = (
                        "⚠️ Nexo recibió un pedido con producto(s) del catálogo "
                        "que todavía no tienen nombre asignado:\n\n"
                        + lineas_comando
                        + "\n\nCopiá el comando, reemplazá <nombre del producto> y mandámelo "
                        "para que lo reconozca la próxima vez."
                    )
                    if not texto or not numero:
                        return None
                    return {
                        "numero": numero,
                        "texto": texto,
                        "nombre": nombre,
                        "aviso_admin": aviso_admin,
                    }

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
