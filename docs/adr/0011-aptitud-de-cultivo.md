# ADR-0011: Recomendación de cultivo reformulada como aptitud y diferida a v2.x

- **Estado:** Aceptada (implementación diferida)
- **Fecha:** 2026-09-22

## Contexto

El clasificador de la v1 predice qué cultivo **se siembra** según los datos EVA, que reflejan costumbre y mercado, no aptitud. Acierto real: 7,08 % (Maíz 0 %). Además, recomendar un cultivo es una decisión de alto impacto económico para el productor.

## Decisión

- En v2.0 solo se muestran **restricciones duras** explicables (pH, textura, drenaje, altitud) por cultivo.
- En v2.x, un score de aptitud por regresión del **rendimiento relativo** (EVA + cosechas registradas en la bitácora de la v2), validado con la correlación de Spearman contra rendimientos reales y comparado con la media municipal.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Mantener el clasificador de la v1 | Evidencia de que no funciona |
| Reentrenar el clasificador con más EVA | Sigue respondiendo la pregunta equivocada (qué se siembra, no qué rinde) |
| Eliminar la funcionalidad | La aptitud tiene valor cuando se apoya en datos de rendimiento reales; la bitácora los va a producir |

## Consecuencias

**Positivas**

- No se muestra una recomendación sin respaldo.
- La bitácora de la v2 genera los datos que hacen falta para hacerlo bien.

**Negativas / costos aceptados**

- La funcionalidad más visible de la v1 se reduce en v2.0.

## Relacionado

[08-ml](../08-ml.md#aptitud-de-cultivo-diferido-a-v2x)
