"""
app.py
-------
Webhook Flask. Recibe mensajes de WhatsApp via YCloud → brain.py → respuesta.

Deploy:
  - Local: flask run  (+ ngrok para exponer el webhook)
  - Render: gunicorn app:app  (usa render.yaml)
"""
from flask import Flask, request
from agent import config
from agent.providers import proveedor_activo
from agent.brain import responder
import threading
import requests
import os

app = Flask(__name__)


def notificar_n8n(numero, texto):
    try:
        requests.post(
            os.environ.get("N8N_WEBHOOK_URL", ""),
            json={
                "nombre": None,
                "telefono": numero,
                "mensaje": texto,
                "fuente": "whatsapp"
            },
            headers={
                "x-lead-secret": os.environ.get("N8N_WEBHOOK_SECRET", ""),
                "Content-Type": "application/json"
            },
            timeout=5
        )
    except Exception as e:
        print(f"[n8n] Error notificando: {e}")


@app.route("/webhook", methods=["GET"])
def verificar_webhook():
    """Verificación de webhook (compatible Meta y YCloud)."""
    modo      = request.args.get("hub.mode")
    challenge = request.args.get("hub.challenge")
    if modo == "subscribe" and challenge:
        return challenge, 200
    return "ok", 200


@app.route("/webhook", methods=["POST"])
def recibir_mensaje():
    payload = request.get_json(force=True, silent=True) or {}
    mensaje = proveedor_activo.parsear_mensaje_entrante(payload)

    if mensaje is None:
        return "ok", 200

    numero         = mensaje["numero"]
    texto_usuario  = mensaje["texto"]

    if texto_usuario == "__AUDIO_NO_TRANSCRIPTO__":
        proveedor_activo.enviar_mensaje(
            numero, "No pude escuchar el audio 😅 ¿podés escribirlo?"
        )
        return "ok", 200

    # Notificar a n8n (fire and forget)
    threading.Thread(target=notificar_n8n, args=(numero, texto_usuario), daemon=True).start()

    try:
        respuesta, resumen_notif = responder(numero, texto_usuario)
    except Exception as e:
        print(f"[ERROR en brain.responder] {e}")
        respuesta   = "Disculpá, tuve un problema procesando tu mensaje. Ya te contactamos."
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
