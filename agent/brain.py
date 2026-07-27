"""
agent/brain.py
----------------
Llama a Claude con el system prompt (con slots de calendario inyectados)
+ historial de la conversacion. Detecta [AGENDAR_LLAMADA] y crea el evento.
"""
import re
import anthropic
from agent import config, memory
from agent import calendar as gcal

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

_AGENDAR_RE = re.compile(
    r"\[AGENDAR_LLAMADA\](.*?)\[/AGENDAR_LLAMADA\]", re.DOTALL
)

def _parsear_campo(bloque: str, campo: str) -> str:
    match = re.search(rf"^{campo}:\s*(.+)$", bloque, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""

def responder(numero: str, mensaje_usuario: str):
    """Devuelve (texto_para_cliente, resumen_notif_o_None)."""
    memory.agregar_mensaje(numero, "user", mensaje_usuario)
    historial = memory.obtener_historial(numero)

    slots = gcal.obtener_slots_disponibles(dias=5)
    system_prompt = config.construir_system_prompt(slots_disponibles=slots)

    respuesta = _client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=600,
        system=system_prompt,
        messages=historial,
    )

    texto_completo = respuesta.content[0].text
    memory.agregar_mensaje(numero, "assistant", texto_completo)

    # DEBUG: ver respuesta completa de Claude
    print(f"[brain] Respuesta Claude: {texto_completo[:500]}")
    match_debug = _AGENDAR_RE.search(texto_completo)
    print(f"[brain] Bloque AGENDAR encontrado: {bool(match_debug)}")

    match = _AGENDAR_RE.search(texto_completo)
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
