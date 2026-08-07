"""
agent/providers/ycloud.py
--------------------------
Adaptador para la API de YCloud (WhatsApp BSP).

Responsabilidades:
  - Validar la firma HMAC de los webhooks entrantes
  - Parsear mensajes entrantes (texto, audio, pedidos de catálogo)
  - Detectar ecos del modo Coexistencia (mensajes enviados por el dueño)
  - Deduplicar WAMIDs para evitar procesar el mismo mensaje dos veces
  - Transcribir audios con Groq Whisper
  - Enviar mensajes de texto vía YCloud

Variables de entorno:
  YCLOUD_API_KEY         — clave de API de YCloud
  YCLOUD_WEBHOOK_SECRET  — secreto para verificar firma HMAC
  GROQ_API_KEY           — clave de Groq (para transcripción de audio)
"""
import hashlib
import hmac
import os
import tempfile
from typing import Any

import requests

from agent import config

_BASE_URL = "https://api.ycloud.com/v2"

# ── Deduplicación de WAMIDs ──────────────────────────────────────────────────
_wamids_vistos: set[str] = set()
_MAX_WAMIDS = 1000


def _marcar_wamid(wamid: str) -> bool:
    """Retorna True si el WAMID es nuevo (no procesado antes)."""
    global _wamids_vistos
    if wamid in _wamids_vistos:
        return False
    _wamids_vistos.add(wamid)
    if len(_wamids_vistos) > _MAX_WAMIDS:
        _wamids_vistos = set(list(_wamids_vistos)[-_MAX_WAMIDS // 2:])
    return True


# ── Semilla del catálogo ─────────────────────────────────────────────────────
_CATALOGO_SEMILLA: dict[str, str] = {
    "product_001": "Sitio Web Básico",
    "product_002": "Sitio Web Premium",
    "product_003": "Gestión de Redes Sociales",
}


# ── Validación HMAC ──────────────────────────────────────────────────────────

def verificar_firma(payload_bytes: bytes, firma_header: str, secret: str | None = None) -> bool:
    """
    Verifica que el webhook realmente proviene de YCloud.
    YCloud envía el HMAC-SHA256 del cuerpo en el header 'YCloud-Signature'.
    El secreto puede pasarse como argumento o se lee de config.YCLOUD_WEBHOOK_SECRET.
    """
    secreto = secret or config.YCLOUD_WEBHOOK_SECRET
    if not secreto:
        # Sin secreto configurado, aceptar todo (solo para desarrollo)
        return True
    esperada = hmac.new(
        secreto.encode(), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperada, firma_header.lower().replace("sha256=", ""))


# ── Transcripción de audio ───────────────────────────────────────────────────

def _transcribir_audio(media_id: str) -> str | None:
    """
    Descarga el audio de YCloud y lo transcribe con Groq Whisper.
    Retorna el texto o None si falla.
    """
    groq_key = config.GROQ_API_KEY
    if not groq_key:
        print("[ycloud] GROQ_API_KEY no configurada, no se transcribe audio")
        return None

    try:
        # 1. Obtener URL de descarga del archivo
        headers = {"X-API-Key": config.YCLOUD_API_KEY}
        media_resp = requests.get(
            f"{_BASE_URL}/whatsapp/media/{media_id}",
            headers=headers,
            timeout=15,
        )
        media_resp.raise_for_status()
        media_data = media_resp.json()
        url_descarga = media_data.get("url")
        mime_type    = media_data.get("mimeType", "audio/ogg")

        if not url_descarga:
            print(f"[ycloud] No se obtuvo URL de descarga para media {media_id}")
            return None

        # 2. Descargar el archivo de audio
        audio_resp = requests.get(url_descarga, timeout=30)
        audio_resp.raise_for_status()

        extension = "ogg"
        if "mpeg" in mime_type or "mp3" in mime_type:
            extension = "mp3"
        elif "mp4" in mime_type or "m4a" in mime_type:
            extension = "m4a"
        elif "wav" in mime_type:
            extension = "wav"

        # 3. Transcribir con Groq Whisper
        with tempfile.NamedTemporaryFile(suffix=f".{extension}", delete=False) as tmp:
            tmp.write(audio_resp.content)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as audio_file:
            groq_resp = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {groq_key}"},
                files={"file": (f"audio.{extension}", audio_file, mime_type)},
                data={"model": "whisper-large-v3", "language": "es"},
                timeout=60,
            )
        os.unlink(tmp_path)
        groq_resp.raise_for_status()
        texto = groq_resp.json().get("text", "").strip()
        print(f"[ycloud] Audio transcrito: {texto[:100]}")
        return texto if texto else None

    except Exception as e:
        print(f"[ycloud] Error transcribiendo audio: {e}")
        return None


# ── Detección de ecos de Coexistencia ────────────────────────────────────────

def parsear_eco_manual(payload: dict) -> str | None:
    """
    En modo Coexistencia, YCloud re-envía al webhook los mensajes que el
    dueño envía desde su teléfono (WhatsApp Business app) con el evento
    'whatsapp.smb.message.echoes'.

    IMPORTANTE: este evento SÍ trae 'status': 'sent' en whatsappMessage —
    el mismo status que usan los webhooks de confirmación de entrega de los
    mensajes que manda el propio bot (evento 'whatsapp.message.updated').
    Por eso NO se puede distinguir por 'status' (bug anterior: se ignoraban
    los ecos reales pensando que eran solo confirmaciones de entrega).
    La única forma confiable de distinguirlos es por el 'type' del evento.

    Retorna el número del cliente al que el dueño le escribió, o None.
    """
    if payload.get("type", "") != "whatsapp.smb.message.echoes":
        return None

    msg = payload.get("whatsappMessage", {})
    to = msg.get("to", "")
    return to if to else None


# ── Parser principal de mensaje entrante ────────────────────────────────────

def parsear_mensaje_entrante(payload: dict) -> dict | None:
    """
    Parsea un webhook de YCloud v2 y retorna un dict con:
      {
        'numero': str,    # E.164 del remitente
        'wamid':  str,    # ID único del mensaje
        'tipo':   str,    # 'texto' | 'audio' | 'catalogo' | 'desconocido'
        'texto':  str,    # contenido (transcrito si era audio)
        'es_eco': bool,   # True si es un eco de mensaje del dueño
      }
    Retorna None si no es un mensaje entrante procesable.

    Formato YCloud v2:
      payload.type == "whatsapp.inbound_message.received"
      payload.whatsappInboundMessage.from  → número del cliente
      payload.whatsappInboundMessage.wamid → ID del mensaje
      payload.whatsappInboundMessage.type  → "text" | "audio" | "order"
      payload.whatsappInboundMessage.text.body → texto
    """
    # Detectar eco del dueño
    numero_eco = parsear_eco_manual(payload)
    if numero_eco:
        return {
            "numero": numero_eco,
            "wamid": payload.get("id", ""),
            "tipo": "eco",
            "texto": "",
            "es_eco": True,
        }

    # Solo procesamos mensajes entrantes
    tipo_evento = payload.get("type", "")
    if tipo_evento != "whatsapp.inbound_message.received":
        print(f"[ycloud] Evento ignorado: {tipo_evento}")
        return None

    msg = payload.get("whatsappInboundMessage", {})
    if not msg:
        return None

    numero = msg.get("from", "")
    wamid  = msg.get("wamid", "") or msg.get("id", "")
    tipo   = msg.get("type", "unknown")

    if not numero:
        return None

    # Deduplicar
    if wamid and not _marcar_wamid(wamid):
        print(f"[ycloud] WAMID duplicado ignorado: {wamid}")
        return None

    # Texto plano
    if tipo == "text":
        cuerpo = msg.get("text", {}).get("body", "")
        return {
            "numero": numero,
            "wamid": wamid,
            "tipo": "texto",
            "texto": cuerpo.strip(),
            "es_eco": False,
        }

    # Audio (voz)
    if tipo == "audio":
        audio = msg.get("audio", {})
        media_id = audio.get("id", "")
        texto_transcrito = _transcribir_audio(media_id) if media_id else None
        if not texto_transcrito:
            texto_transcrito = "[Audio no transcrito]"
        return {
            "numero": numero,
            "wamid": wamid,
            "tipo": "audio",
            "texto": texto_transcrito,
            "es_eco": False,
        }

    # Pedido de catálogo
    if tipo == "order":
        from agent import memory as mem
        catalogo_redis = mem.obtener_catalogo()
        catalogo = {**_CATALOGO_SEMILLA, **catalogo_redis}
        orden = msg.get("order", {})
        items = orden.get("product_items", [])
        lineas = []
        for item in items:
            pid      = item.get("product_retailer_id", "")
            cantidad = item.get("quantity", 1)
            nombre   = catalogo.get(pid, pid)
            lineas.append(f"{cantidad}x {nombre}")
        resumen = "Pedido del catálogo: " + ", ".join(lineas) if lineas else "Pedido de catálogo"
        return {
            "numero": numero,
            "wamid": wamid,
            "tipo": "catalogo",
            "texto": resumen,
            "es_eco": False,
        }

    print(f"[ycloud] Tipo de mensaje no manejado: {tipo}")
    return {
        "numero": numero,
        "wamid": wamid,
        "tipo": "desconocido",
        "texto": f"[Mensaje de tipo '{tipo}' no soportado]",
        "es_eco": False,
    }


# ── Envío de mensajes ────────────────────────────────────────────────────────

def enviar_mensaje(numero: str, texto: str) -> bool:
    """
    Envía un mensaje de texto a través de YCloud.
    Retorna True si se envió correctamente.
    """
    if not config.YCLOUD_API_KEY:
        print("[ycloud] YCLOUD_API_KEY no configurada")
        return False

    from_number = config.YCLOUD_PHONE_NUMBER

    try:
        resp = requests.post(
            f"{_BASE_URL}/whatsapp/messages",
            headers={
                "X-API-Key": config.YCLOUD_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "from": from_number,
                "to": numero,
                "type": "text",
                "text": {"body": texto},
            },
            timeout=15,
        )
        resp.raise_for_status()
        print(f"[ycloud] Mensaje enviado a {numero}: {texto[:80]}")
        return True
    except Exception as e:
        print(f"[ycloud] Error enviando mensaje a {numero}: {e}")
        return False


# ── Clase de compatibilidad (requerida por agent/providers/__init__.py) ──────

class YCloudProvider:
    """
    Wrapper de clase sobre las funciones del módulo.
    Permite que __init__.py haga: proveedor_activo = YCloudProvider()
    sin cambiar nada más.
    """

    def verificar_firma(self, payload_bytes: bytes, firma_header: str) -> bool:
        return verificar_firma(payload_bytes, firma_header)

    def parsear_eco_manual(self, payload: dict) -> str | None:
        return parsear_eco_manual(payload)

    def parsear_mensaje_entrante(self, payload: dict) -> dict | None:
        return parsear_mensaje_entrante(payload)

    def enviar_mensaje(self, numero: str, texto: str) -> bool:
        return enviar_mensaje(numero, texto)
