"""
agent/memory.py
----------------
Historial de conversación, pausas y avisos de admin — TODO guardado en
Upstash Redis (no en RAM), para que sobreviva a los reinicios/"sueño" del
servicio en Render (el plan gratis duerme el servicio tras ~15 min de
inactividad y borra cualquier variable en memoria).

Requiere las variables de entorno UPSTASH_REDIS_URL y UPSTASH_REDIS_TOKEN
(las mismas que aparecen en el panel de Upstash, sección REST API).
"""
import os
import re
import json
from datetime import datetime, date, timedelta
import pytz
from upstash_redis import Redis

TIMEZONE = pytz.timezone("America/Montevideo")

VENTANA = 20  # mensajes a recordar por cliente
PAUSA_HORAS_DEFAULT = 2  # horas que el bot se queda callado tras una respuesta manual

_redis = Redis(
    url=os.environ.get("UPSTASH_REDIS_URL", ""),
    token=os.environ.get("UPSTASH_REDIS_TOKEN", ""),
)


# ── Historial de conversación por número ────────────────────────────────────

def agregar_mensaje(numero: str, rol: str, contenido: str):
    """rol: 'user' o 'assistant'"""
    key = f"hist:{numero}"
    _redis.rpush(key, json.dumps({"role": rol, "content": contenido}))
    _redis.ltrim(key, -VENTANA, -1)


def obtener_historial(numero: str) -> list:
    crudos = _redis.lrange(f"hist:{numero}", 0, -1) or []
    return [json.loads(m) for m in crudos]


def limpiar_historial(numero: str):
    _redis.delete(f"hist:{numero}")


# ── Pausas por conversación ──────────────────────────────────────────────────
# Se usa el TTL nativo de Redis: una pausa "temporal" se guarda con expiración
# automática (Redis la borra solo, no hace falta chequear fechas a mano).
# Una pausa "indefinida" se guarda sin expiración.

def pausar_conversacion(numero: str, horas: float = PAUSA_HORAS_DEFAULT):
    """El dueño tomó la conversación manualmente — el bot se calla por un tiempo."""
    _redis.set(f"pausa:{numero}", "temporal", ex=int(horas * 3600))


def pausar_conversacion_indefinida(numero: str):
    """
    Pausa permanente — para chats que se quieren mantener 100% manuales
    (ej: prospección en frío desde el número de la agencia). No vence sola;
    hace falta reanudar_conversacion() para que el bot vuelva a responder ahí.
    """
    _redis.set(f"pausa:{numero}", "indefinida")


def reanudar_conversacion(numero: str):
    """Reactiva el bot para este número de inmediato (comando manual del dueño)."""
    _redis.delete(f"pausa:{numero}")


def esta_pausada(numero: str) -> bool:
    """True si el bot debe quedarse callado para este número ahora mismo."""
    return _redis.get(f"pausa:{numero}") is not None


# ── Notas de administrador ──────────────────────────────────────────────────
# El dueño le escribe directo al número del bot (se detecta porque el remitente
# es OWNER_WHATSAPP_NUMBER) y ese mensaje queda como una instrucción para el
# bot. Si el texto menciona un día ("mañana", "hoy", "el jueves", "28/07",
# "29 del 7", "29 de julio"), la nota se aplica SOLO ese día y se borra sola
# al pasar — no hace falta que el dueño se acuerde de limpiarla. Si no
# menciona ningún día (ej: "no vendemos más el modelo X"), queda indefinida
# hasta /limpiar.
_NOTAS_KEY = "notas_admin"

