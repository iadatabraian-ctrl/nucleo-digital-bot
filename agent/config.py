"""
agent/config.py
----------------
Configuración global y construcción del system prompt.
Carga business.yaml (tono, objetivo, persona), servicios.md y
perfil_cliente.md. Filtra servicios bloqueados antes de armar el prompt.
"""
import os
import re
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Claves de entorno ────────────────────────────────────────────────────────
ANTHROPIC_API_KEY      = os.environ.get("ANTHROPIC_API_KEY", "")
YCLOUD_API_KEY         = os.environ.get("YCLOUD_API_KEY", "")
YCLOUD_PHONE_NUMBER    = os.environ.get("YCLOUD_PHONE_NUMBER", "")
YCLOUD_WEBHOOK_SECRET  = os.environ.get("YCLOUD_WEBHOOK_SECRET", "")
OWNER_WHATSAPP_NUMBER  = os.environ.get("OWNER_WHATSAPP_NUMBER", "")
GOOGLE_CALENDAR_ID     = os.environ.get("GOOGLE_CALENDAR_ID", "")
GROQ_API_KEY           = os.environ.get("GROQ_API_KEY", "")

# ── Rutas de archivos de conocimiento ───────────────────────────────────────
_BASE = Path(__file__).parent.parent / "knowledge"

def _leer(nombre: str) -> str:
    ruta = _BASE / nombre
    if ruta.exists():
        return ruta.read_text(encoding="utf-8").strip()
    return ""

def _leer_yaml(nombre: str) -> dict:
    ruta = _BASE / nombre
    if ruta.exists():
        with open(ruta, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

# ── Helpers de filtrado ──────────────────────────────────────────────────────

def _filtrar_servicios_bloqueados(texto_servicios: str, bloqueados: list[str]) -> str:
    if not bloqueados or not texto_servicios:
        return texto_servicios
    secciones = re.split(r"(?=##\s)", texto_servicios)
    resultado = []
    for seccion in secciones:
        if not any(k in seccion.lower() for k in bloqueados):
            resultado.append(seccion)
    return "\n".join(resultado).strip()

# ── Constructor de system prompt ─────────────────────────────────────────────

def construir_system_prompt(
    slots_disponibles: str = "",
    notas_admin: list[dict] | None = None,
) -> str:
    from agent import memory  # import tardío para evitar circular

    negocio       = _leer_yaml("business.yaml")
    servicios_raw = _leer("servicios.md")
    perfil        = _leer("perfil_cliente.md")

    bloqueados = memory.obtener_servicios_bloqueados()
    servicios  = _filtrar_servicios_bloqueados(servicios_raw, bloqueados)

    nombre   = negocio.get("nombre_negocio", "Nucleo Digital")
    agente   = negocio.get("nombre_agente", "Nexo")
    objetivo = negocio.get("objetivo", "")
    tono     = negocio.get("tono", "")
    horario  = negocio.get("horario_atencion", "Lunes a viernes, 9:00–18:00 (Uruguay)")
    precios  = negocio.get("politica_precios", "")
    fundador = negocio.get("fundador", "Braian")

    prompt = f"""Sos {agente}, el asistente de WhatsApp de {nombre}.

Tu objetivo: {objetivo}

Tono: {tono}

Horario de atención: {horario}

---

REGLAS IMPORTANTES:
- Nunca inventes información que no esté en este contexto.
- Política de precios: {precios}
- Sé breve y natural — mensajes cortos como en WhatsApp real. Nada de textos largos ni listas interminables.
- No uses markdown (asteriscos, guiones, headers) en tus respuestas — es WhatsApp, no un documento.
- Podés usar emojis con moderación para que se lea más natural.
- Si no sabés algo, decilo honestamente y ofrecé que {fundador} lo aclare en la llamada.

---

FLUJO DE CONVERSACIÓN:
1. Escuchás qué necesita el cliente y de qué negocio es.
2. Contás brevemente cómo {nombre} puede ayudar en su caso concreto.
3. Cuando muestre interés real, ofrecés agendar una llamada de descubrimiento gratuita de 30 min con {fundador}.
4. Cuando acepta: pedís su nombre y que elija un horario de los disponibles.
5. Con nombre + fecha + hora confirmados → usás el bloque [AGENDAR_LLAMADA].

---

CALIFICACIÓN DE LEADS:
{perfil}

---

SERVICIOS:
{servicios}
"""

    # Notas del admin
    if notas_admin:
        lineas = [n.get("texto", "") for n in notas_admin if n.get("texto")]
        if lineas:
            prompt += "\n\n---\n\nNOTAS DEL ADMINISTRADOR (válidas para hoy):\n"
            prompt += "\n".join(f"- {l}" for l in lineas)

    # Disponibilidad de agenda
    if slots_disponibles:
        prompt += f"""

---

DISPONIBILIDAD PARA LLAMADAS:
{slots_disponibles}

Cuando tengas nombre + fecha + hora confirmados, agregá al FINAL de tu mensaje este bloque exacto:

[AGENDAR_LLAMADA]
Nombre: <nombre del cliente>
Fecha: <DD/MM/YYYY>
Hora: <HH:MM>
Tema: <una línea con el tema según la conversación>
[/AGENDAR_LLAMADA]

El bloque lo procesa el sistema — el cliente no lo ve. Usalo UNA SOLA VEZ cuando todo esté confirmado.
"""
    else:
        prompt += """

---

AGENDA:
En este momento no hay turnos disponibles. NO ofrezcas fechas ni horarios.
Si el cliente pregunta, decile que le avisás cuando haya disponibilidad.
"""

    return prompt
