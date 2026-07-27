"""
agent/memory.py
----------------
Historial de conversación por número de WhatsApp — ventana deslizante en RAM.
Si un día se necesita persistencia entre reinicios de Render, se cambia esto
por SQLite sin tocar el resto del código.

También trackea pausas manuales: cuando el dueño responde por su cuenta desde
la app de WhatsApp Business (Coexistence), el bot deja de contestar a ESE
cliente puntual hasta que pase el tiempo configurado o el dueño lo reactive.
"""
from collections import defaultdict, deque
from datetime import datetime, timedelta

VENTANA = 20  # mensajes a recordar por cliente
PAUSA_HORAS_DEFAULT = 3  # horas que el bot se queda callado tras una respuesta manual

_historial = defaultdict(lambda: deque(maxlen=VENTANA))
_pausas: dict[str, datetime] = {}  # numero -> momento hasta el cual está pausado


def agregar_mensaje(numero: str, rol: str, contenido: str):
    """rol: 'user' o 'assistant'"""
    _historial[numero].append({"role": rol, "content": contenido})


def obtener_historial(numero: str) -> list:
    return list(_historial[numero])


def limpiar_historial(numero: str):
    _historial[numero].clear()


def pausar_conversacion(numero: str, horas: float = PAUSA_HORAS_DEFAULT):
    """El dueño tomó la conversación manualmente — el bot se calla para este número."""
    _pausas[numero] = datetime.utcnow() + timedelta(hours=horas)


def reanudar_conversacion(numero: str):
    """Reactiva el bot para este número de inmediato (comando manual del dueño)."""
    _pausas.pop(numero, None)


def esta_pausada(numero: str) -> bool:
    """True si el bot debe quedarse callado para este número ahora mismo."""
    vence = _pausas.get(numero)
    if vence is None:
        return False
    if datetime.utcnow() >= vence:
        _pausas.pop(numero, None)  # ya venció, se limpia sola
        return False
    return True
