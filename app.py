"""
app.py
------
Webhook de Nexo. Antes de tocar la IA, resuelve en este orden:
  1. ¿Es un eco de Coexistence (el dueño contestó a mano)? → pausa 2hs y listo.
  2. ¿El mensaje viene del número del dueño? → modo admin, comandos, nunca IA.
  3. ¿La conversación (o el bot entero) está pausada? → no responde nada.
  4. Si nada de eso aplica → sigue el flujo normal con Claude.
"""
import os
import re
import json
from flask import Flask, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from agent import config, memory
from agent.providers import proveedor_activo
from agent.brain import responder

app = Flask(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "60 per hour"],
    storage_uri="memory://",
)


def _normalizar_numero(numero: str) -> str:
    """Deja solo dígitos, sin '+' ni espacios, para comparar sin importar el formato."""
    return re.sub(r"\D", "", numero or "")


# ── Comandos de administración (solo desde OWNER_WHATSAPP_NUMBER) ───────────

def _procesar_comando_admin(texto: str) -> str:
    t = texto.strip()

    if t == "/pausa":
        memory.pausar_bot_global()
        return "Bot pausado globalmente 🔴 — atención 100% manual. Mandá /bot para reactivarlo."

    if t == "/bot":
        memory.reanudar_bot_global()
        return "Bot reactivado ✅."

    m = re.match(r"^/producto\s+(\S+)\s+(.+)$", t)
    if m:
        pid, nombre = m.group(1), m.group(2).strip()
        memory.guardar_producto(pid, nombre)
        return f'Producto "{pid}" guardado como "{nombre}".'

    if t == "/productos":
        catalogo = memory.obtener_catalogo()
        if not catalogo:
            return "Catálogo vacío."
        return "\n".join(f"{pid}: {nombre}" for pid, nombre in catalogo.items())

    m = re.match(r"^/borrar_producto\s+(\S+)$", t)
    if m:
        ok = memory.borrar_producto(m.group(1))
        return "Producto eliminado." if ok else "Ese producto no existe."

    if t == "/avisos":
        notas = memory.listar_notas_admin()
        if not notas:
            return "Sin avisos activos."
        lineas = []
        for n in notas:
            fecha = n["fecha"].strftime("%d/%m/%Y") if n["fecha"] else "sin vencimiento"
            lineas.append(f"- {n['texto']} (vence: {fecha})")
        return "\n".join(lineas)

    if t == "/horarios":
        horarios = memory.listar_horarios_activos()
        if not horarios:
            return "Sin horarios especiales cargados."
        lineas = []
        for h in horarios:
            fecha = h["fecha"].strftime("%d/%m/%Y")
            if h.get("cerrado"):
                lineas.append(f"- {fecha}: cerrado")
            else:
                lineas.append(f"- {fecha}: {h.get('desde')} a {h.get('hasta')}")
        return "\n".join(lineas)

    if t == "/servicios_bloqueados":
        bloqueados = memory.obtener_servicios_bloqueados()
        if not bloqueados:
            return "Sin servicios bloqueados."
        return "\n".join(f"- {b}" for b in bloqueados)

    m = re.match(r"^/desbloquear\s+(.+)$", t)
    if m:
        ok = memory.desbloquear_servicio(m.group(1))
        return "Desbloqueado ✅." if ok else "Esa palabra no estaba bloqueada."

    if t == "/limpiar":
        memory.limpiar_todo()
        return "Avisos, horarios y bloqueos reseteados ✅."

    m = re.match(r"^/reanudar\s+(\S+)$", t)
    if m:
        memory.reanudar_conversacion(m.group(1))
        return f"Conversación con {m.group(1)} reactivada ✅."

    # Texto libre: probar como horario especial, bloqueo de servicio, o aviso genérico
    resultado = memory.intentar_registrar_horario(t)
    if resultado:
        return resultado

    resultado = memory.intentar_bloquear_servicio(t)
    if resultado:
        return resultado

    fecha = memory.agregar_nota_admin(t)
    if fecha:
        return f"Anotado ✅ para el {fecha.strftime('%d/%m/%Y')}."
    return "Anotado ✅ (sin fecha específica — se borra con /limpiar)."


@app.route("/webhook", methods=["GET"])
def verificar_webhook():
    modo = request.args.get("hub.mode")
    challenge = request.args.get("hub.challenge")
    if modo == "subscribe" and challenge:
        return challenge, 200
    return "ok", 200


@app.route("/webhook", methods=["POST"])
@limiter.limit("30 per minute")
def recibir_mensaje():
    payload_bytes = request.get_data()
    try:
        payload = json.loads(payload_bytes) if payload_bytes else {}
    except Exception:
        payload = {}

    mensaje = proveedor_activo.parsear_mensaje_entrante(payload)
    if mensaje is None:
        return "ok", 200

    numero = mensaje["numero"]
    texto_usuario = mensaje.get("texto", "")
    es_eco = mensaje.get("es_eco", False)

    # ── 1. Eco de Coexistence: el dueño ya contestó a mano ───────────────
    if es_eco:
        memory.pausar_conversacion(numero, horas=2.0)
        print(f"[app] Eco de Coexistence — pausa 2hs para {numero}")
        return "ok", 200

    # ── 2. Modo admin: mensajes desde el número del dueño ────────────────
    es_owner = (
        config.OWNER_WHATSAPP_NUMBER
        and _normalizar_numero(numero) == _normalizar_numero(config.OWNER_WHATSAPP_NUMBER)
    )
    if es_owner:
        respuesta_admin = _procesar_comando_admin(texto_usuario)
        proveedor_activo.enviar_mensaje(numero, respuesta_admin)
        return "ok", 200

    # ── 3. Bot pausado (global o para esta conversación puntual) ─────────
    if memory.bot_pausado_global():
        print("[app] Bot pausado globalmente — no se responde")
        return "ok", 200

    if memory.esta_pausada(numero):
        print(f"[app] Conversación pausada — no se responde a {numero}")
        return "ok", 200

    # ── 4. Flujo normal ───────────────────────────────────────────────────
    if texto_usuario == "__AUDIO_NO_TRANSCRIPTO__":
        proveedor_activo.enviar_mensaje(
            numero, "No pude escuchar el audio 😅 ¿podés escribirlo?"
        )
        return "ok", 200

    try:
        respuesta, resumen_notif = responder(numero, texto_usuario)
    except Exception as e:
        print(f"[ERROR en brain.responder] {e}")
        respuesta = "Disculpá, tuve un problema procesando tu mensaje. Ya te contactamos."
        resumen_notif = None

    proveedor_activo.enviar_mensaje(numero, respuesta)

    if resumen_notif and config.OWNER_WHATSAPP_NUMBER:
        proveedor_activo.enviar_mensaje(config.OWNER_WHATSAPP_NUMBER, resumen_notif)

    return "ok", 200


@app.route("/", methods=["GET"])
@limiter.exempt
def health():
    return "Nucleo Digital Bot — corriendo ✅", 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)
