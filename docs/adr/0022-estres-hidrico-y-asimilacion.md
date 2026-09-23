# ADR-0022: Estrés hídrico por parcela y asimilación ponderada del sensor

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

La [investigación de campo](../investigacion/tecnificacion-campo.md#4-matriz-de-brechas) encontró cuatro brechas frente a FAO-56 en el diseño de riego y alertas ([ADR-0009](0009-riego-fao56.md)):

- **G01:** el estrés hídrico era "humedad < umbral del cultivo", un porcentaje volumétrico fijo guardado en el catálogo `crop`. FAO-56 define el estrés con `Dr > RAW` (ecs. 82–84), que depende del suelo. Un 20 % fijo queda por encima del umbral de estrés en un franco arenoso y cerca o por debajo del punto de marchitez en una arcilla (Tabla 19: θWP de 0,20–0,24).
- **G02:** con θFC = 0,23 y θWP = 0,09, el umbral del maíz (p = 0,55) es 15,3 %. El escenario A abría la alerta cerca de 20 %, antes de que hubiera estrés.
- **G03:** la humedad del sensor reemplazaba el `Dr` modelado, con un sensor a 10 cm. La raíz del maíz llega a 1,0–1,7 m, y un sensor capacitivo tiene un RMSE en campo de 0,055–0,191 m³/m³ sin calibración de campo y de 0,005–0,036 con ella (Strypsteen et al., 2026).
- **G18:** el ñame no tiene Kc en la Tabla 12 de FAO-56, y el plátano solo tiene la aproximación del banano.

## Decisión

- **Estrés por parcela.** `water_stress` significa `Dr > RAW` (Ks < 1), con `RAW = p × TAW`, `TAW` del suelo de la parcela y `p = p_tabla + 0,04 × (5 − ETc)` acotado a 0,1–0,8. Se elimina el umbral fijo del catálogo `crop`.
  - Con un sensor representativo (lectura válida, profundidad representativa y calibración de campo; es decir, `K > 0`), la regla sobre lecturas compara la humedad con `θ_estrés = θFC − p × (θFC − θWP)`, que el job de riego recalcula cada día (`water_balance_daily.stress_moisture_pct`). Conserva duración e histéresis.
  - Sin ese sensor, el balance diario abre `water_stress` cuando `Dr > RAW`.
  - Si el suelo no tiene θFC y θWP medidos, se usan los valores medios de su textura en la Tabla 19 de FAO-56.
- **Asimilación ponderada.** `Dr = Dr_modelo + K × (Dr_obs − Dr_modelo)`. `K = 0` sin calibración de campo o si la profundidad del sensor no es representativa (cerca de Zr/2, o dos profundidades promediadas); `K = 0,5` como valor inicial con calibración de campo (`calibration.kind = field`). Se sigue registrando el error modelo − observado.
- **Origen del Kc.** `crop.kc_source` (`fao56`, `local`, `approximate` o `none`). Con `none` no se recomienda lámina; con `approximate` la recomendación lo indica en su `rationale`.
- Detalle en [06 §5](../06-diseno-detallado.md#5-riego-balance-hídrico-fao-56) y [03](../03-modelo-datos.md#water_balance_daily-estrés-hídrico-por-parcela).

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Umbral volumétrico fijo por cultivo | Contradice FAO-56: el mismo porcentaje es estrés en un suelo arenoso y agua casi no disponible en uno arcilloso (G01, G02) |
| El sensor reemplaza el `Dr` modelado | Traslada al balance todo el error del sensor, que sin calibración de campo es mayor que el propio umbral, y un sensor superficial no ve la zona de raíces (G03) |
| Filtro de Kalman desde ahora | Necesita estimar las varianzas del modelo y del sensor, y todavía no hay datos de campo para hacerlo. Es el paso siguiente si el error modelo − observado sigue alto con `K` fijo |

## Consecuencias

**Positivas**

- Alertas y recomendaciones coherentes con FAO-56 en cualquier suelo, y el mismo criterio con sensor o sin él.
- Un sensor mal calibrado o mal ubicado ya no puede disparar ni ocultar una alerta por sí solo.
- Los escenarios del simulador se pueden verificar con aritmética FAO-56.

**Negativas / costos aceptados**

- La regla `water_stress` depende de que el job diario de riego haya calculado el θ_estrés del día.
- `K = 0,5`, la tolerancia de profundidad y los valores de `p` y Zr por cultivo están pendientes de validación agronómica.
- El ñame queda sin recomendación de lámina hasta tener un Kc local.

## Relacionado

[ADR-0009](0009-riego-fao56.md), [investigación: tecnificación del campo](../investigacion/tecnificacion-campo.md), [11-metricas](../11-metricas.md)
