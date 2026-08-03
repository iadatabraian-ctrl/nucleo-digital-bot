"""
agent/config.py
----------------
Configuración global y construcción del system prompt.
Carga archivos de conocimiento, filtra servicios bloqueados y arma el
prompt completo que recibe Claude en cada llamada.
"""
import os
import re
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

# ── Helpers de filtrado ──────────────────────────────────────────────────────

def _filtrar_servicios_bloqueados(texto_servicios: str, bloqueados: list[str]) -> str:
    """
    Elimina del texto de servicios.md las secciones que contengan
    alguna de las palabras clave bloqueadas.
    Trabaja sección por sección (separadas por líneas en blanco o '##').
    """
    if not bloqueados or not texto_servicios:
        return texto_servicios

    # Dividir en secciones por encabezados ## o líneas vacías consecutivas
    secciones = re.split(r"(?=##\s)", texto_servicios)
    resultado = []
    for seccion in secciones:
        bloqueada = any(k in seccion.lower() for k in bloqueados)
        if not bloqueada:
            resultado.append(seccion)
    return "\n".join(resultado).strip()


# ── Constructor de system prompt ─────────────────────────────────────────────

def construir_system_prompt(
    slots_disponibles: str = "",
    notas_admin: list[dict] | None = None,
) -> str:
    """
    Arma el system prompt completo que se le pasa a Claude.

    Args:
        slots_disponibles: Texto con los próximos slots de calendario disponibles.
                           Si es "", Claude sabe que no hay disponibilidad.
        notas_admin:        Lista de dicts {'texto': str, 'fecha': date|None}
                           provenientes de memory.listar_notas_admin().
    """
    from agent import memory  # import tardío para evitar circular

    perfil      = _leer("perfil_cliente.md")
    servicios_raw = _leer("servicios.md")
    instrucciones = _leer("instrucciones.md")

    # Filtrar servicios bloqueados
    bloqueados = memory.obtener_servicios_bloqueados()
    servicios = _filtrar_servicios_bloqueados(servicios_raw, bloqueados)

    # Construir bloque de notas de admin
    notas_bloque = ""
    if notas_admin:
        lineas = [n.get("texto", "") for n in notas_admin if n.get("texto")]
        if lineas:
            notas_bloque = (
                "\n\n## Notas del administrador (para hoy)\n"
                + "\n".join(f"- {l}" for l in lineas)
            )

    # Bloque de disponibilidad de agenda
    if slots_disponibles:
        agenda_bloque = (
            "\n\n## Slots disponibles para llamadas\n"
            + slots_disponibles
            + "\n\nSi el cliente quiere agendar una llamada, usa EXACTAMENTE este formato:\n"
            "[AGENDAR_LLAMADA]\n"
            "Nombre: <nombre del cliente>\n"
            "Fecha: <DD/MM/YYYY>\n"
            "Hora: <HH:MM>\n"
            "Tema: <tema breve>\n"
            "[/AGENDAR_LLAMADA]\n"
            "Coloca el bloque al final de tu mensaje. El texto visible para el cliente "
            "va ANTES del bloque."
        )
    else:
        agenda_bloque = (
            "\n\n## Agenda\n"
            "En este momento no hay turnos disponibles. "
            "NO ofrezcas fechas ni horarios de ningún tipo. "
            "Si el cliente pregunta, dile que te avisará cuando haya disponibilidad."
        )

    partes = [p for p in [perfil, instrucciones, servicios] if p]
    prompt = "\n\n---\n\n".join(partes)
    prompt += notas_bloque
    prompt += agenda_bloque

    return prompt
