"""
app.py
-------
Webhook Flask. Recibe mensajes de WhatsApp via YCloud → brain.py → respuesta.

Deploy:
  - Local: flask run  (+ ngrok para exponer el webhook)
  - Render: gunicorn app:app  (usa render.yaml)
"""
import os
import json

from flask import Flask, request
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


# ── Rutas ──────────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["GET"])
def verificar_webhook():
    modo      = request.args.get("hub.mode")
    challenge = request.args.get("hub.challenge")
    if modo == "subscribe" and challenge:
        return challenge, 200
    return "ok", 200


@app.route("/webhook", methods=["POST"])
@limiter.limit("30 per minute")
def recibir_mensaje():
    # Log temporal para ver qué headers de firma manda YCloud
    headers_relevantes = {k: v for k, v in request.headers if k.lower().startswith(("svix", "x-ycloud", "x-hub", "webhook"))}
    if headers_relevantes:
        print(f"[DEBUG headers firma] {headers_relevantes}")
    else:
        print("[DEBUG] YCloud no mandó headers de firma reconocidos")

    payload_bytes = request.get_data()
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
