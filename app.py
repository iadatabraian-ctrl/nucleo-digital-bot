"""
app.py
"""
import os
import json
import time
import threading

from flask import Flask, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from agent import config
from agent.providers import proveedor_activo
from agent.providers.ycloud import verificar_firma
from agent.brain import responder, verificar_anthropic_cacheado
from agent import memory

app = Flask(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["5000 per day", "1000 per hour"],
    storage_uri="memory://",
)

# ── Buffer de mensajes: agrupa mensajes seguidos del mismo cliente ──────────
# antes de llamar a Claude, para no responder mensaje por mensaje.
BUFFER_VENTANA_SEG = 12  # espera tras el ÚLTIMO mensaje antes de responder
BUFFER_MAX_SEG = 30  # tope absoluto desde el PRIMER mensaje del lote

_buffers: dict[str, dict] = {}
_buffers_lock = threading.Lock()

def _procesar_buffer(numero: str):
    with _buffers_lock:
        buffer = _buffers.pop(numero, None)
    if not buffer or not buffer["mensajes"]:
        return

    if memory.esta_pausada(numero):
        print(f"[coexistence] Conversación pausada, se descarta buffer de {numero}")
        return

    texto_combinado = "\n".join(buffer["mensajes"])

    print(f"[DEBUG texto_a_claude] {texto_combinado!r}")

    try:
        respuesta, resumen_notif = responder(numero, texto_combinado)
    except Exception as e:
        print(f"[ERROR en brain.responder] {e}")
        respuesta = "Disculpá, tuve un problema procesando tu mensaje. Ya te contactamos."
        resumen_notif = None

    proveedor_activo.enviar_mensaje(numero, respuesta)

    if resumen_notif and config.OWNER_WHATSAPP_NUMBER:
        proveedor_activo.enviar_mensaje(config.OWNER_WHATSAPP_NUMBER, resumen_notif)

def _agregar_a_buffer(numero: str, texto: str):
    ahora = time.time()
    with _buffers_lock:
        buffer = _buffers.get(numero)
        if buffer is None:
            buffer = {"mensajes": [], "primer_ts": ahora, "timer": None}
            _buffers[numero] = buffer

        buffer["mensajes"].append(texto)

        if buffer["timer"] is not None:
            buffer["timer"].cancel()

        transcurrido = ahora - buffer["primer_ts"]
        restante_max = BUFFER_MAX_SEG - transcurrido
        espera = min(BUFFER_VENTANA_SEG, max(restante_max, 0))

        timer = threading.Timer(espera, _procesar_buffer, args=(numero,))
        timer.daemon = True
        buffer["timer"] = timer
        timer.start()

    print(f"[buffer] {numero}: mensaje agregado, responde en {espera:.1f}s")
# ─────────────────────────────────────────────────────────────────────────────

def _normalizar_numero(numero: str) -> str:
    """Deja solo dígitos, para comparar números aunque vengan con +/espacios distinto."""
    return "".join(c for c in (numero or "") if c.isdigit())

@app.route("/webhook", methods=["GET"])
def verificar_webhook():
    modo = request.args.get("hub.mode")
    challenge = request.args.get("hub.challenge")
    if modo == "subscribe" and challenge:
        return challenge, 200
    return "ok", 200

