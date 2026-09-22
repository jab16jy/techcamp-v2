# ADR-0007: LLM por API externa de bajo costo, detrás de un puerto; el LLM explica, no decide

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

La v1 corría Ollama local (`gemma2:2b`, 4 GB de RAM reservados en el Compose). Eso encarece el servidor, da respuestas de baja calidad en español técnico y agrega un contenedor pesado. El volumen estimado es de 1.800 preguntas/día en el año 3.

## Decisión

- Se elimina Ollama.
- El módulo `assistant` define un puerto `LLMClient` (`stream(messages) → tokens`) con un adaptador para un proveedor de bajo costo (candidatos: Claude Haiku 4.5, Gemini Flash). Otro puerto `Embedder` genera los embeddings.
- El proveedor por defecto se elige en E12 con una evaluación corta: 30 preguntas agronómicas reales del Caribe, calificadas por un agrónomo según exactitud, citas y claridad.
- **El LLM no calcula riego, riesgo ni dosis.** Recibe hechos calculados por el sistema y los explica.
- Límite de 10 preguntas/día por usuario, tope mensual de presupuesto y degradación a una respuesta sin LLM.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Mantener Ollama local | Costo fijo de RAM/CPU, calidad insuficiente, operación extra |
| Modelo grande premium | Costo por pregunta alto para el valor que agrega explicar datos ya calculados |
| Sin asistente | Es un canal útil para productores con poca alfabetización digital; se mantiene como módulo acotado |

## Consecuencias

**Positivas**

- Servidor más pequeño y barato.
- Mejor calidad de respuesta; se puede cambiar de proveedor sin tocar el dominio.
- Riesgo de "alucinación" acotado: el LLM no toma decisiones.

**Negativas / costos aceptados**

- Costo variable por uso y dependencia de un tercero (mitigados con topes y degradación).
- Los datos de la parcela salen hacia el proveedor: se envía lo mínimo necesario, sin datos personales, y se informa en el consentimiento.

## Relacionado

[06 §9](../06-diseno-detallado.md#9-asistente-agronómico), [ADR-0008](0008-rag-pgvector.md), [02-estimaciones](../02-estimaciones.md#costo-del-llm)
