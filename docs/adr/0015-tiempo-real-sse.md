# ADR-0015: Server-Sent Events para tiempo real en la PWA

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

La PWA necesita recibir lecturas en vivo y cambios de alertas y nodos. La comunicación es solo del servidor al cliente.

## Decisión

**SSE** en `/api/v1/stream`. Cada proceso `api` mantiene **una** conexión `LISTEN` a Postgres y reparte los eventos (`NOTIFY plot_events`) a sus clientes. Keepalive cada 20 s, reconexión con `Last-Event-ID`. Caddy hace proxy sin buffer.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| WebSockets | Bidireccional sin necesidad; más complejo detrás de proxies y en reconexión |
| Polling | Más tráfico y batería en 3G, peor latencia |
| Redis pub/sub | Infraestructura extra; `LISTEN/NOTIFY` alcanza con pocos procesos `api` |

## Consecuencias

**Positivas**

- HTTP estándar, reconexión nativa del navegador y compatible con HTTP/2.
- Sin servicios extra.

**Negativas / costos aceptados**

- `NOTIFY` tiene payload limitado (8 KB): se envían IDs y datos mínimos.
- Si hubiera muchos procesos `api` habría que revisar la carga de `LISTEN`.

## Relacionado

[04-api](../04-api.md#stream-sse)
