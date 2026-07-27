"""
app.py
"""
import os
import json

from flask import Flask, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from agent import config
from agent.providers import proveedor_activo
from agent.providers.ycloud import verificar_firma
from agent.brain import responder
from agent import memory

app = Flask(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "60 per hour"],
    storage_uri="memory://",
)


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
    payload_bytes = request.get_data()

    # ── Validación de firma HMAC de YCloud ───────────────────────────────────
    firma_header = request.headers.get("YCloud-Signature", "")
    secret = os.environ.get("YCLOUD_WEBHOOK_SECRET", "")

    if not verificar_firma(payload_bytes, firma_header, secret):
        print("[SEGURIDAD] Firma inválida o ausente — request rechazado")
        return "unauthorized", 401
    # ─────────────────────────────────────────────────────────────────────────

    try:
        payload = json.loads(payload_bytes) if payload_bytes else {}
    except Exception:
        payload = {}

    # ── Coexistence: el dueño respondió a mano desde la app ──────────────────
    numero_pausado = proveedor_activo.parsear_eco_manual(payload)
    if numero_pausado:
        texto_eco = (
            payload.get("whatsappInboundMessage", {}).get("text", {}).get("body", "")
            or payload.get("message", {}).get("text", {}).get("body", "")
        )
        if texto_eco.strip().lower() in ("/bot", "/bot on", "segui vos", "seguí vos"):
            memory.reanudar_conversacion(numero_pausado)
            print(f"[coexistence] Bot reactivado manualmente para {numero_pausado}")
        else:
            memory.pausar_conversacion(numero_pausado)
            print(f"[coexistence] Dueño tomó la conversación con {numero_pausado} — bot pausado")
        return "ok", 200
    # ──────────────────────────────────────────────────────────────────────────

    mensaje = proveedor_activo.parsear_mensaje_entrante(payload)

    if mensaje is None:
        return "ok", 200

    numero        = mensaje["numero"]
    texto_usuario = mensaje["texto"]

    if memory.esta_pausada(numero):
        print(f"[coexistence] Conversación pausada, el bot no responde a {numero}")
        return "ok", 200

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
    app.run(port=5000)
