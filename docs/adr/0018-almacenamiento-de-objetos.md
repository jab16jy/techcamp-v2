# ADR-0018: Fotos y artefactos en almacenamiento de objetos S3 con URL prefirmada

- **Estado:** Aceptada
- **Fecha:** 2026-09-22
- **Alcance:** la URL prefirmada y el uso de `object_key` aplican en ambos perfiles. Los proveedores S3 pagos son de producción futura; en el perfil `seminar` se usa MinIO en Docker ([ADR-0021](0021-perfil-seminario-local.md)).

## Contexto

Las fotos de la bitácora son el dato que más crece (~50 GB/año en el año 3). Guardarlas en Postgres infla los backups y la memoria.

## Decisión

Almacenamiento de objetos compatible con S3 (proveedor a elegir por costo: Cloudflare R2, Backblaze B2 o MinIO autoalojado). El cliente comprime la foto a ≤ 200 KB, elimina EXIF y la sube con una **URL prefirmada** que emite la API. En la base solo se guarda `object_key`. Los artefactos de modelos y los backups de Postgres usan el mismo servicio, en buckets separados.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Fotos en Postgres (`bytea`) | Backups y restauraciones lentos; memoria desperdiciada |
| Disco local del VPS | Se pierde con el VPS y no escala |

## Consecuencias

**Positivas**

- La API no transporta bytes de fotos.
- Backups de la base pequeños y rápidos.

**Negativas / costos aceptados**

- Un servicio externo más y un costo de salida de datos según el proveedor (solo en producción).
- Hay que limpiar objetos huérfanos (job periódico).

## Relacionado

[04-api](../04-api.md#bitácora-sincronización-offline), [02-estimaciones](../02-estimaciones.md#almacenamiento)
