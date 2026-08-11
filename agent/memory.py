"""
agent/memory.py
----------------
Historial de conversación, pausas, avisos de admin, horarios especiales por
día y servicios bloqueados — TODO guardado en Upstash Redis (no en RAM),
para que sobreviva a los reinicios/"sueño" del servicio en Render (el plan
gratis duerme el servicio tras ~15 min de inactividad y borra cualquier
variable en memoria).

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

VENTANA = 20
PAUSA_HORAS_DEFAULT = 2.0  # 2 horas

_redis = Redis(
    url=os.environ.get("UPSTASH_REDIS_URL", ""),
    token=os.environ.get("UPSTASH_REDIS_TOKEN", ""),
)

# ── Historial de conversación ────────────────────────────────────────────────

def agregar_mensaje(numero: str, rol: str, contenido: str):
    key = f"hist:{numero}"
    _redis.rpush(key, json.dumps({"role": rol, "content": contenido}))
    _redis.ltrim(key, -VENTANA, -1)

def obtener_historial(numero: str) -> list:
    crudos = _redis.lrange(f"hist:{numero}", 0, -1) or []
    return [json.loads(m) for m in crudos]

def limpiar_historial(numero: str):
    _redis.delete(f"hist:{numero}")

# ── Pausas ───────────────────────────────────────────────────────────────────

def pausar_conversacion(numero: str, horas: float = PAUSA_HORAS_DEFAULT):
    _redis.set(f"pausa:{numero}", "temporal", ex=int(horas * 3600))

def pausar_conversacion_indefinida(numero: str):
    _redis.set(f"pausa:{numero}", "indefinida")

def reanudar_conversacion(numero: str):
    _redis.delete(f"pausa:{numero}")

def esta_pausada(numero: str) -> bool:
    return _redis.get(f"pausa:{numero}") is not None

# ── Pausa global del bot (comando /pausa y /bot del dueño) ──────────────────

_PAUSA_GLOBAL_KEY = "bot_pausado_global"

def pausar_bot_global():
    _redis.set(_PAUSA_GLOBAL_KEY, "1")

def reanudar_bot_global():
    _redis.delete(_PAUSA_GLOBAL_KEY)

def bot_pausado_global() -> bool:
    return _redis.get(_PAUSA_GLOBAL_KEY) is not None

# ── Buffer de mensajes seguidos (agrupar antes de responder) ────────────────

def agregar_a_buffer(numero: str, texto: str):
    key = f"buffer:{numero}"
    _redis.rpush(key, texto)
    _redis.expire(key, 60)  # nunca queda colgado si algo falla

def obtener_y_limpiar_buffer(numero: str) -> list[str]:
    key = f"buffer:{numero}"
    textos = _redis.lrange(key, 0, -1) or []
    _redis.delete(key)
    return textos

# ── Notas de administrador ──────────────────────────────────────────────────

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

    m = re.search(r"\b(\d{1,2})\s*(?:[/-]|del?\b|de\b)\s*(\d{1,2})\b", t)
    if m:
        fecha = _fecha_valida(int(m.group(1)), int(m.group(2)))
        if fecha:
            return fecha

    m = re.search(r"\b(\d{1,2})\s+de\s+(" + "|".join(_MESES) + r")\b", t)
    if m:
        fecha = _fecha_valida(int(m.group(1)), _MESES[m.group(2)])
        if fecha:
            return fecha

    for nombre, idx in _DIAS_SEMANA.items():
        if re.search(rf"\b{nombre}\b", t):
            delta = (idx - hoy.weekday()) % 7
            return hoy + timedelta(days=delta)

    return None

def _leer_notas_crudas() -> list[dict]:
    crudo = _redis.get(_NOTAS_KEY)
    if not crudo:
        return []
    datos = json.loads(crudo)
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
    notas = _limpiar_notas_vencidas(_leer_notas_crudas())
    fecha = _resolver_fecha_mencionada(texto)
    notas.append({"texto": texto, "fecha": fecha})
    _guardar_notas_crudas(notas)
    return fecha

def obtener_notas_admin() -> list[str]:
    hoy = _hoy()
    notas = _limpiar_notas_vencidas(_leer_notas_crudas())
    _guardar_notas_crudas(notas)
    return [n["texto"] for n in notas if n["fecha"] is None or n["fecha"] == hoy]

def listar_notas_admin() -> list[dict]:
    notas = _limpiar_notas_vencidas(_leer_notas_crudas())
    _guardar_notas_crudas(notas)
    return sorted(notas, key=lambda n: (n["fecha"] is None, n["fecha"]))

def limpiar_notas_admin():
    _redis.delete(_NOTAS_KEY)

# ── Horarios especiales ──────────────────────────────────────────────────────

_HORARIOS_KEY_PREFIX = "horario_override:"
_HORARIOS_ACTIVOS_KEY = "horario_overrides_activos"

_RE_CIERRE = re.compile(
    r"(no\s+abrimos|no\s+atendemos|cerrado|cerramos|sin\s+atenci[oó]n|"
    r"no\s+hay\s+atenci[oó]n|no\s+trabajamos)",
    re.IGNORECASE,
)
_RE_APERTURA_RESET = re.compile(
    r"(abrimos\s+normal|atendemos\s+normal|horario\s+normal|(?:^|\s)s[ií]\.?$)",
    re.IGNORECASE,
)
_RE_RANGO_DIAS = re.compile(
    r"\bde\s+(" + "|".join(_DIAS_SEMANA) + r")\s+a\s+(" + "|".join(_DIAS_SEMANA) + r")\b",
    re.IGNORECASE,
)
_RE_HORA_RANGO = re.compile(
    r"de\s+(\d{1,2})(?::(\d{2}))?\s*(?:a|hasta)\s+(\d{1,2})(?::(\d{2}))?"
    r"\s*(?:hs\.?|horas?)?\s*(de\s+la\s+tarde|de\s+la\s+ma[ñn]ana|de\s+la\s+noche)?",
    re.IGNORECASE,
)

def _ajustar_hora_tarde(hora: int, sufijo: str | None) -> int:
    if sufijo and ("tarde" in sufijo.lower() or "noche" in sufijo.lower()) and hora <= 6:
        return hora + 12
    return hora

def _registrar_fecha_override(fecha: date):
    activos = _leer_fechas_override()
    if fecha.isoformat() not in activos:
        activos.append(fecha.isoformat())
        _redis.set(_HORARIOS_ACTIVOS_KEY, json.dumps(activos))

def _leer_fechas_override() -> list[str]:
    crudo = _redis.get(_HORARIOS_ACTIVOS_KEY)
    return json.loads(crudo) if crudo else []

def _extraer_fechas_horario(texto: str) -> list[date]:
    t = texto.lower()
    m_rango = _RE_RANGO_DIAS.search(t)
    if m_rango:
        hoy = _hoy()
        idx1 = _DIAS_SEMANA[m_rango.group(1)]
        idx2 = _DIAS_SEMANA[m_rango.group(2)]
        inicio = hoy + timedelta(days=(idx1 - hoy.weekday()) % 7)
        fin = hoy + timedelta(days=(idx2 - hoy.weekday()) % 7)
        if fin < inicio:
            fin += timedelta(days=7)
        dias = []
        cursor = inicio
        while cursor <= fin:
            dias.append(cursor)
            cursor += timedelta(days=1)
        return dias

    fecha_unica = _resolver_fecha_mencionada(texto)
    return [fecha_unica] if fecha_unica else []

def intentar_registrar_horario(texto: str) -> str | None:
    fechas = _extraer_fechas_horario(texto)
    if not fechas:
        return None

    es_cierre = bool(_RE_CIERRE.search(texto))
    es_apertura = bool(_RE_APERTURA_RESET.search(texto.strip()))
    m_hora = _RE_HORA_RANGO.search(texto)

    if not (es_cierre or es_apertura or m_hora):
        return None

    dias_str = " y ".join(f.strftime("%d/%m") for f in fechas)

    if es_apertura and not es_cierre and not m_hora:
        for fecha in fechas:
            _redis.delete(f"{_HORARIOS_KEY_PREFIX}{fecha.isoformat()}")
        return f"Anotado ✅: horario normal restaurado para {dias_str}."

    if es_cierre:
        for fecha in fechas:
            _redis.set(
                f"{_HORARIOS_KEY_PREFIX}{fecha.isoformat()}",
                json.dumps({"cerrado": True, "desde": None, "hasta": None}),
            )
            _registrar_fecha_override(fecha)
        return f"Anotado ✅: {dias_str} — cerrado, no se agenda ese día."

    if m_hora:
        h1 = _ajustar_hora_tarde(int(m_hora.group(1)), m_hora.group(5))
        m1 = int(m_hora.group(2) or 0)
        h2 = _ajustar_hora_tarde(int(m_hora.group(3)), m_hora.group(5))
        m2 = int(m_hora.group(4) or 0)
        desde = f"{h1:02d}:{m1:02d}"
        hasta = f"{h2:02d}:{m2:02d}"
        for fecha in fechas:
            _redis.set(
                f"{_HORARIOS_KEY_PREFIX}{fecha.isoformat()}",
                json.dumps({"cerrado": False, "desde": desde, "hasta": hasta}),
            )
            _registrar_fecha_override(fecha)
        return f"Anotado ✅: {dias_str} — atención restringida de {desde} a {hasta}."

    return None

def obtener_horario_override(fecha: date) -> dict | None:
    crudo = _redis.get(f"{_HORARIOS_KEY_PREFIX}{fecha.isoformat()}")
    if not crudo:
        return None
    return json.loads(crudo)

def listar_horarios_activos() -> list[dict]:
    hoy = _hoy()
    activos = _leer_fechas_override()
    vigentes = []
    resultado = []
    for f_str in activos:
        f = date.fromisoformat(f_str)
        if f < hoy:
            continue
        override = obtener_horario_override(f)
        if override:
            resultado.append({"fecha": f, **override})
            vigentes.append(f_str)
    if vigentes != activos:
        _redis.set(_HORARIOS_ACTIVOS_KEY, json.dumps(vigentes))
    return sorted(resultado, key=lambda x: x["fecha"])

# ── Servicios bloqueados ─────────────────────────────────────────────────────

_SERVICIOS_BLOQUEADOS_KEY = "servicios_bloqueados"

_RE_BLOQUEAR_SERVICIO = re.compile(
    r"no\s+(?:ofrezcas|ofrezcamos|vendas|vendamos|promociones|promocion[eé]s)\s+"
    r"(?:servicios?\s+de\s+|el\s+servicio\s+de\s+)?(.+)",
    re.IGNORECASE,
)

def intentar_bloquear_servicio(texto: str) -> str | None:
    m = _RE_BLOQUEAR_SERVICIO.search(texto.strip())
    if not m:
        return None
    keyword = m.group(1).strip().rstrip(".").lower()
    if not keyword:
        return None
    bloqueados = obtener_servicios_bloqueados()
    if keyword not in bloqueados:
        bloqueados.append(keyword)
        _redis.set(_SERVICIOS_BLOQUEADOS_KEY, json.dumps(bloqueados))
    return f"Anotado ✅: no se va a ofrecer más \"{keyword}\"."

def obtener_servicios_bloqueados() -> list[str]:
    crudo = _redis.get(_SERVICIOS_BLOQUEADOS_KEY)
    if not crudo:
        return []
    return json.loads(crudo)

def desbloquear_servicio(keyword: str) -> bool:
    bloqueados = obtener_servicios_bloqueados()
    keyword = keyword.strip().lower()
    if keyword not in bloqueados:
        return False
    bloqueados.remove(keyword)
    _redis.set(_SERVICIOS_BLOQUEADOS_KEY, json.dumps(bloqueados))
    return True

def limpiar_todo():
    limpiar_notas_admin()
    for f_str in _leer_fechas_override():
        _redis.delete(f"{_HORARIOS_KEY_PREFIX}{f_str}")
    _redis.delete(_HORARIOS_ACTIVOS_KEY)
    _redis.delete(_SERVICIOS_BLOQUEADOS_KEY)

# ── Catálogo de productos ────────────────────────────────────────────────────

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
    catalogo = obtener_catalogo()
    if product_id not in catalogo:
        return False
    del catalogo[product_id]
    _redis.set(_CATALOGO_KEY, json.dumps(catalogo))
    return True
