"""
agent/brain.py
----------------
Llama a Claude con el system prompt (con slots de calendario inyectados)
+ historial de la conversacion. Detecta [AGENDAR_LLAMADA] y crea el evento.

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
"""
import re
from datetime import datetime
import anthropic
from agent import config, memory
from agent import calendar as gcal

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

# ── Health check ──────────────────────────────────────────────────────────
_ANTHROPIC_HEALTH_CACHE_KEY = "health:anthropic"
_ANTHROPIC_HEALTH_TTL_SEG = 1800  # 30 min — no gastar en cada ping de un monitor externo

def verificar_anthropic() -> str:
    """Llamada mínima real a la API para confirmar auth + crédito disponible. Sin cache."""
    try:
        _client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        return "ok"
    except Exception as e:
        print(f"[health] Anthropic no responde: {e}")
        return "error"

def verificar_anthropic_cacheado() -> str:
    """Como verificar_anthropic(), pero reusa el resultado desde Redis por 30 min."""
    try:
        cacheado = memory.cache_get(_ANTHROPIC_HEALTH_CACHE_KEY)
        if cacheado:
            return cacheado
    except Exception as e:
        print(f"[health] No se pudo leer cache de salud en Redis: {e}")

    estado = verificar_anthropic()

    try:
        memory.cache_set(_ANTHROPIC_HEALTH_CACHE_KEY, estado, _ANTHROPIC_HEALTH_TTL_SEG)
    except Exception as e:
        print(f"[health] No se pudo guardar cache de salud en Redis: {e}")

    return estado
# ─────────────────────────────────────────────────────────────────────────

_AGENDAR_RE = re.compile(
    r"\[AGENDAR_LLAMADA\](.*?)\[/AGENDAR_LLAMADA\]", re.DOTALL
)

# Palabras clave que indican restricción TOTAL de agendamiento en una nota de admin
_RE_RESTRICCION_AGENDA = re.compile(
    r"(no\s+agendes?|no\s+atendés?\s+llamadas?|sin\s+llamadas?|no\s+hay\s+llamadas?|no\s+agenda)",
    re.IGNORECASE,
)

# Detecta si Claude menciona una fecha/día puntual en su respuesta — usado como
# red de seguridad: si NO hay slots reales (restricción total activa) pero
# Claude igual menciona un día, es una fecha inventada (de memoria del
# historial o alucinada) y hay que bloquearla en código.
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

    # ── Restricción TOTAL de agendamiento detectada en código ─────────────
    # Si hay un aviso que prohíbe agendar en general, pasamos slots vacíos al
    # prompt. Así Claude NO ve horarios disponibles y no puede ofrecerlos,
    # sin importar cuánto lo pida el cliente.
    if _tiene_restriccion_agenda(notas_admin):
        slots = ""
        print("[brain] Restricción total de agenda activa — slots omitidos del prompt")
    else:
        slots = gcal.obtener_slots_disponibles(dias=5)
    # ─────────────────────────────────────────────────────────────────────

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

    # DEBUG: ver respuesta completa de Claude
    print(f"[brain] Respuesta Claude: {texto_completo[:500]}")
    match = _AGENDAR_RE.search(texto_completo)
    print(f"[brain] Bloque AGENDAR encontrado: {bool(match)}")

    bloqueado_por_seguridad = False

    # ── Red 1: sin disponibilidad real, no se menciona fecha ni se agenda ──
    if not slots and (match or _RE_FECHA_MENCION.search(texto_completo)):
        print("[brain] Bloqueado: mencionaba fecha/agenda sin disponibilidad real (restricción total activa)")
        bloqueado_por_seguridad = True

    # ── Red 2: hay disponibilidad general, pero ¿esta fecha/hora puntual   ──
    # ── cae dentro de un horario especial (cierre parcial de ese día)?    ──
    elif match:
        bloque_tmp = match.group(1).strip()
        fecha_tmp = _parsear_campo(bloque_tmp, "Fecha")
        hora_tmp = _parsear_campo(bloque_tmp, "Hora")
        fecha_obj, hora_obj = _parsear_fecha_hora(fecha_tmp, hora_tmp)
        try:
            if fecha_obj and hora_obj and not gcal.horario_permitido(fecha_obj, hora_obj):
                print(f"[brain] Bloqueado: {fecha_tmp} {hora_tmp} cae fuera de la ventana permitida ese día")
                bloqueado_por_seguridad = True
        except Exception as e:
            print(f"[brain] No se pudo validar horario propuesto: {e}")
    # ─────────────────────────────────────────────────────────────────────

    if bloqueado_por_seguridad:
        texto_completo = _MENSAJE_SIN_DISPONIBILIDAD
        match = None

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
