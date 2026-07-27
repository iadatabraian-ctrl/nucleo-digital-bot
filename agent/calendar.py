import os
import json
import base64
from datetime import datetime, timedelta, time
import pytz

TIMEZONE = pytz.timezone("America/Montevideo")
HORA_INICIO = time(9, 0)
HORA_FIN = time(18, 0)
DURACION_SLOT_MIN = 45
MARGEN_MIN = 60


def _get_service():
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError("Faltan dependencias de Google.")
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON no configurada.")
    try:
        service_account_info = json.loads(raw)
    except json.JSONDecodeError:
        service_account_info = json.loads(base64.b64decode(raw).decode())
    scopes = ["https://www.googleapis.com/auth/calendar"]
    creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _dias_habiles(desde, cantidad):
    dias = []
    cursor = desde.replace(hour=0, minute=0, second=0, microsecond=0)
    while len(dias) < cantidad:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            dias.append(cursor)
    return dias


def _slots_del_dia(dia):
    slots = []
    cursor = TIMEZONE.localize(datetime.combine(dia.date(), HORA_INICIO))
    fin_dia = TIMEZONE.localize(datetime.combine(dia.date(), HORA_FIN))
    delta = timedelta(minutes=DURACION_SLOT_MIN)
    while cursor + delta <= fin_dia:
        slots.append(cursor)
        cursor += delta
    return slots


def _periodos_ocupados(service, calendar_id, dias):
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


def _slot_libre(slot, ocupados):
    slot_utc = slot.astimezone(pytz.utc)
    fin_slot = slot_utc + timedelta(minutes=DURACION_SLOT_MIN)
    for inicio, fin in ocupados:
        if slot_utc < fin and fin_slot > inicio:
            return False
    return True


def obtener_slots_disponibles(dias=5):
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "")
    if not calendar_id or not os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""):
        return (
            "El sistema de calendario no está configurado. "
            "Cuando el cliente quiera agendar, decile que Braian lo va a contactar."
        )
    try:
        service = _get_service()
        ahora = datetime.now(TIMEZONE)
        margen = ahora + timedelta(minutes=MARGEN_MIN)
        proximos_dias = _dias_habiles(ahora, dias)
        ocupados = _periodos_ocupados(service, calendar_id, proximos_dias)
        slots_por_dia = {}
        traducciones = {
            "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miercoles",
            "Thursday": "Jueves", "Friday": "Viernes",
        }
        for dia in proximos_dias:
            nombre_dia = dia.strftime("%A %d/%m").capitalize()
            for en, es in traducciones.items():
                nombre_dia = nombre_dia.replace(en, es)
            for slot in _slots_del_dia(dia):
                if slot <= margen:
                    continue
                if _slot_libre(slot, ocupados):
                    hora = slot.strftime("%H:%M")
                    slots_por_dia.setdefault(nombre_dia, []).append(hora)
        if not any(slots_por_dia.values()):
            return "No hay slots disponibles. Braian se va a poner en contacto."
        lineas = ["Horarios disponibles (Uruguay, GMT-3):"]
        for dia, horas in slots_por_dia.items():
            if horas:
                lineas.append(f"- {dia}: {', '.join(horas)}")
        return "\n".join(lineas)
    except Exception as e:
        print(f"[calendar] Error obteniendo disponibilidad: {e}")
        return "No pude verificar disponibilidad. Braian lo va a contactar."


def crear_evento(fecha_str, hora_str, nombre_cliente, numero_cliente, tema=""):
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "")
    if not calendar_id or not os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""):
        print("[calendar] No configurado.")
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
        hoy = datetime.now(TIMEZONE).date()
        if fecha < hoy:
            fecha = fecha.replace(year=hoy.year)
        if fecha < hoy:
            fecha = fecha.replace(year=hoy.year + 1)
        hora = datetime.strptime(hora_str, "%H:%M").time()
        inicio = TIMEZONE.localize(datetime.combine(fecha, hora))
        fin = inicio + timedelta(minutes=DURACION_SLOT_MIN)
        descripcion = f"Cliente WhatsApp: {numero_cliente}"
        if tema:
            descripcion += f"\nTema: {tema}"
        descripcion += "\n\nAgendado automaticamente por el bot de Nucleo Digital."
        evento = {
            "summary": f"Llamada Nucleo - {nombre_cliente}",
            "description": descripcion,
            "start": {"dateTime": inicio.isoformat(), "timeZone": "America/Montevideo"},
            "end": {"dateTime": fin.isoformat(), "timeZone": "America/Montevideo"},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 30},
                    {"method": "email", "minutes": 60},
                ],
            },
        }
        service.events().insert(calendarId=calendar_id, body=evento).execute()
        print(f"[calendar] Evento creado: {inicio.strftime('%d/%m/%Y %H:%M')} - {nombre_cliente}")
        return True
    except Exception as e:
        print(f"[calendar] Error creando evento: {e}")
        return False
