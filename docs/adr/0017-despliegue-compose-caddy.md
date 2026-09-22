# ADR-0017: Despliegue en un VPS con Docker Compose y Caddy

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

El piloto necesita un costo bajo y una operación simple. La v1 usaba Docker Compose con nginx y exponía Postgres (5432) y el backend (8000) al host.

## Decisión

- Un VPS con **Docker Compose**: `caddy`, `api`, `ingestor`, `worker`, `postgres`, `mosquitto`; perfiles opcionales `lora` (ChirpStack) y `ops` (Prometheus + Grafana).
- **Caddy** con TLS automático, sirve la PWA y hace proxy de `/api` y SSE.
- Solo se exponen 443 y 8883 (y 1700/UDP con `lora`).
- Backups con pgBackRest a S3 (RPO 5 min, RTO 4 h) y restauración ensayada cada trimestre.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Kubernetes | Complejidad operativa desproporcionada para 5 contenedores |
| PaaS (Render, Fly.io, Railway) | MQTT, UDP de LoRa y extensiones de Postgres complican o encarecen; se reevalúa después del piloto |
| nginx como en la v1 | TLS manual; Caddy lo automatiza con menos configuración |

## Consecuencias

**Positivas**

- Un solo archivo describe todo el sistema; el entorno local es igual a producción.
- Costo de un VPS.

**Negativas / costos aceptados**

- El VPS es un punto único de falla (aceptado en el piloto, ver [09](../09-cuellos-de-botella.md#puntos-únicos-de-falla)).
- Escalar requiere pasos manuales (réplica, segundo host).

## Relacionado

[05-arquitectura](../05-arquitectura.md#despliegue)
