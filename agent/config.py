"""
agent/config.py
----------------
Carga knowledge/business.yaml + knowledge/servicios.md + knowledge/perfil_cliente.md
y construye el system prompt para Claude. También expone las variables de entorno.

Para cambiar el comportamiento del bot sin tocar código:
→ Editá knowledge/business.yaml (tono, objetivo, etc.)
→ Editá knowledge/servicios.md (servicios, precios, casos de uso)
→ Editá knowledge/perfil_cliente.md (criterio de qué negocios calificar)
"""
import os
import yaml
from pathlib import Path
from datetime import datetime
import pytz
from dotenv import load_dotenv
from agent import memory

load_dotenv()

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"

def cargar_negocio() -> dict:
    path = KNOWLEDGE_DIR / "business.yaml"
    if not path.exists():
        raise FileNotFoundError("No existe knowledge/business.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

def cargar_servicios() -> str:
    """
    Carga knowledge/servicios.md y saca las secciones enteras de servicios
    que estén bloqueados (ver memory.obtener_servicios_bloqueados). El
    archivo separa cada servicio con una línea "---", así que si una
    sección menciona una palabra clave bloqueada se descarta completa
    (título + descripción) — Claude no se queda con la información para
    poder ofrecerlo, en vez de depender de que "recuerde" no mencionarlo.
    """
    path = KNOWLEDGE_DIR / "servicios.md"
    if not path.exists():
        return ""
    texto = path.read_text(encoding="utf-8")

    bloqueados = memory.obtener_servicios_bloqueados()
    if not bloqueados:
        return texto

    secciones = texto.split("\n---\n")
    secciones_filtradas = [
        s for s in secciones
        if not any(kw in s.lower() for kw in bloqueados)
    ]
    return "\n---\n".join(secciones_filtradas)

def cargar_perfil_cliente() -> str:
    path = KNOWLEDGE_DIR / "perfil_cliente.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""

def construir_system_prompt(slots_disponibles: str = "", notas_admin: list[dict] | None = None) -> str:
    negocio = cargar_negocio()
    servicios = cargar_servicios()
    perfil_cliente = cargar_perfil_cliente()
    notas_admin = notas_admin or []
    servicios_bloqueados = memory.obtener_servicios_bloqueados()

    if notas_admin:
        hoy_str = datetime.now(pytz.timezone("America/Montevideo")).strftime("%d/%m")
        lineas = []
        for n in notas_admin:
            fecha = n.get("fecha")
            if fecha is None:
                lineas.append(f"- (sin fecha, aplica siempre): {n['texto']}")
            else:
                lineas.append(f"- Para el {fecha.strftime('%d/%m')}: {n['texto']}")
        bloque_notas = "\n".join(lineas)
        seccion_avisos = f"""
---

AVISOS DEL DUEÑO/ADMINISTRADOR (MÁXIMA PRIORIDAD — hoy es {hoy_str}):
{bloque_notas}

REGLAS PARA APLICAR ESTOS AVISOS:
1. Los avisos SIN fecha aplican SIEMPRE, en toda conversación, hasta que se borren.
2. Los avisos CON fecha: si el texto del aviso dice "hasta" (ej: "hasta el viernes", "no agendes hasta el jueves"), la restricción aplica desde HOY hasta esa fecha inclusive — no solo ese día puntual. Si el aviso NO dice "hasta" (ej: "no tiene llamada el jueves"), aplica SOLO en esa fecha específica. En ambos casos, si el cliente pregunta con anticipación, ya aplicá el aviso.
3. Estos avisos PISAN COMPLETAMENTE las secciones DISPONIBILIDAD e INFORMACIÓN DEL NEGOCIO. Si contradicen algo de esas secciones, gana el aviso, sin excepción.
4. Si un aviso prohíbe o restringe el agendamiento de llamadas (ej: "no agendes", "sin llamadas", "hasta el X"):
   - NO ofrezcas horarios disponibles, aunque los veas en la sección DISPONIBILIDAD.
   - NO invites al cliente a agendar ni menciones que hay slots libres.
   - Si el cliente pide agendar o pregunta cuándo puede llamar, decile simplemente: "Por el momento no estamos tomando llamadas nuevas, le aviso cuando tengamos disponibilidad 😊" (o similar, sin inventar fechas).
5. Si un aviso prohíbe ofrecer un servicio, no lo menciones aunque el cliente lo pida directamente.

"""
    else:
        seccion_avisos = ""

    if servicios_bloqueados:
        lista_bloqueados = ", ".join(servicios_bloqueados)
        seccion_servicios_bloqueados = f"""
---

SERVICIOS QUE NO SE OFRECEN ACTUALMENTE: {lista_bloqueados}.
Si el cliente pregunta por alguno de estos (aunque lo pida directamente),
respondé con naturalidad que por ahora no lo estás ofreciendo — sin dar
explicaciones técnicas ni mencionar que es una restricción del sistema — y
redirigí la conversación hacia lo que sí ofrecés.

"""
    else:
        seccion_servicios_bloqueados = ""

    texto_disponibilidad = slots_disponibles or (
        "⛔ Sin disponibilidad activa. NO hay horarios para ofrecer — ni esta semana, "
        "ni la próxima, ni ningún día puntual. NO inventes ni repitas fechas que hayas "
        "mostrado antes en la conversación. Si el cliente pide agendar o pregunta por "
        "un día específico (hoy, mañana, cualquier fecha), respondé SOLO con algo como: "
        '"Por el momento no estamos tomando llamadas nuevas, le aviso apenas tengamos '
        'disponibilidad 😊" — sin mencionar ningún día ni semana concreta.'
    )

    prompt = f"""Tu nombre es Nexo. Sos el asistente de WhatsApp de {negocio.get('nombre_negocio', 'El Núcleo Digital')}.
Presentate siempre como "Nexo", nunca como "Sos Nexo" ni ninguna otra variante.

Tu objetivo: {negocio.get('objetivo', '')}

Tono: {negocio.get('tono', '')}

Horario de atención: {negocio.get('horario_atencion', 'Lunes a viernes, 11:00-18:00 Uruguay')}