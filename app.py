"""
app.py
-------
Webhook Flask. Recibe mensajes de WhatsApp via YCloud → brain.py → respuesta.

Deploy:
  - Local: flask run  (+ ngrok para exponer el webhook)
  - Render: gunicorn app:app  (usa render.yaml)

Seguridad:
  - Rate limiting: max 30 req/min por IP en el webhook POST
  - Verificación de firma Svix (usado por YCloud)
    Requiere variable de entorno: YCLOUD_WEBHOOK_SECRET (el whsec_... de YCloud)
"""
import hmac
import hashlib
import base64
import os
import json

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


def _verificar_firma_svix(payload_bytes: bytes) -> bool:
    """
    YCloud usa Svix para firmar webhooks.
    Headers requeridos: svix-id, svix-timestamp, svix-signature
    Secret: el whsec_... que aparece en YCloud → Webhooks → Secreto

    Si YCLOUD_WEBHOOK_SECRET no está configurado, se omite la verificación.
    """
    if not YCLOUD_WEBHOOK_SECRET:
        print("[SECURITY] YCLOUD_WEBHOOK_SECRET no configurado — verificación omitida")
        return True

    svix_id        = request.headers.get("svix-id", "")
    svix_timestamp = request.headers.get("svix-timestamp", "")
    svix_signature = request.headers.get("svix-signature", "")

    if not svix_id or not svix_timestamp or not svix_signature:
        print("[SECURITY] Headers Svix ausentes")
        return False

    # Svix firma: HMAC-SHA256("{svix-id}.{svix-timestamp}.{body}")
    # con el secret decodificado de base64 (sin el prefijo "whsec_")
    signed_payload = f"{svix_id}.{svix_timestamp}.".encode() + payload_bytes

    secret_bytes = YCLOUD_WEBHOOK_SECRET
    if secret_bytes.startswith("whsec_"):
        secret_bytes = secret_bytes[len("whsec_"):]
    secret_decoded = base64.b64decode(secret_bytes)

    firma_esperada = base64.b64encode(
        hmac.new(secret_decoded, signed_payload, hashlib.sha256).digest()
    ).decode()

    # svix-signature puede tener múltiples firmas separadas por espacio: "v1,xxxx v1,yyyy"
    firmas_recibidas = [
        sig.split(",", 1)[1]
        for sig in svix_signature.split(" ")
        if "," in sig
    ]

    for firma in firmas_recibidas:
        if hmac.compare_digest(firma, firma_esperada):
            return True

    print("[SECURITY] Firma Svix inválida")
    return False


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

    if not _verificar_firma_svix(payload_bytes):
        abort(401)

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
