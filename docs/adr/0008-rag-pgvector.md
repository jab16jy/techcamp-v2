# ADR-0008: RAG con embeddings en pgvector (reemplaza TF-IDF)

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

La v1 usaba TF-IDF sobre ~20–40 documentos. TF-IDF no encuentra sinónimos ni paráfrasis ("gusano cogollero" frente a "Spodoptera frugiperda") y no escala bien a más documentos.

## Decisión

Fragmentar los documentos (~500 tokens con solapamiento), generar embeddings con el puerto `Embedder` y guardarlos en `kb_chunk.embedding` con un índice HNSW (coseno). Recuperar los 5 fragmentos más cercanos con similitud ≥ 0,75. Cada documento registra su fuente y licencia para poder citarlo.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Mantener TF-IDF | Recuperación pobre con vocabulario agronómico variado |
| Búsqueda híbrida (BM25 + vectores) desde el inicio | Más complejidad; se agrega si la evaluación de E12 muestra fallos con términos exactos |
| Base vectorial externa | Innecesaria para 10.000 fragmentos ([ADR-0003](0003-postgres-unico.md)) |

## Consecuencias

**Positivas**

- Mejor recuperación semántica en la misma base de datos.
- Citas trazables a la fuente.

**Negativas / costos aceptados**

- Costo (pequeño) de embeddings y reindexar si se cambia de modelo de embeddings.
- El corpus de la v1 debe revisarse por licencia antes de migrarse.

## Relacionado

[ADR-0007](0007-llm-por-api.md)
