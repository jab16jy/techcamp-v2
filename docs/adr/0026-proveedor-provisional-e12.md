# ADR-0026: Proveedor provisional del asistente en E12: DeepSeek para el LLM y `multilingual-e5-small` local para el embedding

- **Estado:** Aceptada
- **Fecha:** 2026-10-06

**Pin en una línea:** LLM = `deepseek-chat` por la API compatible con OpenAI; embeddings =
`intfloat/multilingual-e5-small`, **384** dimensiones, licencia MIT, ejecutado localmente. T2 escribe
`vector(384)` con HNSW coseno; T8 confirma el pin o aterriza una migración de reescritura como su propia
unidad de trabajo.

## Contexto

[ADR-0007](0007-llm-por-api.md) elimina Ollama, fija el puerto `LLMClient` (`stream(messages) → tokens`) y
deja el **proveedor concreto** para E12, decidido por una evaluación de 30 preguntas agronómicas. Mientras
tanto [docs/02:78](../02-estimaciones.md#costo-del-llm) usa Claude Haiku 4.5 (USD 1 / 5 por millón) como
referencia de precio y dice, en la misma línea, que *"los precios se reconfirman al implementar"*.

El dueño decidió el 2026-10-06: **modelos baratos, nada de SDK Anthropic y tope DeepSeek para el LLM.**

Hay un segundo bloqueo que la decisión del dueño no cubre por sí sola. La dimensión de
`kb_chunk.embedding` es una propiedad del modelo de embeddings, no una elección del servidor
([docs/03:361](../03-modelo-datos.md), `vector embedding`; [docs/03:558](../03-modelo-datos.md), HNSW
coseno). La revisión de pgvector con ctx7 del 2026-10-06 confirma que la columna se declara
`vector(<dim>)` y que `pgvector.sqlalchemy.Vector(dim)` exige conocer ese número
(`techcamp-v2-e12-blockers.md:74-82`). Por tanto **T2 no puede escribir `vector(<dim>)` sin este pin**, y
esta ADR es el pin.

Verificado con ctx7 el 2026-10-06 (biblioteca `/websites/api-docs_deepseek`, fuente de reputación alta):

| Hecho | Evidencia |
|---|---|
| La API de DeepSeek es **compatible con OpenAI**: se llama con el SDK `openai` pasando `base_url` | `api_samples/chat_python`: `OpenAI(api_key=…, base_url="https://api.deepseek.com")` |
| Endpoint y autenticación | `POST https://api.deepseek.com/chat/completions` con `Authorization: Bearer ${DEEPSEEK_API_KEY}` |
| Streaming SSE | `POST /chat/completions` con `stream: true` devuelve líneas `data: {…}` con `choices[0].delta.content`, un último chunk con `finish_reason` y `usage`, y cierra con `data: [DONE]` |
| `usage` trae los tokens | `prompt_tokens`, `completion_tokens`, `total_tokens`, `prompt_cache_hit_tokens`, `prompt_cache_miss_tokens` |
| Modelos de chat | `deepseek-chat` y `deepseek-flash` |
| **No hay endpoint de embeddings** | La documentación solo cubre `chat`, `reasoner` y archivos (`files`); buscar *embeddings* en ella no devuelve ningún `POST /embeddings` |

Ese último punto es la razón de que el embedding sea una decisión separada: la API de DeepSeek es
**chat/reasoning compatible con OpenAI** (V3.2 y R1) y **no publica un endpoint `/embeddings`**, así que el
corpus no puede usar el mismo proveedor que el LLM. Los proveedores baratos que **sí** exponen embeddings
existen, pero no son el mismo proveedor: los **locales** `multilingual-e5-small` / `bge-m3` (costo de API
cero, y el corpus no sale del servidor), los **hosteados** Voyage AI (`output_dimension` flexible en
256/512/1024/2048), OpenAI `text-embedding-3-small`, Cohere y Gemini. Este ADR no fija precios de ninguno:
los reales se escriben en T8 ([docs/02:78](../02-estimaciones.md#costo-del-llm)).

## Decisión

- **LLM provisional: `deepseek-chat`** contra la API compatible con OpenAI. Es barato, cumple el tope del
  dueño y no obliga a cambiar el puerto `LLMClient` de [ADR-0007:14](0007-llm-por-api.md).
- **Cliente HTTP: genérico o `openai` apuntando a `base_url`.** El adaptador de `assistant` habla HTTP
  común; si usa el SDK `openai`, solo cambia `base_url` y la clave. **Prohibido añadir `anthropic` a
  `server/pyproject.toml`** (T2 fija las dependencias): el SDK de Anthropic no se usa en v2.
- **Streaming: SSE con `data:` y cierre `data: [DONE]`.** El `usage` del último chunk es la fuente de
  `message.cost_cop` (D-T12.1 en [docs/03](../03-modelo-datos.md#kb_document-kb_chunk-conversation-y-message-el-asistente-e12)).
  El caché de prompts (`prompt_cache_hit_tokens`) existe y es la palanca del costo mentioned en
  [docs/02:78](../02-estimaciones.md#costo-del-llm).
- **Embeddings: `intfloat/multilingual-e5-small`, 384 dimensiones, MIT, local.** El dueño fijó el pin el
  2026-10-06. El embedding es una decisión **separada** del LLM porque DeepSeek no expone embeddings
  ([Contexto](#contexto)), y **va pineado aparte** por una razón concreta: la dimensión es una propiedad
  del modelo y bloquea la migración de T2.

  | Opción | Modelo | Dimensión | Licencia | Costo | Estado |
  |---|---|---|---|---|---|
  | **A (elegida)** | `intfloat/multilingual-e5-small` | **384** | MIT | Cero por llamada: corre en el job de ingesta, los fragmentos no salen del servidor | **Pin vigente** |
  | B | `BAAI/bge-m3` | **1024** | MIT | Cero por llamada; multilingüe más fuerte, cuatro veces la columna | Descartada, documentada como alternativa |

  A gana por el costo de la columna: 384 mantiene `vector(384)` y el HNSW baratos para ~10 000 fragmentos
  ([docs/02:90](../02-estimaciones.md)), y el recall extra de B no está justificado mientras la evaluación
  no lo pida. Ambas son multilingües (el corpus y las preguntas son en español,
  [docs/06:379](../06-diseno-detallado.md)) y las dos se ejecutarían en el mismo proceso que ya indexa el
  corpus, sin un segundo tercero al que enviar datos ([ADR-0007:39](0007-llm-por-api.md)).
- **T2 escribe `vector(384)` + HNSW coseno** (`vector_cosine_ops`, [docs/03:558](../03-modelo-datos.md)).
  El pin ya no es owner-confirmable: es un número. **La dimensión y la licencia se reconfirman contra la
  ficha del modelo antes de escribir la migración**; este ADR fija el criterio, no la confianza.
- **T8 confirma o reescribe.** Si la evaluación de 30 preguntas degrada el recall de A, volver a B —o
  migrar a un embedding hosteado— **reescribe la columna `vector(<dim>)`**, y eso es su propia unidad de
  trabajo, con su migración y su PR.
- **Precios: los de [docs/02:78](../02-estimaciones.md#costo-del-llm) (Haiku 4.5, USD 1 / 5) quedan
  obsoletos** y no son la base del presupuesto. T8 escribe los precios reales de `deepseek-chat` y del
  embedding elegido en esa misma línea. Esa edición es de T8, no de T0.
- **T8 conserva la última palabra** ([ADR-0007:15](0007-llm-por-api.md)): la evaluación de 30 preguntas
  puede cambiar el LLM. Cambiar el modelo de **embeddings** es más caro: reescribe la columna, y por eso
  se migra como su propia unidad de trabajo, nunca en silencio.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Claude Haiku 4.5 como provisional | El dueño lo descartó (2026-10-06): el SDK `anthropic` queda prohibido y el objetivo es el menor costo. [docs/02:78](../02-estimaciones.md#costo-del-llm) lo conserva solo como referencia histórica hasta T8 |
| Esperar a T8 antes de fijar nada | La dimensión del embedding bloquea la migración de T2; sin pin, T2 escribe una columna que después se reescribe |
| Un único proveedor para LLM y embeddings | DeepSeek no publica endpoint de embeddings ([Contexto](#contexto)), así que no existe la opción |
| Embeddings alojados de un segundo proveedor | Envía los fragmentos del corpus a un tercero adicional, con el consentimiento y el mínimo de datos de [ADR-0007:39](0007-llm-por-api.md) que eso obliga a revisar, a cambio de nada que valga ese riesgo de privacidad |
| `bge-m3` (1024) sobre `multilingual-e5-small` (384) | Descartada por el dueño (2026-10-06): 384 mantiene la columna y el HNSW baratos para ~10 000 fragmentos ([docs/02:90](../02-estimaciones.md)). B queda documentada aquí y es el primer candidato si el recall de A falla en la evaluación de T8 |

## Consecuencias

**Positivas**

- T2 puede escribir `vector(384)` con un número, no con una incógnita.
- Cero dependencias Anthropic: el `pyproject.toml` del servidor no crece con un SDK de proveedor.
- El costo diario real se puede medir desde el `usage` del propio proveedor y escribir en `message.cost_cop`.

**Negativas / costos aceptados**

- Un **embedding local agrega un modelo a las dependencias del `worker`** (torch/transformers o similar). Es
  el precio de no enviar el corpus a un tercero; el `worker` ya es el proceso que indexa.
- Si T8 cambia el modelo de embeddings, la columna se reescribe con una migración propia.
- La calidad en español de `deepseek-chat` está **sin medir**: la evaluación de 30 preguntas de T8 es la
  que la juzga, no esta ADR.

## Relacionado

[ADR-0007](0007-llm-por-api.md), [ADR-0008](0008-rag-pgvector.md),
[03-modelo-datos](../03-modelo-datos.md#kb_document-kb_chunk-conversation-y-message-el-asistente-e12),
[06 §9](../06-diseno-detallado.md#9-asistente-agronómico),
[02-estimaciones](../02-estimaciones.md#costo-del-llm),
`odd/tasks/techcamp-v2-e12-assistant.md` (D-T12.2)