@app.route("/webhook", methods=["POST"])
@limiter.limit("300 per minute")
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

    # ── DEBUG TEMPORAL: ver el payload crudo para mapear catálogo ────────────
    print(f"[DEBUG payload] {json.dumps(payload, ensure_ascii=False)}")
    # ─────────────────────────────────────────────────────────────────────────

    # ── Coexistence: el dueño respondió a mano desde la app ──────────────────
    numero_pausado = proveedor_activo.parsear_eco_manual(payload)
    if numero_pausado:
        texto_eco = payload.get("whatsappMessage", {}).get("text", {}).get("body", "").strip().lower()
        if texto_eco == "/bot":
            memory.reanudar_conversacion(numero_pausado)
            print(f"[coexistence] Bot reactivado manualmente para {numero_pausado}")
        elif texto_eco == "/pausa":
            memory.pausar_conversacion_indefinida(numero_pausado)
            print(f"[coexistence] Pausa INDEFINIDA activada para {numero_pausado} — 100% manual")
        else:
            memory.pausar_conversacion(numero_pausado)
            print(f"[coexistence] Dueño tomó la conversación con {numero_pausado} — bot pausado {memory.PAUSA_HORAS_DEFAULT}hs")
        return "ok", 200
    # ──────────────────────────────────────────────────────────────────────────

    mensaje = proveedor_activo.parsear_mensaje_entrante(payload)

    if mensaje is None:
        return "ok", 200

    numero = mensaje["numero"]
    texto_usuario = mensaje["texto"]

    # ── Producto de catálogo sin identificar: avisar al admin ───────────────
    aviso_admin_catalogo = mensaje.get("aviso_admin")
    if aviso_admin_catalogo and config.OWNER_WHATSAPP_NUMBER:
        proveedor_activo.enviar_mensaje(config.OWNER_WHATSAPP_NUMBER, aviso_admin_catalogo)
    # ──────────────────────────────────────────────────────────────────────────

    # ── Modo admin: mensaje del dueño directo al número del bot ──────────────
    # Se trata como comando/aviso del día, NO como consulta de un cliente.
    admin_num = _normalizar_numero(config.OWNER_WHATSAPP_NUMBER)
    if admin_num and _normalizar_numero(numero) == admin_num:
        texto_limpio = texto_usuario.strip()

        # ── Gestión del catálogo (id -> nombre de producto) ─────────────────
        if texto_limpio.lower().startswith("/producto "):
            partes = texto_limpio.split(" ", 2)
            if len(partes) < 3:
                proveedor_activo.enviar_mensaje(
                    numero, "Formato: /producto <id> <nombre del producto>"
                )
            else:
                pid, nombre_producto = partes[1], partes[2].strip()
                memory.guardar_producto(pid, nombre_producto)
                proveedor_activo.enviar_mensaje(
                    numero, f"Listo ✅ Guardé \"{nombre_producto}\" para el ID {pid}."
                )
            return "ok", 200

        elif texto_limpio.lower() == "/productos":
            catalogo = memory.obtener_catalogo()
            if not catalogo:
                proveedor_activo.enviar_mensaje(numero, "Todavía no guardaste ningún producto.")
            else:
                lineas = [f"• {pid} → {nom}" for pid, nom in catalogo.items()]
                proveedor_activo.enviar_mensaje(
                    numero, "Productos guardados:\n" + "\n".join(lineas)
                )
            return "ok", 200

        elif texto_limpio.lower().startswith("/borrar_producto "):
            pid = texto_limpio.split(" ", 1)[1].strip()
            ok = memory.borrar_producto(pid)
            if ok:
                proveedor_activo.enviar_mensaje(numero, f"Borrado ✅ el producto con ID {pid}.")
            else:
                proveedor_activo.enviar_mensaje(numero, f"No tenía guardado ningún producto con ID {pid}.")
            return "ok", 200
        # ─────────────────────────────────────────────────────────────────────

        if texto_limpio.lower() == "/limpiar":
            memory.limpiar_todo()
            proveedor_activo.enviar_mensaje(
                numero, "Listo, borré todos los avisos, horarios especiales y servicios bloqueados 👍"
            )
            return "ok", 200

        elif texto_limpio.lower() == "/avisos":
            notas = memory.listar_notas_admin()
            if not notas:
                proveedor_activo.enviar_mensaje(numero, "No tenés ningún aviso activo ahora mismo. 👍")
            else:
                lineas = []
                for n in notas:
                    if n["fecha"] is None:
                        lineas.append(f"• (sin fecha, indefinido) {n['texto']}")
                    else:
                        lineas.append(f"• {n['fecha'].strftime('%d/%m')} — {n['texto']}")
                proveedor_activo.enviar_mensaje(
                    numero, "Avisos activos:\n" + "\n".join(lineas)
                )
            return "ok", 200

        elif texto_limpio.lower() == "/horarios":
            overrides = memory.listar_horarios_activos()
            if not overrides:
                proveedor_activo.enviar_mensaje(numero, "No tenés ningún horario especial activo — todo en horario normal. 👍")
            else:
                lineas = []
                for o in overrides:
                    if o["cerrado"]:
                        lineas.append(f"• {o['fecha'].strftime('%d/%m')} — cerrado")
                    else:
                        lineas.append(f"• {o['fecha'].strftime('%d/%m')} — {o['desde']} a {o['hasta']}")
                proveedor_activo.enviar_mensaje(
                    numero, "Horarios especiales activos:\n" + "\n".join(lineas)
                )
            return "ok", 200

        elif texto_limpio.lower() == "/servicios_bloqueados":
            bloqueados = memory.obtener_servicios_bloqueados()
            if not bloqueados:
                proveedor_activo.enviar_mensaje(numero, "No tenés ningún servicio bloqueado ahora mismo. 👍")
            else:
                lineas = [f"• {b}" for b in bloqueados]
                proveedor_activo.enviar_mensaje(
                    numero, "Servicios bloqueados:\n" + "\n".join(lineas)
                )
            return "ok", 200

        elif texto_limpio.lower().startswith("/desbloquear "):
            keyword = texto_limpio.split(" ", 1)[1].strip()
            ok = memory.desbloquear_servicio(keyword)
            if ok:
                proveedor_activo.enviar_mensaje(numero, f"Listo ✅, volvés a ofrecer \"{keyword}\".")
            else:
                proveedor_activo.enviar_mensaje(
                    numero,
                    f"No tenía bloqueado nada que coincida con \"{keyword}\". "
                    f"Probá /servicios_bloqueados para ver la lista exacta.",
                )
            return "ok", 200

        else:
            # 1) ¿Es un cambio de horario? ("el jueves cerrado", "de miércoles
            #    a viernes solo de 13 a 16hs", "el miércoles sí" para resetear).
            confirmacion_horario = memory.intentar_registrar_horario(texto_limpio)
            if confirmacion_horario:
                proveedor_activo.enviar_mensaje(numero, confirmacion_horario)
                return "ok", 200

            # 2) ¿Es un bloqueo de servicio? ("no ofrezcas servicios de X")
            confirmacion_bloqueo = memory.intentar_bloquear_servicio(texto_limpio)
            if confirmacion_bloqueo:
                proveedor_activo.enviar_mensaje(numero, confirmacion_bloqueo)
                return "ok", 200

            # 3) Si no es ninguna de las anteriores, es un aviso genérico
            #    para que Claude lo tenga en cuenta en la conversación.
            fecha_resuelta = memory.agregar_nota_admin(texto_limpio)
            if fecha_resuelta:
                aviso_fecha = f"Válido para el {fecha_resuelta.strftime('%d/%m')} — se borra solo después."
            else:
                aviso_fecha = "Sin fecha específica — queda activo hasta que me escribas /limpiar."
            proveedor_activo.enviar_mensaje(
                numero, f"Anotado ✅: \"{texto_limpio}\"\n{aviso_fecha}"
            )
        return "ok", 200
    # ──────────────────────────────────────────────────────────────────────────

    if memory.esta_pausada(numero):
        print(f"[coexistence] Conversación pausada, el bot no responde a {numero}")
        return "ok", 200

    if texto_usuario == "__AUDIO_NO_TRANSCRIPTO__":
        proveedor_activo.enviar_mensaje(
            numero, "No pude escuchar el audio 😅 ¿podés escribirlo?"
        )
        return "ok", 200

    # En vez de responder ya, se agrupa con otros mensajes seguidos del mismo
    # número y se responde una sola vez, pasada la ventana de espera.
    _agregar_a_buffer(numero, texto_usuario)

    return "ok", 200

@app.route("/", methods=["GET"])
@limiter.limit("60 per minute")
def health():
    try:
        variables_requeridas = [
            "ANTHROPIC_API_KEY",
            "YCLOUD_API_KEY",
            "UPSTASH_REDIS_URL",
            "OWNER_WHATSAPP_NUMBER",
        ]
        checks = {}
        for var in variables_requeridas:
            checks[f"env_{var}"] = "ok" if os.environ.get(var) else "error"

        try:
            memory.ping()
            checks["redis"] = "ok"
        except Exception as e:
            print(f"[health] Redis no responde: {e}")
            checks["redis"] = "error"

        checks["anthropic"] = verificar_anthropic_cacheado()

        todo_ok = all(v == "ok" for v in checks.values())
        return {"status": "ok" if todo_ok else "degraded", "checks": checks}, 200 if todo_ok else 503

    except Exception as e:
        print(f"[health] Fallo inesperado en el health check: {e}")
        return {"status": "error"}, 503

if __name__ == "__main__":
    app.run(port=5000)
