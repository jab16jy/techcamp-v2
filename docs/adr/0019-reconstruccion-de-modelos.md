# ADR-0019: Reconstruir los modelos de ML desde cero; de la v1 solo se reutiliza código revisado

- **Estado:** Aceptada
- **Fecha:** 2026-09-22
- **Reemplaza a:** [ADR-0010](0010-rescate-y-gobierno-de-modelos.md)

## Contexto

El ADR-0010 proponía rescatar el modelo de inundación de la v1 como candidato. Después se confirmaron dos hechos:

1. **Los datos y artefactos de la v1 se perdieron.** El dataset procesado (CHIRPS, ERA5, UNGRD, HDX, DesInventar) y los `.joblib` entrenados estaban en otra máquina (`CLIMATE_DATA_DIR=/home/jabyn/Downloads/...`) a la que ya no hay acceso. Las métricas de `model_metrics.json` no se pueden reproducir.
2. **La auditoría del código encontró fallas de método** en los tres modelos: fuga de datos por los negativos difíciles y por duplicar filas antes de partir, métricas medidas con una frecuencia artificial de positivos, hiperparámetros sin búsqueda y un LSTM sin línea base. Detalle en [08-ml](../08-ml.md#auditoría-de-la-v1).

## Decisión

- No se rescata **ningún** modelo ni métrica de la v1 como referencia que haya que superar.
- Cada modelo de la v2 se construye desde fuentes públicas reproducibles siguiendo el protocolo del [ADR-0020](0020-protocolo-de-experimentacion-ml.md).
- Se reutiliza solo **código revisado**: descargadores de fuentes, `decide_promotion` sin `force_promote`, selección de calibración en validación, matriz de umbrales y el patrón de climatología calculada con el período de train.
- Un modelo se construye solo si mejora una **decisión concreta del productor** frente a una regla simple (inventario M1–M6 en [08-ml](../08-ml.md#inventario-de-modelos-de-la-v2)). Antes del piloto solo se construye M2 (riesgo de inundación).
- Mantiene las reglas de gobierno del ADR-0010: sin `force_promote`, trazabilidad por `model_version` y paridad de features entre entrenamiento y producción.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Reentrenar con el código de la v1 tal cual | Reproduciría la fuga de datos y las métricas engañosas |
| Tomar las métricas de la v1 como línea base | No se pueden reproducir y se midieron con una frecuencia artificial de positivos |
| No usar ML en la v2 | Se pierden mejoras reales donde los datos lo permiten; el protocolo decide caso por caso |

## Consecuencias

**Positivas**

- Cada métrica publicada se puede reproducir desde cero en cualquier máquina.
- Los modelos se atan a decisiones del productor, no a la demostración técnica.

**Negativas / costos aceptados**

- Hay que regenerar los datasets (descarga de CHIRPS y del histórico climático: horas de cómputo y almacenamiento).
- Al inicio del piloto habrá menos modelos visibles: las líneas base y reglas cubren mientras tanto.

## Relacionado

[08-ml](../08-ml.md), [ADR-0020](0020-protocolo-de-experimentacion-ml.md), [ADR-0011](0011-aptitud-de-cultivo.md)
