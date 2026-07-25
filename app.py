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

app = Flask(__name__)


@app.route("/webhook", methods=["GET"])
def verificar_webhook():
    """Verificación de webhook (compatible Meta y YCloud)."""
    # Meta challenge
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

    try:
        respuesta, resumen_notif = responder(numero, texto_usuario)
    except Exception as e:
        print(f"[ERROR en brain.responder] {e}")
        respuesta   = "Disculpá, tuve un problema procesando tu mensaje. Ya te contactamos."
        resumen_notif = None

    proveedor_activo.enviar_mensaje(numero, respuesta)

    # Notificar a Braian si se agendó una llamada
    if resumen_notif and config.OWNER_WHATSAPP_NUMBER:
        proveedor_activo.enviar_mensaje(config.OWNER_WHATSAPP_NUMBER, resumen_notif)

    return "ok", 200


@app.route("/", methods=["GET"])
def health():
    return "Nucleo Digital Bot — corriendo ✅", 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)
