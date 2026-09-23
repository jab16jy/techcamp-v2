# ADR-0023: Parcelas con riego y de secano

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

La propuesta central del diseño era la "decisión diaria de riego": una lámina en mm y los minutos según el caudal del sistema ([ADR-0009](0009-riego-fao56.md)). Supone que cada parcela tiene un sistema de riego.

La [investigación de campo](../investigacion/tecnificacion-campo.md#4-matriz-de-brechas) lo contradice (**G06**): según el CNA 2014, solo el 33,3 % de las UPA con cultivos usa algún tipo de riego, y los departamentos del Caribe no están entre los de mayor uso. La recomendación de lámina aplica a una minoría de las parcelas de la región. Además, el modelo de datos no guardaba el sistema de riego ni su eficiencia, aunque el diseño la declaraba "configurable por parcela" (G04).

El dueño del producto decidió el 2026-09-22 que el seminario y un piloto futuro atienden **las dos** clases de parcela.

## Decisión

- **Sistema de riego por parcela.** `plot.irrigation_system` (`none`, `drip`, `sprinkler` o `gravity`) y `plot.irrigation_efficiency`, con el valor por defecto de cada sistema. `none` es una parcela de secano, sin eficiencia ni caudal.
- **El mismo balance hídrico en las dos.** El job diario calcula `Dr`, RAW y `water_stress` igual; en secano el riego registrado es 0. Se mantienen la asimilación del sensor y la alerta de estrés de [ADR-0022](0022-estres-hidrico-y-asimilacion.md).
- **Recomendación según la parcela** (`irrigation_recommendation.kind`):
  - con riego: `irrigate`, `postpone`, `not_needed` o `no_kc`, como en [ADR-0009](0009-riego-fao56.md);
  - de secano: `rainfed`, sin lámina ni minutos. Trae el déficit frente a RAW, la lluvia pronosticada en 7 días y consejos de manejo (`advice`) de una tabla corta de reglas: aplazar la siembra, conservar la humedad, priorizar la cosecha. Las reglas están pendientes de validación agronómica.
- **Interfaz y métricas.** La tarjeta de decisión tiene una variante de secano. La productividad del agua de riego y el componente `decision` del índice solo aplican a parcelas con riego; en secano se reportan el rendimiento y los días en estrés.
- **Demo.** Nuevo escenario E del simulador: veranillo en maíz de secano, sin nodo.
- Detalle en [06 §5](../06-diseno-detallado.md#parcelas-de-secano), [03](../03-modelo-datos.md#plot-e-irrigation_recommendation-parcelas-con-riego-y-de-secano) y [11](../11-metricas.md).

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Solo parcelas con riego | Deja fuera a unos dos tercios de las UPA con cultivos (G06). El producto no serviría a la mayoría de los pequeños productores del Caribe |
| Solo parcelas de secano | Desperdicia la recomendación de lámina, que es la parte del diseño con mejor respaldo técnico (FAO-56), y excluye a los productores que sí riegan (hortalizas, plátano, arroz) |
| Un producto distinto para secano | Duplica el balance, las alertas y la interfaz. El balance es el mismo; solo cambia qué se recomienda con él |

## Consecuencias

**Positivas**

- La decisión diaria sirve a toda parcela, con o sin sistema de riego.
- El estrés hídrico y los días en estrés se miden igual en las dos, así que los indicadores son comparables.
- La eficiencia del riego deja de ser un valor sin campo en el modelo de datos.

**Negativas / costos aceptados**

- Los consejos de secano son reglas simples y todavía no están validadas por un agrónomo.
- La lluvia pronosticada a 7 días tiene más error que la de 48 h que usa la rama con riego.
- Las métricas y el índice tienen dos variantes según el sistema de riego.
- Quedan fuera por ahora la ventana de siembra según el pronóstico estacional (ENSO) y la cosecha de agua ([investigación, RQ6](../investigacion/tecnificacion-campo.md#rq6--requisitos-que-faltan-o-están-mal)).

## Relacionado

[ADR-0009](0009-riego-fao56.md), [ADR-0022](0022-estres-hidrico-y-asimilacion.md), [investigación: tecnificación del campo](../investigacion/tecnificacion-campo.md), [11-metricas](../11-metricas.md)
