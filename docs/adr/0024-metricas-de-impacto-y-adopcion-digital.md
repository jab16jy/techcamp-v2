# ADR-0024: Métricas de impacto y adopción digital

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

Las métricas de [11-metricas](../11-metricas.md) son la forma de demostrar que el campo se tecnificó (E11). La [investigación de campo](../investigacion/tecnificacion-campo.md#4-matriz-de-brechas) encontró cinco brechas:

- **G04:** varias métricas de impacto usaban datos que el modelo no guardaba. El margen bruto pedía un "precio registrado en la cosecha" que `logbook_entry` no tenía; las pérdidas por evento, observaciones "ligadas a alertas" sin `alert_id`; la línea base, una encuesta de inscripción sin entidad; el costo por kg y el ODS 2.3.1 (producción por día de trabajo), jornales que no existían.
- **G05:** el "índice de tecnificación" medía el uso de la plataforma (nodo transmitiendo, bitácora, recomendación seguida y alertas reconocidas), no la tecnificación. Su componente `risk_management` contaba el reconocimiento de la alerta, no la acción tomada, y no se relaciona con el enfoque de cinco aspectos de la extensión agropecuaria (Ley 1876, art. 25).
- **G09:** la "anticipación de alertas" se medía en horas, pero los modelos de riesgo (M2 y M3) predicen por municipio y mes.
- **G10:** la meta de error del balance (< 5 puntos de humedad volumétrica) queda dentro del error de un sensor capacitivo sin calibración de campo (5,5–19 puntos).
- **G11:** la "brecha de rendimiento" comparaba con la media EVA municipal. En la literatura, la brecha de rendimiento es la distancia al rendimiento potencial; además, los productores que adoptan se autoseleccionan y EVA no es una muestra comparable, así que esa diferencia no es el efecto de la tecnología.

Además, "línea base" tenía dos significados en el glosario: la heurística que un modelo de ML debe superar y la encuesta de la parcela al inscribirse.

## Decisión

- **Campos de impacto en la bitácora:** `sold_kg` y `sale_price_cop_per_kg` en la cosecha, `labor_days` en las labores y `alert_id` opcional en cualquier entrada. Una observación con `alert_id` registra la pérdida por la alerta.
- **Encuesta de inscripción** (`plot_baseline`): cultivo y rendimiento del último ciclo, costos aproximados, práctica de riego y fecha de inscripción. "Línea base" (`baseline`) queda solo para ML.
- **Impacto contra la encuesta:** cambio de rendimiento antes y después por parcela; en un piloto, diferencias en diferencias con parcelas de control. La "brecha de rendimiento" pasa a ser el **rendimiento relativo municipal** (`relative_yield = rendimiento / mediana EVA`), que da contexto y no se presenta como impacto.
- **Producción por jornal** (`Σ yield_kg / Σ labor_days`) como aproximación al ODS 2.3.1.
- **Índice de adopción digital** (`digital_adoption_index`) en lugar de "índice de tecnificación". `risk_management` cuenta las alertas seguidas de una acción registrada dentro de 48 h (una entrada con su `alert_id` o, en `water_stress` con riego, un riego); reconocer la alerta no basta. **La acción de `water_stress` es de la parcela, no del ciclo de cultivo** (D-T3.1, E11 T3): el riego cuenta si está registrado en esa parcela dentro de la ventana, sin qualifier de ciclo, y una entrada con `crop_cycle_id` nulo cuenta igual. Alinearlo con la clasificación de usuarios del MADR (niveles 1–4) es una decisión abierta del dueño del producto.
- **Anticipación:** en horas solo para las reglas de pronóstico; para M2 y M3, "aviso emitido antes del mes del evento".
- **Error del balance:** se reporta junto al RMSE de la calibración del sensor (`calibration.rmse_pct`); en el seminario se mide contra la verdad del simulador.
- Detalle en [11-metricas](../11-metricas.md) y [03](../03-modelo-datos.md#logbook_entry-la-tabla-que-se-sincroniza-offline).

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Mantener la "brecha de rendimiento" contra la media EVA como indicador de impacto | No es una brecha en el sentido de la literatura y confunde autoselección con efecto (G11) |
| Tomar el precio de venta siempre de SIPSA | El precio mayorista no es el que recibe el productor en la finca. Queda como respaldo opcional cuando no hay precio registrado (G28, fuera de este alcance) |
| Mantener el nombre "índice de tecnificación" y cambiar solo `risk_management` | El nombre promete medir prácticas adoptadas y los cinco aspectos de la extensión, que el índice no mide (G05) |
| Alinear ya el índice con la clasificación 1–4 del MADR | Depende de una decisión del dueño del producto y del programa de extensión; el registro oficial no se puede simular en el seminario |

## Consecuencias

**Positivas**

- Cada métrica de impacto se calcula con campos que existen en el modelo de datos.
- El impacto se compara con la propia parcela, no con una media que no es comparable.
- El índice dice lo que mide y premia la acción ante una alerta, no el toque de "reconocer".

**Negativas / costos aceptados**

- La bitácora pide más datos (venta, jornales, vínculo con la alerta): más fricción para el productor, que la interfaz debe hacer opcional y rápida.
- La encuesta de inscripción es autorreportada y aproximada.
- Sin un jornal de referencia, el costo por kg no incluye el valor de la mano de obra familiar; los jornales solo alimentan la producción por jornal.
- No hay todavía un indicador de prácticas adoptadas; queda como trabajo futuro.

## Relacionado

[11-metricas](../11-metricas.md), [ADR-0023](0023-parcelas-con-riego-y-secano.md), [investigación: tecnificación del campo](../investigacion/tecnificacion-campo.md)
