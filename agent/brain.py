"""
agent/brain.py
----------------
Llama a Claude con el system prompt (con slots de calendario inyectados)
+ historial de la conversacion. Detecta [AGENDAR_LLAMADA] y crea el evento.
Detecta [SOLICITA_HUMANO] y pausa la conversación avisando al dueño.

Redes de seguridad a nivel código (no dependen de que el LLM respete una
instrucción de texto):
1. Si hay aviso que prohíbe agendar del todo → se omiten los slots del
   prompt. Claude no puede ofrecer lo que no ve.
2. Si Claude igual menciona una fecha o arma un bloque [AGENDAR_LLAMADA] sin
   que haya slots reales, se bloquea acá y se reemplaza por el mensaje
   seguro — nunca se crea un evento fantasma.
3. Si hay slots reales pero Claude propone una fecha/hora fuera de la
   ventana permitida para ese día en particular (horario especial), también
   se bloquea antes de tocar Calendar.
4. Si Claude detecta un pedido de hablar con un humano, se pausa la
   conversación en código (no depende de que Claude "recuerde" no seguir
   respondiendo) y se notifica al dueño con el motivo.
"""
import re
from datetime import datetime
import anthropic
from agent import config, memory
from agent import calendar as gcal

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

_AGENDAR_RE = re.compile(
    r"\[AGENDAR_LLAMADA\](.*?)\[/AGENDAR_LLAMADA\]", re.DOTALL
)

_SOLICITA_HUMANO_RE = re.compile(
    r"\[SOLICITA_HUMANO\](.*?)\[/SOLICITA_HUMANO\]", re.DOTALL
)

_RE_RESTRICCION_AGENDA = re.compile(
    r"(no\s+agendes?|no\s+atendés?\s+llamadas?|sin\s+llamadas?|no\s+hay\s+llamadas?|no\s+agenda)",
    re.IGNORECASE,
)

_RE_FECHA_MENCION = re.compile(
    r"(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|"
    r"\d{1,2}\s*/\s*\d{1,2}|pr[oó]xima\s+semana|esta\s+semana)",
    re.IGNORECASE,
)

_MENSAJE_SIN_DISPONIBILIDAD = (
    "Por el momento no estamos tomando llamadas nuevas, te aviso apenas "
    "tengamos disponibilidad 😊"
)

def _tiene_restriccion_agenda(notas: list[dict]) -> bool:
    """True si alguna nota activa prohíbe o restringe TOTALMENTE el agendamiento."""
    return any(_RE_RESTRICCION_AGENDA.search(n.get("texto", "")) for n in notas)