_DIAS_SEMANA = {
    "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2,
    "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6,
}

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}


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

    def _fecha_valida(dia: int, mes: int) -> date | None:
        try:
            fecha = date(hoy.year, mes, dia)
            if fecha < hoy:
                fecha = date(hoy.year + 1, mes, dia)
            return fecha
        except ValueError:
            return None

    # Formatos numéricos: "29/7", "29-7", "29 del 7", "29 de 7"
    m = re.search(r"\b(\d{1,2})\s*(?:[/-]|del?\b|de\b)\s*(\d{1,2})\b", t)
    if m:
        fecha = _fecha_valida(int(m.group(1)), int(m.group(2)))
        if fecha:
            return fecha

    # Formato con nombre de mes: "29 de julio"
    m = re.search(r"\b(\d{1,2})\s+de\s+(" + "|".join(_MESES) + r")\b", t)
    if m:
        fecha = _fecha_valida(int(m.group(1)), _MESES[m.group(2)])
        if fecha:
            return fecha

    for nombre, idx in _DIAS_SEMANA.items():
        if re.search(rf"\b{nombre}\b", t):
            delta = (idx - hoy.weekday()) % 7  # 0 = hoy mismo es ese día
            return hoy + timedelta(days=delta)

    return None  # no se menciona ningún día -> indefinida


def _leer_notas_crudas() -> list[dict]:
    crudo = _redis.get(_NOTAS_KEY)
    if not crudo:
        return []
    datos = json.loads(crudo)
    # las fechas se guardan como string "YYYY-MM-DD" o null en JSON
    for n in datos:
        if n.get("fecha"):
            n["fecha"] = date.fromisoformat(n["fecha"])
    return datos


def _guardar_notas_crudas(notas: list[dict]):
    serializable = [
        {"texto": n["texto"], "fecha": n["fecha"].isoformat() if n["fecha"] else None}
        for n in notas
    ]
    _redis.set(_NOTAS_KEY, json.dumps(serializable))


def _limpiar_notas_vencidas(notas: list[dict]) -> list[dict]:
    hoy = _hoy()
    return [n for n in notas if n["fecha"] is None or n["fecha"] >= hoy]


def agregar_nota_admin(texto: str) -> date | None:
    """Guarda la nota y devuelve la fecha a la que quedó asociada (o None si es indefinida)."""
    notas = _limpiar_notas_vencidas(_leer_notas_crudas())
    fecha = _resolver_fecha_mencionada(texto)
    notas.append({"texto": texto, "fecha": fecha})
    _guardar_notas_crudas(notas)
    return fecha


def obtener_notas_admin() -> list[str]:
    """Solo las notas vigentes HOY (para compatibilidad; preferir listar_notas_admin)."""
    hoy = _hoy()
    notas = _limpiar_notas_vencidas(_leer_notas_crudas())
    _guardar_notas_crudas(notas)
    return [n["texto"] for n in notas if n["fecha"] is None or n["fecha"] == hoy]


def listar_notas_admin() -> list[dict]:
    """Todas las notas vigentes (hoy, futuras o indefinidas), con su fecha — para consulta del admin."""
    notas = _limpiar_notas_vencidas(_leer_notas_crudas())
    _guardar_notas_crudas(notas)
    return sorted(notas, key=lambda n: (n["fecha"] is None, n["fecha"]))


def limpiar_notas_admin():
    _redis.delete(_NOTAS_KEY)


# ── Catálogo de productos (mapeo product_retailer_id -> nombre) ────────────
# Se guarda en Redis para que sobreviva reinicios. Se administra desde
# WhatsApp con /producto, /productos y /borrar_producto (ver app.py) —
# así no hace falta tocar código cada vez que cambia el catálogo.
_CATALOGO_KEY = "catalogo_productos"


def obtener_catalogo() -> dict[str, str]:
    crudo = _redis.get(_CATALOGO_KEY)
    if not crudo:
        return {}
    return json.loads(crudo)


def guardar_producto(product_id: str, nombre: str):
    catalogo = obtener_catalogo()
    catalogo[product_id] = nombre
    _redis.set(_CATALOGO_KEY, json.dumps(catalogo))


def borrar_producto(product_id: str) -> bool:
    """Devuelve True si existía y se borró, False si no estaba."""
    catalogo = obtener_catalogo()
    if product_id not in catalogo:
        return False
    del catalogo[product_id]
    _redis.set(_CATALOGO_KEY, json.dumps(catalogo))
    return True
