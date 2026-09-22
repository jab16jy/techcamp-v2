# ADR-0010: Rescate selectivo de modelos de la v1 y compuerta de promoción sin excepciones

- **Estado:** Reemplazada por [ADR-0019](0019-reconstruccion-de-modelos.md)
- **Fecha:** 2026-09-22

> Reemplazada el 2026-09-22: los datasets y artefactos de la v1 se perdieron y la auditoría del código encontró fuga de datos. Ver ADR-0019.

## Contexto

Métricas de la v1 (`model_metrics.json`):

- Riesgo de inundación: PR-AUC 0,654 frente a 0,407 de la heurística.
- Riesgo de sequía: PR-AUC 0,490, **menor** que la heurística (0,529), y aun así quedó `promoted` con `force_promote=True`.
- Recomendador de cultivos: 7,08 % de acierto real.

La v1 entrenó con ERA5/CHIRPS, que no están disponibles al día en producción: hay riesgo de sesgo entre entrenamiento y producción.

## Decisión

- Se rescatan el **pipeline** de riesgo (dataset, negativos difíciles, calibración, matriz de umbrales, auditoría y sus tests) y el modelo de **inundación como candidato**, a reentrenar.
- El modelo de **sequía no** se rescata: en producción se usa la heurística más la humedad medida.
- Se elimina `force_promote`. Compuerta: PR-AUC > línea base **y** Brier ≤ línea base, en una partición temporal.
- Las features se calculan con un único módulo compartido entre `ml/` y `server/`, con las mismas fuentes que en producción (archivo histórico de Open-Meteo).
- Registro de `model_version` con métricas, línea base, hash del dataset y commit; cada predicción referencia su versión.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Rescatar todos los modelos tal cual | Se publicaría un modelo peor que su heurística y otro con 7 % de acierto |
| Descartar todo el ML | Se perdería un pipeline con buena disciplina de evaluación y un modelo de inundación que sí supera su línea base |
| MLflow u otra plataforma de ML | Demasiado para 2–3 modelos; una tabla `model_version` y los artefactos en S3 alcanzan |

## Consecuencias

**Positivas**

- Solo llega a producción lo que demuestra valor contra algo simple.
- Trazabilidad completa de cada alerta hasta su modelo.

**Negativas / costos aceptados**

- Reentrenar con features de Open-Meteo puede bajar las métricas de la v1; si pasa, se usa la heurística.
- El riesgo de sequía queda con menos "IA" visible al inicio.

## Relacionado

[08-ml](../08-ml.md)
