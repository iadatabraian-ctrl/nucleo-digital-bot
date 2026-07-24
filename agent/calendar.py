"""
agent/calendar.py
------------------
Integración con Google Calendar para:
  1. Obtener slots disponibles en los próximos N días hábiles
  2. Crear un evento cuando se agenda una llamada

Usa una Service Account de Google (sin OAuth interactivo — funciona en Render).

Setup del lado de Google:
  1. Crear Service Account en console.cloud.google.com (o IAM & Admin)
  2. Descargar el JSON de credenciales
  3. Compartir el calendario de Braian con el email de la Service Account
     (con permiso "Hacer cambios en eventos")
  4. Pegar el JSON completo como valor de la variable GOOGLE_SERVICE_ACCOUNT_JSON en Render
  5. Poner el ID del calendario en GOOGLE_CALENDAR_ID (normalmente tu Gmail)

Manejo de disponibilidad sin código:
  Braian solo usa Google Calendar normal. Si tiene algo ocupado, el bot no
  ofrece ese horario. Simple.
"""
import os
import json
import base64
from datetime import datetime, timedelta, time
import pytz

TIMEZONE = pytz.timezone("America/Montevideo")

# Horario de disponibilidad (hora local Uruguay)
HORA_INICIO = time(9, 0)
HORA_FIN = time(18, 0)
DURACION_SLOT_MIN = 45  # minutos por llamada

# Margen mínimo para ofrecer slots (no ofrecer algo en los próximos 60 min)
MARGEN_MIN = 60


def _get_service():
    """Inicializa el cliente de Google Calendar con la Service Account."""
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            "Faltan dependencias de Google. Instalar: google-auth google-api-python-client"
        )

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise ValueError("Variable GOOGLE_SERVICE_ACCOUNT_JSON no configurada.")

    # Acepta JSON directo o base64
    try:
        service_account_info = json.loads(raw)
    except json.JSONDecodeError:
        service_account_info = json.loads(base64.b64decode(raw).decode())

    scopes = ["https://www.googleapis.com/auth/calendar"]
    creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _dias_habiles(desde: datetime, cantidad: int) -> list[datetime]:
    """Devuelve `cantidad` fechas de días hábiles (lun-vie) desde `desde`."""
    dias = []
    cursor = desde.replace(hour=0, minute=0, second=0, microsecond=0)
    while len(dias) < cantidad:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:  # 0=lunes … 4=viernes
            dias.append(cursor)
    return dias


def _slots_del_dia(dia: datetime) -> list[datetime]:
    """
    Genera todos los slots posibles dentro del horario de atención para un día.
    Cada slot = inicio de una llamada de DURACION_SLOT_MIN minutos.
    """
    slots = []
    cursor = TIMEZONE.localize(
        datetime.combine(dia.date(), HORA_INICIO)
    )
    fin_dia = TIMEZONE.localize(
        datetime.combine(dia.date(), HORA_FIN)
    )
    delta = timedelta(minutes=DURACION_SLOT_MIN)
    while cursor + delta <= fin_dia:
        slots.append(cursor)
        cursor += delta
    return slots


def _periodos_ocupados(service, calendar_id: str, dias: list[datetime]) -> list[tuple]:
    """
    Consulta freebusy de Google Calendar para los días dados.
    Devuelve lista de (inicio, fin) como datetimes UTC.
    """
    if not dias:
        return []

    time_min = TIMEZONE.localize(
        datetime.combine(dias[0].date(), time(0, 0))
    ).isoformat()
    time_max = TIMEZONE.localize(
        datetime.combine(dias[-1].date(), time(23, 59))
    ).isoformat()

    body = {
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": calendar_id}],
    }
    result = service.freebusy().query(body=body).execute()
    busy_raw = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])

    ocupados = []
    for bloque in busy_raw:
        inicio = datetime.fromisoformat(bloque["start"].replace("Z", "+00:00"))
        fin = datetime.fromisoformat(bloque["end"].replace("Z", "+00:00"))
        ocupados.append((inicio, fin))
    return ocupados


def _slot_libre(slot: datetime, ocupados: list[tuple]) -> bool:
    slot_utc = slot.astimezone(pytz.utc)
    fin_slot = slot_utc + timedelta(minutes=DURACION_SLOT_MIN)
    for inicio, fin in ocupados:
        if slot_utc < fin and fin_slot > inicio:
            return False
    return True


