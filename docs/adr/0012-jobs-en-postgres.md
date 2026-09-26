# ADR-0012: Cola de trabajos y tareas periódicas en PostgreSQL (procrastinate)

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

Hacen falta jobs periódicos (clima, balance hídrico, riesgo, métricas) y asíncronos (notificaciones, recalibración, exportaciones). No hay Redis en el stack.

## Decisión

Usar **procrastinate** (cola de tareas async sobre PostgreSQL, con `SKIP LOCKED` y tareas periódicas) en el proceso `worker`. Las notificaciones usan además una tabla outbox propia ([ADR-0016](0016-notificaciones-outbox.md)). Los jobs son idempotentes, identificados por (tipo, entidad, fecha).

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Celery + Redis | Otro servicio que operar y un segundo lugar donde vive el estado |
| APScheduler dentro de la API | Se duplica al escalar la API y se pierde si el proceso cae |
| Cron del sistema | Sin reintentos, sin visibilidad y fuera del código |

## Consecuencias

**Positivas**

- Los jobs se encolan en la misma transacción que los datos que los originan.
- Sin infraestructura extra.

**Negativas / costos aceptados**

- Rendimiento de la cola limitado por Postgres; alcanza con amplio margen para miles de jobs/día.
- La biblioteca y su versión se confirman con la documentación vigente al implementar E0.
- La versión se fija de forma exacta (`procrastinate==3.10.0` en `server/pyproject.toml` y `server/uv.lock`), no como rango: la migración Alembic `231a40930eb5` aplica el SQL que devuelve `SchemaManager.get_schema()` de la versión instalada, así que un rango flotante aplicaría a una base ya migrada un esquema distinto al registrado. Subir de versión requiere una migración nueva, escrita contra el esquema de la versión nueva y contra las migraciones propias de procrastinate (`procrastinate_migrations`), que esta migración no sigue.

## Relacionado

[10-dag §3](../10-dag.md#3-dag-de-orquestación-diaria-worker)
