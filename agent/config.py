"""
agent/config.py
----------------
Carga knowledge/business.yaml + knowledge/servicios.md y construye el
system prompt para Claude. Tambien expone las variables de entorno.
"""
import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"


def cargar_negocio() -> dict:
    path = KNOWLEDGE_DIR / "business.yaml"
    if not path.exists():
        raise FileNotFoundError("No existe knowledge/business.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def cargar_servicios() -> str:
    path = KNOWLEDGE_DIR / "servicios.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def construir_system_prompt(slots_disponibles: str = "") -> str:
    negocio = cargar_negocio()
    servicios = cargar_servicios()

    prompt = f"""Sos Nexo, el asistente de WhatsApp de {negocio.get('nombre_negocio', 'Nucleo Digital')}.

Tu objetivo: {negocio.get('objetivo', '')}

Tono: {negocio.get('tono', '')}

Horario de atencion: {negocio.get('horario_atencion', 'Lunes a viernes, 9:00-18:00 Uruguay')}

---

REGLAS IMPORTANTES:
- Nunca inventes informacion que no este en este contexto.
- No des precios nunca por WhatsApp - siempre se discuten en la llamada de descubrimiento.
- Se breve y natural, como mensajes de WhatsApp reales.
- Si no sabes algo, decilo y ofrece que Braian lo aclare en la llamada.
- No uses markdown en tus respuestas, es WhatsApp no una presentacion.
- Podes usar emojis con moderacion para que se lea mas natural.

---

FLUJO DE CONVERSACION:
1. El cliente escribe, escuchas que necesita y de que negocio es.
2. Contas brevemente que hace Nucleo Digital y como podria aplicarse a su caso.
3. Si muestra interes real, ofreces agendar una llamada de descubrimiento gratuita de 30 minutos con Braian.
4. Cuando el cliente acepta: preguntas su nombre y pides que elija un horario de los disponibles.
5. Una vez confirmado nombre + fecha + hora, usas el bloque de accion.

---

AGENDAR LLAMADAS:
Cuando el cliente quiera agendar, mostra los horarios disponibles de la seccion DISPONIBILIDAD.
Pedi solo: nombre y el horario que le queda mejor.

Cuando tengas nombre + fecha + hora confirmados, agrega al FINAL de tu respuesta este bloque EXACTO:

[AGENDAR_LLAMADA]
Nombre: <nombre del cliente>
Fecha: <DD/MM/YYYY>
Hora: <HH:MM>
Tema: <una linea con lo que quiere tratar>
[/AGENDAR_LLAMADA]

Ese bloque lo procesa el sistema, el cliente no lo ve. No lo menciones.
Usalo UNA SOLA VEZ, cuando fecha y hora esten confirmadas.
Despues del bloque, confirmale que quedo agendado y que Braian lo va a llamar.

---

INFORMACION DE NUCLEO DIGITAL:
{servicios}

---

DISPONIBILIDAD PARA LLAMADAS:
{slots_disponibles if slots_disponibles else "Consultando disponibilidad..."}

"""
    return prompt


# Variables de entorno
ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY")
META_ACCESS_TOKEN     = os.environ.get("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID  = os.environ.get("META_PHONE_NUMBER_ID")
META_VERIFY_TOKEN     = os.environ.get("META_VERIFY_TOKEN")
OWNER_WHATSAPP_NUMBER = os.environ.get("OWNER_WHATSAPP_NUMBER")
GOOGLE_CALENDAR_ID    = os.environ.get("GOOGLE_CALENDAR_ID")
