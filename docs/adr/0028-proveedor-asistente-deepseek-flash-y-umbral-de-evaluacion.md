# ADR-0028: Proveedor del asistente confirmado — `deepseek-flash` sin thinking, umbral de evaluación y política de licencia del corpus

- **Estado:** Aceptada
- **Fecha:** 2026-10-23
- **Alcance:** E12 (T8 U2). Cierra las Open questions 1 y 2 del épico y confirma o descarta los dos condicionales que [ADR-0026](0026-proveedor-provisional-e12.md) dejó a T8: la reescritura de dimensión y la búsqueda híbrida.

## Contexto

[ADR-0026](0026-proveedor-provisional-e12.md) pineó `deepseek-chat` como LLM provisional y
`intfloat/multilingual-e5-small` (384, MIT, local) como embedding, y dejó a T8: los precios reales de
[docs/02:78](../02-estimaciones.md#costo-del-llm), el cierre de
[docs/README:68](../README.md), la confirmación o reescritura de la dimensión y la decisión de
búsqueda híbrida ([ADR-0008:19](0008-rag-pgvector.md)). Ningún documento decía qué puntaje promueve
un proveedor (Open question 1) ni qué licencias admite el corpus (Open question 2,
[ADR-0008:32](0008-rag-pgvector.md)).

El 2026-10-23 el dueño delegó ambas decisiones: *"usa las decisiones que tengan sentido con el épico
12 y los docs"*, y fijó el proveedor: **DeepSeek V4 Flash / V4.1 Flash**. Tres hechos verificados ese
mismo día con ctx7 (biblioteca `/websites/api-docs_deepseek`, fuente oficial) obligan a renombrar el
pin de ADR-0026 aunque el proveedor no cambie:

| Hecho | Evidencia |
|---|---|
| **`deepseek-chat` está retirado** | El 2026-04-24 la API pasó a V4 y avisó que los nombres legacy `deepseek-chat`/`deepseek-reasoner` se descontinuarían el **2026-07-24**; durante la transición apuntaban al modo non-thinking/thinking de `deepseek-v4-flash`. Hoy la tabla oficial de modelos lista **`deepseek-flash`** (DeepSeek-V4.1-Flash) y `deepseek-v4-pro` |
| **El thinking viene activado por default** | `POST /chat/completions`: `thinking.type` admite `enabled` (default) o `disabled` |
| **Precios vigentes (USD por 1M de tokens)** | `deepseek-flash`: input cache-hit $0.003 off-peak / $0.006 peak; input cache-miss $0.15 off-peak / $0.30 peak; output $0.60 off-peak / $1.20 peak. Pico: 01:00–04:00 y 06:00–10:00 UTC, lunes a viernes |

Mientras tanto el arnés de evaluación (T8 U1) quedó cerrado y verificado: replay de las 30 preguntas
por el `retrieve_fragments` y el `build_prompt` reales, reporte de evidencia sin score ni veredicto,
y una advertencia medida — con el `SeminarHashEmbedder` (el stand-in del seminario) las 30 preguntas
recuperan **0 fragmentos**, así que ninguna conclusión de calidad sale del perfil seminario: la
corrida graduada necesita el modelo real de este pin (ADR-0026) y el corpus con licencia revisada.

## Decisión

### 1. LLM: `deepseek-flash`, una llamada, thinking desactivado

El proveedor sigue siendo DeepSeek (tope del dueño, API compatible con OpenAI, el cliente de T5 ya
habla SSE con `data:`/`data: [DONE]`); lo que cambia es el **nombre del modelo** en el cableado:
`deepseek-chat` murió el 2026-07-24 y una configuración que lo nome sería una llamada que hoy falla.
El modelo es **`deepseek-flash`** (DeepSeek-V4.1-Flash) — la opción barata de la tabla vigente, con
1M de contexto para el prompt de hechos + fragmentos.

La llamada del asistente va **sin thinking** (`"thinking": {"type": "disabled"}`):
[ADR-0007](0007-llm-por-api.md) define una sola llamada que **explica hechos ya calculados y no
decide**; el razonamiento largo no aporta y cuadruplica el costo por token de salida. El modo
non-thinking es además el modo al que el retirado `deepseek-chat` apuntaba, así que el contrato de
llamada de T5 no cambia de forma, solo de parámetro.

**Precios reales** (tabla oficial del 2026-10-23, USD por 1M de tokens; se reconfirman al desplegar):

| Concepto | Off-peak | Peak |
|---|---|---|
| Input, cache hit | $0.003 | $0.006 |
| Input, cache miss | $0.15 | $0.30 |
| Output | $0.60 | $1.20 |

Con la fórmula de [docs/02:78](../02-estimaciones.md#costo-del-llm) (6,3 M input + 0,72 M output al
día en el año 3), el día cuesta **≈ USD 0,45–0,90 con cache** y **≈ USD 2,75 en el peor caso**
(peak y cache miss total), contra ≈ USD 10/día de la referencia Haiku que reemplaza. El caché de
prompts (`prompt_cache_hit_tokens`) es la palanca: el bloque sistema + hechos + fragmentos es
repetitivo por construcción (D-T12.6).

**Tarjeta en COP:** `message.cost_cop` y el tope de presupuesto son pesos (D-T12.1, D-T6.1), así que
la tarjeta que cablea T8 convierte a la **TRM del 2026-10-23: COP 3.185,01/USD**
([Banco de la República](https://suameca.banrep.gov.co/estadisticas-economicas/informacionSerie/1/tasa_cambio_peso_colombiano_trm_dolar_usd)),
tomando **siempre las tarifas peak de cache miss** — la tarjeta conservadora: un costo registrado no
puede quedar por debajo de la factura. Con esa regla: **input COP 955,50 / 1M, output COP 3.822,01
/ 1M**. El ahorro del cache y del off-peak queda del lado de la factura real, no del presupuesto que
el sistema reporta; si la costura aprende a leer el split hit/miss del `usage`, la tarjeta se afina
con su propia unidad de trabajo.

### 2. Embedding: el pin de ADR-0026 se confirma, **no hay migración de reescritura**

`intfloat/multilingual-e5-small` (384, MIT, local) sigue vigente y `vector(384)` no se toca. El
criterio de ADR-0026 era "si la evaluación degrada el recall de A, volver a B o migrar"; esa
evaluación aún no corre — lo único medido hasta hoy es que el **stand-in** no recupera nada, lo cual
no es evidencia contra el modelo real. Reescribir la columna sin esa evidencia sería pagar la
migración más cara del épico por un supuesto. La migración condicional queda escrita y esperando su
disparador, igual que en ADR-0026. `bge-m3` (1024) sigue siendo el primer candidato si el disparador
se enciende.

### 3. Open question 1 — el umbral de la evaluación, fijado **antes** de la corrida

La evaluación califica exactitud, citas y claridad por pregunta
([ADR-0007:15](0007-llm-por-api.md)). El umbral que promueve o degrada al proveedor, fijado ahora y
no leído de los resultados:

- **Descalifican al proveedor, con uno solo que ocurra:** (a) una respuesta que cite un documento que
  el replay no recuperó — una cita fabricada; (b) una respuesta que contradiga un hecho que el
  sistema calculó — el LLM explica, no corrige ([ADR-0007](0007-llm-por-api.md)); (c) una respuesta
  peor que la respuesta degradada en su propia pregunta — los hechos sin LLM son la línea base, y lo
  que se promueve tiene que superarla ([ADR-0019](0019-reconstruccion-de-modelos.md), la cultura de
  promoción del repo).
- **Promueven:** al menos **27 de 30 (90 %)** preguntas calificadas correctas — cita válida, hechos
  bien explicados, claridad suficiente para un productor. Con 30 preguntas, el margen de tres fallos
  separa "ruido de anotación" de "el proveedor no sirve", y el 90 % es el punto donde la degradación
  sin LLM (que existe y es segura, D-T12.3) sigue siendo mejor negocio que un asistente que falla
  una de cada diez explicaciones.
- **Si el proveedor descalifica o no llega al 90 %:** no se promueve; se prueba el siguiente
  candidato (`deepseek-v4-pro`, u otro tras una nueva ADR) contra el mismo umbral, y el perfil queda
  en su rama degradada mientras tanto.

El umbral es del dueño y así queda registrado: delegado el 2026-10-23, **revisable por el dueño
hasta antes de la corrida** — eso es exactamente lo que "fijar antes del run" protege: que nadie
ajuste el número después de ver los resultados.

### 4. Open question 2 — política de licencia del corpus

El corpus admite únicamente documentos cuya licencia permita **uso comercial y redistribución con
atribución**: CC-BY 4.0 (o más permisiva), CC-BY-SA y dominio público. **Quedan fuera** las licencias
NonCommercial (NC), NoDerivatives (ND) y todo material sin licencia declarada. Es la política que los
fixtures ya cumplen por construcción (CC-BY-4.0 de prueba) y la que cada documento real declara en
su fila de ingesta — `source_url`, `license` y `published_on` son columnas obligatorias desde
[ADR-0008:12](0008-rag-pgvector.md) y el loader las exige.

**Quién firma:** la política queda firmada aquí (delegación del dueño, 2026-10-23); la **lista de
documentos concretos** la firma el dueño documento por documento cuando se recolecte el corpus real —
cada alta registra su licencia, y un documento sin la suya no entra. Con esto T3 queda autorizado a
cargar el corpus real en cuanto la lista firmada exista.

### 5. Búsqueda híbrida: no se adopta; el disparador sigue en pie

[ADR-0008:19](0008-rag-pgvector.md) agrega BM25+vectores "si la evaluación muestra fallos con
términos exactos". Ese disparador exige la corrida graduada con el embedding real y el corpus real —
lo único que hoy puede medirse (el stand-in) recupera 0 y no mide nada. Decisión: **no se adopta
ahora**; sigue siendo el primer cambio de recuperación si la corrida graduada muestra preguntas de
término exacto fallidas. No hay código, no hay migración, no hay segundo índice.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Mantener `deepseek-chat` en el cableado | El nombre está retirado desde 2026-07-24: la llamada fallaría. El pin de ADR-0026 nombraba un alias, no un modelo |
| `deepseek-v4-pro` | Cuatro veces el input y tres veces el output del flash, para un asistente que explica hechos ya calculados; queda como segundo candidato si `deepseek-flash` falla el umbral |
| Otra casa (Gemini Flash, GLM, Qwen) | El dueño fijó el tope DeepSeek el 2026-10-06 y lo reiteró el 2026-10-23; cambiar de casa es una ADR nueva con su propia evaluación |
| Thinking activado | Cuadruplica el output y su costo por token; el asistente no decide ni planifica, explica (ADR-0007) |
| Fijar el umbral después de la corrida | Es exactamente lo que la Open question 1 prohíbe: un umbral leído de los resultados no mide, justifica |
| Admitir licencias NC/ND en el corpus | El producto es comercial (docs/02); una cita que no puede redistribuir el material que cita cierra la función de citas del asistente |
| Migrar ya a `bge-m3` (1024) | Ninguna evidencia contra el pin vigente; la columna se reescribe solo con el disparador de evaluación (ADR-0026) |

## Consecuencias

**Positivas**

- [docs/02:78](../02-estimaciones.md#costo-del-llm) deja la referencia Haiku y presupuesta con los
  números reales del proveedor: el peor caso (≈ USD 2,75/día) cabe en una fracción del tope que ya
  se configuraba.
- La evaluación de 30 preguntas tiene umbral antes de correr y un instrumento verificado que la
  produce; su veredicto será comparar, no decidir reglas sobre la marcha.
- T3 sabe exactamente qué documento puede entrar y quién firma; el corpus real se desbloquea con la
  lista, no con otra ADR.
- La dimensión `vector(384)` y el índice HNSW no se tocan: cero migración.

**Negativas / costos aceptados**

- La tarjeta COP fija la TRM de un día (2026-10-23): una TRM distinta al desplegar mueve el costo
  reportado; la regla conservadora (peak, cache miss) acota el error hacia arriba, nunca hacia abajo.
- La corrida graduada sigue siendo trabajo humano del dueño: la máquina produce la evidencia, el
  agrónomo pone la nota (ADR-0007:15).
- El `deepseek-flash` en modo no-thinking es más débil razonando que el V4-Pro: aceptado porque su
  trabajo es explicar, y el umbral lo descalifica si esa debilidad se nota en las 30.

## Relacionado

[ADR-0007](0007-llm-por-api.md), [ADR-0008](0008-rag-pgvector.md),
[ADR-0019](0019-reconstruccion-de-modelos.md), [ADR-0021](0021-perfil-seminario-local.md),
[ADR-0026](0026-proveedor-provisional-e12.md), [ADR-0027](0027-stream-asistente-e12.md),
[02-estimaciones](../02-estimaciones.md#costo-del-llm), [README de docs](../README.md),
`odd/tasks/techcamp-v2-e12-assistant.md` (T8, Open questions 1–2)