def obtener_slots_disponibles(dias: int = 5) -> str:
    """
    Devuelve un texto formateado con los slots disponibles para los próximos
    `dias` días hábiles. Se inyecta en el system prompt de Claude.

    Si Google Calendar no está configurado, devuelve un aviso para que el bot
    le diga al usuario que contacte directamente a Braian.
    """
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "")
    if not calendar_id or not os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""):
        return (
            "⚠️ El sistema de calendario no está configurado todavía. "
            "Cuando el cliente quiera agendar, decile que Braian lo va a contactar "
            "directamente para coordinar el horario."
        )

    try:
        service = _get_service()
        ahora = datetime.now(TIMEZONE)
        margen = ahora + timedelta(minutes=MARGEN_MIN)
        proximos_dias = _dias_habiles(ahora, dias)
        ocupados = _periodos_ocupados(service, calendar_id, proximos_dias)

        slots_por_dia: dict[str, list[str]] = {}
        for dia in proximos_dias:
            nombre_dia = dia.strftime("%A %d/%m").capitalize()
            traducciones = {
                "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
                "Thursday": "Jueves", "Friday": "Viernes",
            }
            for en, es in traducciones.items():
                nombre_dia = nombre_dia.replace(en, es)

            for slot in _slots_del_dia(dia):
                if slot <= margen:
                    continue
                if _slot_libre(slot, ocupados):
                    hora = slot.strftime("%H:%M")
                    slots_por_dia.setdefault(nombre_dia, []).append(hora)

        if not any(slots_por_dia.values()):
            return (
                "No hay slots disponibles en los próximos días hábiles. "
                "Decile al cliente que Braian se va a poner en contacto para "
                "coordinar un horario alternativo."
            )

        lineas = ["Horarios disponibles para agendar una llamada (Uruguay, GMT-3):"]
        for dia, horas in slots_por_dia.items():
            if horas:
                lineas.append(f"• {dia}: {', '.join(horas)}")
        return "\n".join(lineas)

    except Exception as e:
        print(f"[calendar] Error obteniendo disponibilidad: {e}")
        return (
            "No pude verificar la disponibilidad en este momento. "
            "Cuando el cliente quiera agendar, decile que Braian lo va a contactar "
            "directamente para coordinar."
        )


def crear_evento(
    fecha_str: str,
    hora_str: str,
    nombre_cliente: str,
    numero_cliente: str,
    tema: str = "",
) -> bool:
    """
    Crea un evento en Google Calendar para la llamada de descubrimiento.

    Args:
        fecha_str:     "DD/MM/YYYY" o "YYYY-MM-DD"
        hora_str:      "HH:MM"
        nombre_cliente: nombre del cliente (puede ser su número si no dio nombre)
        numero_cliente: número de WhatsApp del cliente
        tema:          resumen del tema que quiere tratar (opcional)

    Returns True si se creó OK, False si falló.
    """
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "")
    if not calendar_id or not os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""):
        print("[calendar] No configurado — no se creó el evento.")
        return False

    try:
        service = _get_service()

        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                fecha = datetime.strptime(fecha_str, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Formato de fecha no reconocido: {fecha_str}")

        hora = datetime.strptime(hora_str, "%H:%M").time()
        inicio = TIMEZONE.localize(datetime.combine(fecha, hora))
        fin = inicio + timedelta(minutes=DURACION_SLOT_MIN)

        descripcion = f"Cliente WhatsApp: {numero_cliente}"
        if tema:
            descripcion += f"\nTema: {tema}"
        descripcion += "\n\nAgendado automáticamente por el bot de Nucleo Digital."

        evento = {
            "summary": f"📞 Llamada Nucleo — {nombre_cliente}",
            "description": descripcion,
            "start": {
                "dateTime": inicio.isoformat(),
                "timeZone": "America/Montevideo",
            },
            "end": {
                "dateTime": fin.isoformat(),
                "timeZone": "America/Montevideo",
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 30},
                    {"method": "email", "minutes": 60},
                ],
            },
        }

        service.events().insert(calendarId=calendar_id, body=evento).execute()
        print(f"[calendar] Evento creado: {inicio.strftime('%d/%m %H:%M')} — {nombre_cliente}")
        return True

    except Exception as e:
        print(f"[calendar] Error creando evento: {e}")
        return False
