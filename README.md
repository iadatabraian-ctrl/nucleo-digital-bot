# Nexo — Bot de ventas WhatsApp con IA (El Núcleo Digital)

Nexo es el asistente de WhatsApp con IA de **El Núcleo Digital**. Atiende
consultas de clientes, califica prospectos, responde con el catálogo y
disponibilidad reales, y agenda llamadas directo en Google Calendar — todo
corriendo en producción sobre infraestructura gratuita/de bajo costo.

## Qué hace

- Responde consultas de clientes por WhatsApp usando **Claude Haiku 4.5**,
  con contexto real del negocio (servicios, horarios, catálogo).
- Transcribe audios con **Groq Whisper** para que el cliente pueda mandar
  notas de voz.
- Consulta y agenda en **Google Calendar** — nunca inventa disponibilidad:
  si no hay horarios reales, no ofrece agendar.
- Agrupa mensajes seguidos del mismo cliente (buffer de 12s, tope 30s) para
  responder una sola vez en vez de mensaje por mensaje.
- Se administra 100% por WhatsApp: el dueño manda comandos desde su propio
  número para cargar catálogo, bloquear servicios, avisar cierres o
  simplemente pausar el bot y atender manual.
- Detecta cuando el dueño contesta a mano (WhatsApp Business Coexistence) y
  se pausa solo, sin pisar la conversación.

## Arquitectura (resumen)

```
Cliente (WhatsApp)
   → Meta → YCloud (BSP) → Webhook Flask (Render)
   → Validación HMAC → Rate limiting → Deduplicación (WAMID)
   → ¿Es el dueño? → Modo Admin (comandos, nunca toca la IA)
   → ¿Está pausado? → Buffer de mensajes (12s / tope 30s)
   → Arma contexto (historial + Calendar + catálogo + perfil de cliente)
   → Claude Haiku 4.5
   → Redes de seguridad en código (anti-alucinación de fechas/horarios)
   → Envía respuesta (YCloud API) + notifica al dueño si se agendó algo
```

Stack: **Flask** (Render, free tier) · **YCloud** (BSP de WhatsApp,
Coexistence habilitado) · **Claude Haiku 4.5** (Anthropic) · **Groq Whisper**
(transcripción de audio) · **Google Calendar API** · **Upstash Redis**
(estado persistente — sobrevive a que Render duerma el servidor).

## Estructura del repo

```
app.py                         # webhook, buffer, rate limiting, rutas Flask
agent/
  brain.py                     # arma el contexto, llama a Claude, redes de seguridad
  config.py                    # reglas de formato/tono/flujo del prompt
  memory.py                    # historial, pausas, avisos, catálogo — todo en Redis
  calendar.py                  # disponibilidad real y creación de eventos
  providers/
    ycloud.py                  # integración con YCloud (envío, firma, parseo)
knowledge/
  business.yaml                # datos del negocio, tono, objetivo
  servicios.md                 # catálogo de servicios ofrecidos
  perfil_cliente.md            # criterio de calificación de leads
requirements.txt
```

## Variables de entorno necesarias

| Variable | Para qué |
|---|---|
| `ANTHROPIC_API_KEY` | Llamadas a Claude Haiku 4.5 |
| `YCLOUD_API_KEY` | Enviar mensajes por WhatsApp vía YCloud |
| `YCLOUD_PHONE_NUMBER` | Número de WhatsApp del negocio |
| `YCLOUD_WEBHOOK_SECRET` | Validar la firma HMAC del webhook |
| `GROQ_API_KEY` | Transcripción de audios (Whisper) |
| `GOOGLE_CALENDAR_ID` | Calendario donde se consulta/crea disponibilidad |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Credenciales de la cuenta de servicio de Google |
| `UPSTASH_REDIS_URL` | Conexión a Redis (estado persistente) |
| `UPSTASH_REDIS_TOKEN` | Token de Redis |
| `OWNER_WHATSAPP_NUMBER` | Número del dueño — modo admin y notificaciones |

## Comandos de administración (por WhatsApp, desde `OWNER_WHATSAPP_NUMBER`)

| Comando | Acción |
|---|---|
| `/pausa` | Pausa el bot indefinidamente (atención 100% manual) |
| `/bot` | Reactiva el bot |
| `/producto <id> <nombre>` | Asigna nombre a un producto del catálogo |
| `/productos` | Lista el catálogo dinámico |
| `/borrar_producto <id>` | Elimina un producto |
| `/avisos` | Lista avisos activos con su fecha de expiración |
| `/horarios` | Lista horarios especiales cargados |
| `/servicios_bloqueados` | Lista servicios ocultos temporalmente |
| `/desbloquear <palabra>` | Reactiva un servicio bloqueado |
| `/limpiar` | Borra avisos, horarios especiales y bloqueos — reset total |
| Texto libre | Se interpreta como cambio de horario, bloqueo de servicio, o aviso genérico con fecha |

Si el dueño responde manual desde la app de WhatsApp Business
(Coexistence), el bot se pausa automáticamente por 2hs para esa
conversación.

## Correr en local

```bash
pip install -r requirements.txt
cp .env.example .env   # completar con las variables de arriba
python app.py
```

## Deploy

Corre en **Render** (free tier). El servicio se duerme tras ~15 min de
inactividad — por eso todo el estado (historial, pausas, catálogo, avisos)
vive en Redis y no en memoria del proceso.

---
Desarrollado por **El Núcleo Digital** — Salto, Uruguay.
