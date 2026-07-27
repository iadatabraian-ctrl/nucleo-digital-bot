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


def _dias_habiles(desde: datetime, cantidad: int) -> list[datetime]:
    dias = []
    cursor = desde.replace(hour=0, minute=0, second=0, microsecond=0)
    while len(dias) < cantidad:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            dias.append(cursor)
    return dias


def _slots_del_dia(dia: datetime) -> list[datetime]:
    slots = []
    cursor = TIMEZONE.localize(datetime.combine(dia.date(), HORA_INICIO))
    fin_dia = TIMEZONE.localize(datetime.combine(dia.date(), HORA_FIN))
    delta = timedelta(minutes=DURACION_SLOT_MIN)
    while cursor + delta <= fin_dia:
        slots.append(cursor)
        cursor += delta
    return slots


def _periodos_ocupados(service, calendar_id: str, dias: list[datetime]) -> list[tuple]:
    if not dias:
        return []
    time_min = TIMEZONE.localize(datetime.combine(dias[0].date(), time(0, 0))).isoformat()
    time_max = TIMEZONE.localize(datetime.c
