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
from collections import defaultdict, deque
from datetime import datetime, timedelta

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
# es OWNER_WHATSAPP_NUMBER) y ese mensaje queda como una instrucción del día
# que el bot respeta al hablar con cualquier cliente — sirve tanto para
# restringir agenda ("no agendes hoy", "solo de 16 a 17") como para avisos de
# stock/cierre ("hoy no hay mozzarella", "cerrado por duelo"). Se borra sola
# al cambiar de día calendario.
_notas_admin: list[dict] = []  # [{"texto": str, "fecha": date}, ...]


def agregar_nota_admin(texto: str):
    hoy = datetime.utcnow().date()
    _notas_admin[:] = [n for n in _notas_admin if n["fecha"] == hoy]  # limpia notas viejas
    _notas_admin.append({"texto": texto, "fecha": hoy})


def obtener_notas_admin() -> list[str]:
    hoy = datetime.utcnow().date()
    return [n["texto"] for n in _notas_admin if n["fecha"] == hoy]


def limpiar_notas_admin():
    _notas_admin.clear()
