# ADR-0003: PostgreSQL único con PostGIS, TimescaleDB y pgvector

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

El sistema necesita consultas espaciales (parcelas), series de tiempo (sensores y clima) y búsqueda semántica (asistente). La v1 ya usaba PostGIS. El volumen del año 3 es ~30 GB/año, comprimido.

## Decisión

Una sola instancia de **PostgreSQL** con las extensiones **PostGIS**, **TimescaleDB** (hypertables, compresión, agregados continuos y retención) y **pgvector** (índice HNSW). Se usa una imagen que ya trae las tres extensiones; se verifica al implementar E0.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| InfluxDB para series + Postgres para lo demás | Dos motores, dos backups y joins imposibles entre lecturas y parcelas |
| Base vectorial dedicada (Qdrant, Pinecone) | 10.000 fragmentos caben de sobra en pgvector |
| Postgres sin TimescaleDB (particionado nativo) | Habría que escribir a mano la compresión, los agregados continuos y la retención |
| MongoDB / NoSQL | El dominio es relacional (organización → finca → parcela → ciclo) y necesita integridad referencial |

## Consecuencias

**Positivas**

- Un solo motor que operar, respaldar y monitorear.
- Joins entre lecturas, parcelas y alertas en SQL, con transacciones.
- La compresión de TimescaleDB (~90 %) mantiene pequeño el almacenamiento.

**Negativas / costos aceptados**

- TimescaleDB tiene partes con licencia TSL (no Apache). Algunas funciones no están en todos los proveedores de Postgres gestionado; se autoaloja.
- Toda la carga depende de un nodo; la ruta de escala es una réplica de lectura ([09](../09-cuellos-de-botella.md)).

## Relacionado

[03-modelo-datos](../03-modelo-datos.md)
