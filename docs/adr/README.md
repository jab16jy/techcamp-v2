# Registro de decisiones de arquitectura (ADR)

Cada ADR registra una decisión importante: su contexto, las alternativas y las consecuencias aceptadas. Un ADR aceptado no se edita; si la decisión cambia, se escribe uno nuevo que lo **reemplaza** y el anterior se marca `Reemplazada por ADR-XXXX`. Cuando un ADR posterior **aclara** una decisión sin cambiarla, el anterior se marca `Aceptada; modificada por ADR-XXXX` y el texto original queda intacto (así 0009 con 0022 y 0023, y 0024 con 0025).

| ADR | Decisión | Estado |
|---|---|---|
| [0001](0001-nuevo-repositorio-v2.md) | TechCamp v2 en un repositorio nuevo, rescatando piezas de la v1 | Aceptada |
| [0002](0002-monolito-modular.md) | Monolito modular con arquitectura hexagonal por módulo | Aceptada |
| [0003](0003-postgres-unico.md) | PostgreSQL único con PostGIS, TimescaleDB y pgvector | Aceptada |
| [0004](0004-mqtt-y-lorawan.md) | MQTT como protocolo de ingesta y LoRaWAN (ChirpStack) como transporte rural | Aceptada |
| [0005](0005-pwa-offline-first.md) | PWA offline-first en lugar de app nativa | Aceptada |
| [0006](0006-design-system.md) | Design system propio sobre Tailwind v4 + shadcn/ui (Radix), construido antes que las pantallas | Aceptada |
| [0007](0007-llm-por-api.md) | LLM por API externa de bajo costo, detrás de un puerto; el LLM explica, no decide | Aceptada |
| [0008](0008-rag-pgvector.md) | RAG con embeddings en pgvector (reemplaza TF-IDF) | Aceptada |
| [0009](0009-riego-fao56.md) | Riego con balance hídrico FAO-56 corregido por humedad de suelo medida | Aceptada; modificada por 0022 y 0023 |
| [0010](0010-rescate-y-gobierno-de-modelos.md) | Rescate selectivo de modelos de la v1 y compuerta de promoción sin excepciones | Reemplazada por 0019 |
| [0011](0011-aptitud-de-cultivo.md) | Recomendación de cultivo reformulada como aptitud y diferida a v2.x | Aceptada (implementación diferida) |
| [0012](0012-jobs-en-postgres.md) | Cola de trabajos y tareas periódicas en PostgreSQL (procrastinate) | Aceptada |
| [0013](0013-sincronizacion-offline.md) | Sincronización offline con UUIDv7 del cliente, cursor de servidor y última escritura gana | Aceptada |
| [0014](0014-autenticacion.md) | Autenticación con proveedor gestionado (OTP por teléfono) y autorización propia | Aceptada |
| [0015](0015-tiempo-real-sse.md) | Server-Sent Events para tiempo real en la PWA | Aceptada |
| [0016](0016-notificaciones-outbox.md) | Notificaciones con outbox transaccional, reintentos y canal alterno | Aceptada |
| [0017](0017-despliegue-compose-caddy.md) | Despliegue en un VPS con Docker Compose y Caddy | Aceptada |
| [0018](0018-almacenamiento-de-objetos.md) | Fotos y artefactos en almacenamiento de objetos S3 con URL prefirmada | Aceptada |
| [0019](0019-reconstruccion-de-modelos.md) | Reconstruir los modelos de ML desde cero; de la v1 solo se reutiliza código revisado | Aceptada |
| [0020](0020-protocolo-de-experimentacion-ml.md) | Protocolo de experimentación de ML con harness fijo, escalera de líneas base y tuning sistemático | Aceptada |
| [0021](0021-perfil-seminario-local.md) | Perfil de seminario: ejecución local con emuladores | Aceptada |
| [0022](0022-estres-hidrico-y-asimilacion.md) | Estrés hídrico por parcela y asimilación ponderada del sensor | Aceptada |
| [0023](0023-parcelas-con-riego-y-secano.md) | Parcelas con riego y de secano: el mismo balance hídrico, con lámina o con recomendación de secano | Aceptada |
| [0024](0024-metricas-de-impacto-y-adopcion-digital.md) | Métricas de impacto y adopción digital: impacto contra la encuesta de inscripción e índice que cuenta acciones | Aceptada; modificada por 0025 |
| [0025](0025-la-accion-de-water-stress-es-de-la-parcela.md) | La acción de `water_stress` que cuenta en el índice de adopción es de la parcela, no del ciclo de cultivo | Aceptada |

Los ADRs 0004, 0007 (en parte), 0014, 0016, 0017 y 0018 describen el perfil `production` (futuro). Cada uno lleva una nota de **Alcance** que remite al [ADR-0021](0021-perfil-seminario-local.md), donde se definen los adaptadores del perfil `seminar`. Esa nota no cambia la decisión.

## Plantilla

```markdown
# ADR-NNNN: <decisión en una frase>

- **Estado:** Propuesta | Aceptada | Reemplazada por ADR-XXXX
- **Fecha:** AAAA-MM-DD

## Contexto
## Decisión
## Alternativas consideradas
## Consecuencias
## Relacionado
```
