"""
agent/memory.py
----------------
Historial de conversación por número de WhatsApp — ventana deslizante en RAM.
Si un día se necesita persistencia entre reinicios de Render, se cambia esto
por SQLite sin tocar el resto del código.

También trackea pausas manuales: cuando el dueño responde por su cuenta desde
la app de WhatsApp Business (Coexistence), el bot deja de contestar a ESE
cliente puntual — por un tiempo (pausa corta) o indefinidamente (pausa
permanente, para chats que se quieren mantener 100% manuales, como
prospección en frío) hasta que el dueño lo reactive con un comando.
"""
import re
from collections import defaultdict, deque
from datetime import datetime, date, timedelta
import pytz

TIMEZONE = pytz.timezone("America/Montevideo")

VENTANA = 20  # mensajes a recordar por cliente
PAUSA_HORAS_DEFAULT = 3  # horas que el bot se queda callado tras una respuesta manual

_historial = defaultdict(lambda: deque(maxlen=VENTANA))
# numero -> datetime (vence en ese momento) o None (pausa indefinida/permanente)
_pausas: dict[str, datetime | None] = {}


def agregar_mensaje(numero: str, rol: str, contenido: str):
    """rol: 'user' o 'assistant'"""
    _historial[numero].append({"role": rol, "content": contenido})


def obtener_historial(numero: str) -> list:
    return list(_historial[numero])


def limpiar_historial(numero: str):
    _historial[numero].clear()


def pausar_conversacion(numero: str, horas: float = PAUSA_HORAS_DEFAULT):
    """El dueño tomó la conversación manualmente — el bot se calla por un tiempo."""
    _pausas[numero] = datetime.utcnow() + timedelta(hours=horas)


def pausar_conversacion_indefinida(numero: str):
    """
    Pausa permanente — para chats que se quieren mantener 100% manuales
    (ej: prospección en frío desde el número de la agencia). No vence solo;
    hace falta reanudar_conversacion() para que el bot vuelva a responder ahí.
    """
    _pausas[numero] = None


def reanudar_conversacion(numero: str):
    """Reactiva el bot para este número de inmediato (comando manual del dueño)."""
    _pausas.pop(numero, None)


def esta_pausada(numero: str) -> bool:
    """True si el bot debe quedarse callado para este número ahora mismo."""
    if numero not in _pausas:
        return False
    vence = _pausas[numero]
    if vence is None:
        return True  # pausa indefinida
    if datetime.utcnow() >= vence:
        _pausas.pop(numero, None)  # ya venció, se limpia sola
        return False
    return True


# ── Notas de administrador ──────────────────────────────────────────────────
# El dueño le escribe directo al número del bot (se detecta porque el remitente
# es OWNER_WHATSAPP_NUMBER) y ese mensaje queda como una instrucción para el
# bot. Si el texto menciona un día ("mañana", "hoy", "el jueves", "28/07"),
# la nota se aplica SOLO ese día y se borra sola al pasar — no hace falta que
# el dueño se acuerde de limpiarla. Si no menciona ningún día (ej: "no
# vendemos más el modelo X"), queda indefinida hasta /limpiar.
_DIAS_SEMANA = {
    "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2,
    "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6,
}

_notas_admin: list[dict] = []  # [{"texto": str, "fecha": date | None}, ...]


def _hoy() -> date:
    return datetime.now(TIMEZONE).date()


def _resolver_fecha_mencionada(texto: str) -> date | None:
    """Intenta detectar a qué día se refiere el texto. None = sin referencia (indefinida)."""
    t = texto.lower()
    hoy = _hoy()

    if re.search(r"\bhoy\b", t):
        return hoy
    if re.search(r"\bma(ñ|n)ana\b", t):
        return hoy + timedelta(days=1)

    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", t)
    if m:
        dia, mes = int(m.group(1)), int(m.group(2))
        try:
            fecha = date(hoy.year, mes, dia)
            if fecha < hoy:
                fecha = date(hoy.year + 1, mes, dia)
            return fecha
        except ValueError:
            pass

    for nombre, idx in _DIAS_SEMANA.items():
        if re.search(rf"\b{nombre}\b", t):
            delta = (idx - hoy.weekday()) % 7  # 0 = hoy mismo es ese día
            return hoy + timedelta(days=delta)

    return None  # no se menciona ningún día -> indefinida


def agregar_nota_admin(texto: str) -> date | None:
    """Guarda la nota y devuelve la fecha a la que quedó asociada (o None si es indefinida)."""
    _limpiar_notas_vencidas()
    fecha = _resolver_fecha_mencionada(texto)
    _notas_admin.append({"texto": texto, "fecha": fecha})
    return fecha


def _limpiar_notas_vencidas():
    hoy = _hoy()
    _notas_admin[:] = [n for n in _notas_admin if n["fecha"] is None or n["fecha"] >= hoy]


def obtener_notas_admin() -> list[str]:
    _limpiar_notas_vencidas()
    hoy = _hoy()
    return [n["texto"] for n in _notas_admin if n["fecha"] is None or n["fecha"] == hoy]


def limpiar_notas_admin():
    _notas_admin.clear()