def _parsear_campo(bloque: str, campo: str) -> str:
    match = re.search(rf"^{campo}:\s*(.+)$", bloque, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _parsear_fecha_hora(fecha_str: str, hora_str: str):
    """Devuelve (date, time) o (None, None) si no se pudo parsear."""
    fecha_obj = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            fecha_obj = datetime.strptime(fecha_str, fmt).date()
            break
        except (ValueError, TypeError):
            continue
    hora_obj = None
    try:
        hora_obj = datetime.strptime(hora_str, "%H:%M").time()
    except (ValueError, TypeError):
        pass
    return fecha_obj, hora_obj


def responder(numero: str, mensaje_usuario: str):
    """Devuelve (texto_para_cliente, resumen_notif_o_None)."""
    memory.agregar_mensaje(numero, "user", mensaje_usuario)
    historial = memory.obtener_historial(numero)

    notas_admin = memory.listar_notas_admin()

    if _tiene_restriccion_agenda(notas_admin):
        slots = ""
        print("[brain] Restricción total de agenda activa — slots omitidos del prompt")
    else:
        slots = gcal.obtener_slots_disponibles(dias=5)

    system_prompt = config.construir_system_prompt(
        slots_disponibles=slots, notas_admin=notas_admin
    )

    respuesta = _client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=600,
        system=system_prompt,
        messages=historial,
    )

    texto_completo = respuesta.content[0].text
    print(f"[brain] Respuesta Claude: {texto_completo[:500]}")

    match_humano = _SOLICITA_HUMANO_RE.search(texto_completo)
    match = _AGENDAR_RE.search(texto_completo)
    print(f"[brain] Bloque AGENDAR encontrado: {bool(match)} | Bloque SOLICITA_HUMANO encontrado: {bool(match_humano)}")

    bloqueado_por_seguridad = False

    if not slots and (match or _RE_FECHA_MENCION.search(texto_completo)):
        print("[brain] Bloqueado: mencionaba fecha/agenda sin disponibilidad real")
        bloqueado_por_seguridad = True

    elif match:
        bloque_tmp = match.group(1).strip()
        fecha_tmp = _parsear_campo(bloque_tmp, "Fecha")
        hora_tmp = _parsear_campo(bloque_tmp, "Hora")
        fecha_obj, hora_obj = _parsear_fecha_hora(fecha_tmp, hora_tmp)
        try:
            if fecha_obj and hora_obj and not gcal.horario_permitido(fecha_obj, hora_obj):
                print(f"[brain] Bloqueado: {fecha_tmp} {hora_tmp} fuera de la ventana permitida")
                bloqueado_por_seguridad = True
        except Exception as e:
            print(f"[brain] No se pudo validar horario propuesto: {e}")

    if bloqueado_por_seguridad:
        texto_completo = _MENSAJE_SIN_DISPONIBILIDAD
        match = None
        match_humano = None

    # ── Pedido de hablar con un humano: pausa la conversación en código ────
    # No depende de que Claude "recuerde" no seguir respondiendo — se
    # bloquea acá mismo, en el mismo mensaje donde se detecta.
    if match_humano:
        bloque_h = match_humano.group(1).strip()
        motivo = _parsear_campo(bloque_h, "Motivo") or "No especificado"

        texto_cliente = _SOLICITA_HUMANO_RE.sub("", texto_completo).strip()
        memory.agregar_mensaje(numero, "assistant", texto_cliente)

        try:
            memory.pausar_conversacion_indefinida(numero)
            print(f"[brain] Conversación pausada indefinidamente por pedido de humano: {numero}")
        except Exception as e:
            print(f"[brain] No se pudo pausar la conversación tras pedido de humano: {e}")

        resumen_notif = (
            f"Cliente pidió hablar con una persona\n"
            f"WhatsApp: {numero}\n"
            f"Motivo: {motivo}\n"
            f"Bot pausado para esta conversación — escribí /bot para reactivarlo cuando termines."
        )
        return texto_cliente, resumen_notif
    # ─────────────────────────────────────────────────────────────────────

    memory.agregar_mensaje(numero, "assistant", texto_completo)

    resumen_notif = None

    if match:
        bloque = match.group(1).strip()
        nombre = _parsear_campo(bloque, "Nombre") or numero
        fecha  = _parsear_campo(bloque, "Fecha")
        hora   = _parsear_campo(bloque, "Hora")
        tema   = _parsear_campo(bloque, "Tema")

        ok = gcal.crear_evento(
            fecha_str=fecha,
            hora_str=hora,
            nombre_cliente=nombre,
            numero_cliente=numero,
            tema=tema,
        )

        estado = "Evento creado en Google Calendar." if ok else "No se pudo crear en Calendar."
        resumen_notif = (
            f"Nueva llamada agendada\n"
            f"Nombre: {nombre}\n"
            f"WhatsApp: {numero}\n"
            f"Fecha: {fecha} - {hora}\n"
            f"Tema: {tema}\n"
            f"{estado}"
        )

        texto_cliente = _AGENDAR_RE.sub("", texto_completo).strip()
        return texto_cliente, resumen_notif

    return texto_completo, resumen_notif
