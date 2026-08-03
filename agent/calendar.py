"""
agent/calendar.py
------------------
Interacción con Google Calendar usando una cuenta de servicio
(sin OAuth interactivo, ideal para servidores sin interfaz).

Variables de entorno requeridas:
  GOOGLE_CALENDAR_ID      — ID del calendario (ej. xxx@group.calendar.google.com)
  GOOGLE_SERVICE_ACCOUNT  — JSON completo de la clave de la cuenta de servicio
                            (como string, no como ruta de archivo)

Características:
  - obtener_slots_disponibles(): respeta overrides de horario en Redis
  - horario_permitido(): valida si una fecha/hora propuesta por Claude es válida
  - crear_evento(): crea el evento en Google Calendar
"""
import json
import os
from datetime import date, datetime, time, timedelta

import pytz
from google.oauth2 import service_account
from googleapiclient.discovery import build

from agent import config

TIMEZONE = pytz.timezone("America/Montevideo")
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Horario de atención por defecto (lunes a viernes)
_HORA_INICIO_DEFAULT = time(9, 0)
_HORA_FIN_DEFAULT    = time(18, 0)
_DURACION_SLOT_MIN   = 60
_DIAS_HABILES        = {0, 1, 2, 3, 4}  # lunes=0 … viernes=4


def _get_service():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT no configurada")
    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _ahora() -> datetime:
    return datetime.now(TIMEZONE)


def _inicio_dia(d: date) -> datetime:
    return TIMEZONE.localize(datetime(d.year, d.month, d.day, 0, 0, 0))


def _fin_dia(d: date) -> datetime:
    return TIMEZONE.localize(datetime(d.year, d.month, d.day, 23, 59, 59))


# ── Overrides de horario (desde Redis vía memory) ────────────────────────────

def _ventana_dia(d: date) -> tuple[time, time] | None:
    """
    Devuelve (hora_inicio, hora_fin) para el día considerando overrides.
    Devuelve None si el día está cerrado.
    """
    from agent import memory  # tardío para evitar circular

    override = memory.obtener_horario_override(d)
    if override:
        if override.get("cerrado"):
            return None
        desde_str = override.get("desde")
        hasta_str = override.get("hasta")
        if desde_str and hasta_str:
            try:
                h_ini = time(*[int(x) for x in desde_str.split(":")])
                h_fin = time(*[int(x) for x in hasta_str.split(":")])
                return h_ini, h_fin
            except (ValueError, TypeError):
                pass

    # Sin override: usar horario por defecto solo en días hábiles
    if d.weekday() not in _DIAS_HABILES:
        return None
    return _HORA_INICIO_DEFAULT, _HORA_FIN_DEFAULT


# ── Validación de horario propuesto por Claude ───────────────────────────────

def horario_permitido(fecha: date, hora: time) -> bool:
    """
    Retorna True si el slot propuesto (fecha, hora) está dentro de la
    ventana permitida para ese día (considerando overrides en Redis).
    """
    ventana = _ventana_dia(fecha)
    if ventana is None:
        return False
    h_ini, h_fin = ventana
    return h_ini <= hora < h_fin


# ── Obtener slots libres ─────────────────────────────────────────────────────

def obtener_slots_disponibles(dias: int = 5) -> str:
    """
    Devuelve un string con los próximos slots libres, considerando:
    - Overrides de horario en Redis
    - Eventos ya existentes en Google Calendar
    """
    try:
        service = _get_service()
        cal_id  = config.GOOGLE_CALENDAR_ID

        ahora  = _ahora()
        inicio = ahora + timedelta(hours=1)  # al menos 1h de margen
        fin    = ahora + timedelta(days=dias + 1)

        # Obtener eventos existentes en el rango
        eventos_resp = service.events().list(
            calendarId=cal_id,
            timeMin=inicio.isoformat(),
            timeMax=fin.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        eventos = eventos_resp.get("items", [])

        # Construir set de horas ocupadas {(date, hour)}
        ocupados: set[tuple[date, int]] = set()
        for ev in eventos:
            inicio_ev_str = ev.get("start", {}).get("dateTime")
            if not inicio_ev_str:
                continue
            try:
                dt = datetime.fromisoformat(inicio_ev_str.replace("Z", "+00:00"))
                dt_local = dt.astimezone(TIMEZONE)
                ocupados.add((dt_local.date(), dt_local.hour))
            except (ValueError, TypeError):
                continue

        # Generar slots disponibles
        slots = []
        cursor_dia = inicio.date()
        while cursor_dia <= fin.date() and len(slots) < 10:
            ventana = _ventana_dia(cursor_dia)
            if ventana is None:
                cursor_dia += timedelta(days=1)
                continue
            h_ini, h_fin = ventana
            hora_actual = h_ini.hour
            while hora_actual < h_fin.hour:
                slot_dt = TIMEZONE.localize(
                    datetime(cursor_dia.year, cursor_dia.month, cursor_dia.day, hora_actual, 0)
                )
                if slot_dt > inicio and (cursor_dia, hora_actual) not in ocupados:
                    slots.append(
                        slot_dt.strftime("- %A %d/%m/%Y a las %H:%M hs")
                    )
                hora_actual += _DURACION_SLOT_MIN // 60

            cursor_dia += timedelta(days=1)

        if not slots:
            return ""
        return "\n".join(slots)

    except Exception as e:
        print(f"[calendar] Error obteniendo slots: {e}")
        return ""


# ── Crear evento ─────────────────────────────────────────────────────────────

def crear_evento(
    fecha_str: str,
    hora_str: str,
    nombre_cliente: str,
    numero_cliente: str,
    tema: str,
) -> bool:
    """
    Crea un evento de 1h en Google Calendar.
    fecha_str: DD/MM/YYYY  |  hora_str: HH:MM
    Retorna True si se creó correctamente.
    """
    try:
        service = _get_service()
        cal_id  = config.GOOGLE_CALENDAR_ID

        # Parsear fecha y hora
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                fecha_obj = datetime.strptime(fecha_str, fmt).date()
                break
            except ValueError:
                continue
        else:
            print(f"[calendar] Fecha inválida: {fecha_str}")
            return False

        hora_obj = datetime.strptime(hora_str, "%H:%M").time()

        inicio_dt = TIMEZONE.localize(
            datetime(fecha_obj.year, fecha_obj.month, fecha_obj.day,
                     hora_obj.hour, hora_obj.minute)
        )
        fin_dt = inicio_dt + timedelta(hours=1)

        evento = {
            "summary": f"Llamada con {nombre_cliente}",
            "description": (
                f"Cliente: {nombre_cliente}\n"
                f"WhatsApp: {numero_cliente}\n"
                f"Tema: {tema}"
            ),
            "start": {"dateTime": inicio_dt.isoformat(), "timeZone": str(TIMEZONE)},
            "end":   {"dateTime": fin_dt.isoformat(),    "timeZone": str(TIMEZONE)},
        }

        service.events().insert(calendarId=cal_id, body=evento).execute()
        print(f"[calendar] Evento creado: {nombre_cliente} {fecha_str} {hora_str}")
        return True

    except Exception as e:
        print(f"[calendar] Error creando evento: {e}")
        return False
