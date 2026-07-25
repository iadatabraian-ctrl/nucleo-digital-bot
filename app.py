"""
app.py
-------
Webhook Flask. Recibe mensajes de WhatsApp via YCloud → brain.py → respuesta.

Deploy:
  - Local: flask run  (+ ngrok para exponer el webhook)
  - Render: gunicorn app:app  (usa render.yaml)

Seguridad:
  - Rate limiting: max 30 req/min por IP en el webhook POST
  - Verificación de firma HMAC-SHA256 de YCloud (header X-YCloud-Signature-256)
    Requiere variable de entorno: YCLOUD_WEBHOOK_SECRET
"""
import hmac
import hashlib
import os

from flask import Flask, request, abort
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from agent import config
from agent.providers import proveedor_activo
from agent.brain import responder

app = Flask(__name__)

# ── Rate limiting ──────────────────────────────────────────────────────────────
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "60 per hour"],
    storage_uri="memory://",
)

YCLOUD_WEBHOOK_SECRET = os.environ.get("YCLOUD_WEBHOOK_SECRET", "")


def _verificar_firma_ycloud(payload_bytes: bytes) -> bool:
    """
    YCloud firma el body con HMAC-SHA256 usando tu webhook secret.
    Header: X-YCloud-Signature-256  →  sha256=<hex>
    Si no está configurado el secret, se omite la verificación (dev mode).
    """
    if not YCLOUD_WEBHOOK_SECRET:
        print("[SECURITY] YCLOUD_WEBHOOK_SECRET no configurado — verificación omitida")
        return True

    firma_header = request.headers.get("X-YCloud-Signature-256", "")
    if not firma_header.startswith("sha256="):
        return False

    firma_recibida = firma_header[len("sha256="):]
    firma_esperada = hmac.new(
        YCLOUD_WEBHOOK_SECRET.encode(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(firma_recibida, firma_esperada)


# ── Rutas ──────────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["GET"])
def verificar_webhook():
    """Verificación de webhook (compatible Meta y YCloud)."""
    modo      = request.args.get("hub.mode")
    challenge = request.args.get("hub.challenge")
    if modo == "subscribe" and challenge:
        return challenge, 200
    return "ok", 200


@app.route("/webhook", methods=["POST"])
@limiter.limit("30 per minute")
def recibir_mensaje():
    payload_bytes = request.get_data()

    if not _verificar_firma_ycloud(payload_bytes):
        print("[SECURITY] Firma inválida — request rechazado")
        abort(401)

    import json
    try:
        payload = json.loads(payload_bytes) if payload_bytes else {}
    except Exception:
        payload = {}

    mensaje = proveedor_activo.parsear_mensaje_entrante(payload)

    if mensaje is None:
        return "ok", 200

    numero        = mensaje["numero"]
    texto_usuario = mensaje["texto"]

    if texto_usuario == "__AUDIO_NO_TRANSCRIPTO__":
        proveedor_activo.enviar_mensaje(
            numero, "No pude escuchar el audio 😅 ¿podés escribirlo?"
        )
        return "ok", 200

    try:
        respuesta, resumen_notif = responder(numero, texto_usuario)
    except Exception as e:
        print(f"[ERROR en brain.responder] {e}")
        respuesta     = "Disculpá, tuve un problema procesando tu mensaje. Ya te contactamos."
        resumen_notif = None

    proveedor_activo.enviar_mensaje(numero, respuesta)

    if resumen_notif and config.OWNER_WHATSAPP_NUMBER:
        proveedor_activo.enviar_mensaje(config.OWNER_WHATSAPP_NUMBER, resumen_notif)

    return "ok", 200


@app.route("/", methods=["GET"])
def health():
    return "Nucleo Digital Bot — corriendo ✅", 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)
