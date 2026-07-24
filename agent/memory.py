"""
agent/memory.py
----------------
Historial de conversación por número de WhatsApp — ventana deslizante en RAM.
Si un día se necesita persistencia entre reinicios de Render, se cambia esto
por SQLite sin tocar el resto del código.
"""
from collections import defaultdict, deque

VENTANA = 20  # mensajes a recordar por cliente

_historial = defaultdict(lambda: deque(maxlen=VENTANA))


def agregar_mensaje(numero: str, rol: str, contenido: str):
    """rol: 'user' o 'assistant'"""
    _historial[numero].append({"role": rol, "content": contenido})


def obtener_historial(numero: str) -> list:
    return list(_historial[numero])


def limpiar_historial(numero: str):
    _historial[numero].clear()
