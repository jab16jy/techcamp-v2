# ADR-0013: Sincronización offline con UUIDv7 del cliente, cursor de servidor y última escritura gana

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

El productor registra labores y cosechas sin señal. Los datos no pueden perderse ni duplicarse. Es poco común que dos personas editen la misma entrada, pero puede pasar (productor y técnico).

## Decisión

- El cliente genera el ID (UUIDv7) y escribe primero en Dexie, con un outbox local.
- `POST /sync/push` es idempotente por `id` y `client_updated_at`. `GET /sync/pull?since=` usa un `server_version` monotónico.
- Conflictos: **gana la última escritura** según `client_updated_at`; el perdedor recibe `conflict_overwritten` y la interfaz lo muestra. El servidor rechaza relojes más de 24 h en el futuro.
- Borrado lógico (`deleted_at`) para propagar eliminaciones.
- Las fotos se suben aparte con URL prefirmada.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| CRDTs (Automerge, Yjs) | Complejidad alta para registros casi siempre de un solo autor |
| Motores de sync de terceros (PowerSync, ElectricSQL, Replicache) | Otra pieza de infraestructura o licencia; el caso es una sola entidad sincronizada |
| Solo en línea | Contradice RNF-01 |

## Consecuencias

**Positivas**

- Un protocolo simple, probado y fácil de depurar.
- La idempotencia hace seguros los reintentos.

**Negativas / costos aceptados**

- Una edición concurrente puede perder cambios (se avisa). Si el piloto muestra conflictos frecuentes, se pasa a fusión por campo.
- Depende de relojes razonables en los teléfonos.

## Relacionado

[06 §7](../06-diseno-detallado.md#7-sincronización-offline-de-la-bitácora)
