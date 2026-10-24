# ADR-0027: El stream del asistente es el cuerpo de su propia respuesta, no el stream de tiempo real

- **Estado:** Aceptada
- **Fecha:** 2026-10-06

## Contexto

Dos documentos describen dos cosas distintas y ambos usan la palabra *stream*:

- [ADR-0015:13](0015-tiempo-real-sse.md) define SSE como el fan-out **servidor → cliente** de
  `GET /api/v1/stream`: una conexión `LISTEN plot_events` **por proceso `api`**, keepalive cada 20 s,
  reconexión con `Last-Event-ID`. La conexión no termina nunca mientras el proceso vive.
- [docs/04:227](../04-api.md) define `POST /assistant/messages` devolviendo `text/event-stream`: los
  tokens y las fuentes citadas de **una** respuesta.

Sin esta aclaración, la lectura conjunta produce tres errores: tratar el stream del asistente como otro
cliente del hub de [ADR-0015](0015-tiempo-real-sse.md), aplicarle el límite de 1 stream por usuario de
[docs/09:28](../09-cuellos-de-botella.md), y asumir que el navegador puede consumirlo con `EventSource`.

Ese último punto no es una preferencia. **`EventSource` solo abre `GET` y no envía cabeceras**, así que no
puede mandar `Authorization: Bearer` ([docs/04:15](../04-api.md)) a un endpoint `POST`. El repo ya tiene la
pieza correcta: `web/src/lib/api/sse.ts` expone `createSseParser` y
`web/src/lib/api/useFarmEvents.ts:32-89` lee el stream del hub con `fetch` + `ReadableStream`.

Del lado del servidor, la ruta SSE existente (`telemetry/adapters/api/router.py:446-485`) lleva en su
docstring el hallazgo `R3-stream-holds-db-session`: mantener la sesión de base de datos viva durante el
`yield` de cada token agota el pool, porque la sesión queda retenida durante toda la respuesta.

## Decisión

- **Son dos streams y cada uno conserva su alcance.** [ADR-0015](0015-tiempo-real-sse.md) sigue rigiendo
  `GET /api/v1/stream`; `docs/04:227` sigue rigiendo la forma del endpoint del asistente. Ninguno se
  reescribe ni se reemplaza con el otro.
- **El stream del asistente no pasa por el hub.** No hay `LISTEN`, ni suscripción, ni registro en el hub
  de eventos: abre y cierra con su propia solicitud. Un `GET /api/v1/stream` no emite tokens.
- **El límite de 1 stream por usuario de [docs/09:28](../09-cuellos-de-botella.md) acota el fan-out de
  tiempo real, no el stream del asistente**, que además nace y muere con la petición. [docs/09](../09-cuellos-de-botella.md)
  queda anotado para que el próximo que lea la tabla sepa a cuál se refiere.
- **Cliente web: `fetch` + `ReadableStream` + `createSseParser`**, nunca `EventSource`. El token va en la
  cabecera `Authorization`, igual que en el resto de la API.
- **Servidor: la sesión de base de datos se abre y se cierra alrededor del trabajo, nunca durante el
  `yield`.** Los hechos y la recuperación se resuelven antes de la primera respuesta; el `yield` solo
  entrega tokens ya obtenidos. Una fuente de datos lenta no puede agotar el pool por estar checked out.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Colgar el asistente del hub de [ADR-0015](0015-tiempo-real-sse.md) | Una respuesta es única y tiene final; el hub es para hechos broadcasts a muchos clientes. Unirlos obligaría a modelar identidad de suscripción para un flujo que dura segundos |
| Reutilizar la ruta SSE del hub para el asistente | El `POST` no puede volver a un `GET` de suscripción: el cuerpo lleva la pregunta y la identidad, y la respuesta tiene que durar lo que dura ese turno |
| Leer el stream con `EventSource` | No admite `POST` ni cabeceras: no hay forma de autenticar la llamada |
| Mantener la sesión viva durante el `yield` | Es el bug ya registrado como `R3-stream-holds-db-session`; con tokens por segundo, cada respuesta retiene una conexión del pool |
| Un WebSocket para el asistente | Bidireccional sin necesidad: la pregunta ya se envió en el `POST` y el servidor no llama al cliente |

## Consecuencias

**Positivas**

- El límite de concurrencia de [docs/09](../09-cuellos-de-botella.md) protege el recurso que realmente lo
  necesita (el proceso `api` con su `LISTEN`), no un stream que ya está acotado por su propia duración.
- El cliente del asistente reutiliza un parser y un patrón ya probados en la PWA.
- El pool de conexiones no se estira por respuestas largas.

**Negativas / costos aceptados**

- El cliente del asistente **no** reconecta solo: una respuesta a medio leer se pierde y el productor
  vuelve a preguntar. Es aceptable porque la respuesta es de un turno, no un histórico de eventos.
- El parser de SSE se usa en dos formas distintas (hub y asistente). Se comparten, no se duplican.

## Relacionado

[ADR-0015](0015-tiempo-real-sse.md), [ADR-0007](0007-llm-por-api.md),
[04-api](../04-api.md#riesgo-métricas-y-asistente),
[09-cuellos-de-botella](../09-cuellos-de-botella.md),
[06 §9](../06-diseno-detallado.md#9-asistente-agronómico),
`odd/tasks/techcamp-v2-e12-assistant.md` (D-T12.4)
