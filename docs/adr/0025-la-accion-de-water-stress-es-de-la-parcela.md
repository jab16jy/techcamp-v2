# ADR-0025: La acción de `water_stress` es de la parcela, no del ciclo de cultivo

- **Estado:** Aceptada
- **Fecha:** 2026-10-03

## Contexto

[ADR-0024](0024-metricas-de-impacto-y-adopcion-digital.md) decidió que el componente `risk_management` del
índice de adopción digital contara las alertas seguidas de una acción registrada dentro de 48 h: una entrada
con su `alert_id` o, en `water_stress` con riego, un riego. La frase "un riego" quedó sin decir **de
dónde**.

Implementar el cálculo (E11 T3) dejó la ambigüedad al descubierto. La vista de solo lectura
`metrics_plot_alert_action` filtra el riego por `plot_id` (`alert` no tiene `crop_cycle_id`), así que un
riego registrado en la parcela cuenta como acción de cualquier alerta de estrés hídrico de esa parcela,
esté o no vinculada al ciclo de cultivo que la entrada traiga.

La revisión por recibo del linaje `review-9b9197b7eee80109` (E11 T3) levantó el hallazgo CRITICAL
`R3-reliability.alert-action.cross-plot`, que pedía acotar la cláusula al ciclo de cultivo: un riego de
otro ciclo no debería marcar como atendida una alerta que no le corresponde.

Tres documentos que poseen la decisión coinciden en que ninguno define un filtro por ciclo de cultivo:
[11-metricas](../11-metricas.md#2-adopción-índice-de-adopción-digital) ("un riego registrado"),
[03-modelo-datos](../03-modelo-datos.md#logbook_entry-la-tabla-que-se-sincroniza-offline) (`crop_cycle_id`
admite `null`) y el propio ADR-0024.

Dos hechos del modelo cortan la vía del alcance por ciclo:

- **`logbook_entry.crop_cycle_id` admite `null`.** El teléfono manda el ciclo que tiene en caché y
  a menudo no manda ninguno. Una entrada de riego sin ciclo es una respuesta válida al estrés
  hídrico, y el ciclo que el teléfono tuvo en mano no dice a qué alerta responde.
- **`alert` no tiene columna `crop_cycle_id`.** Acotar exigiría un cruce temporal —el ciclo activo de la
  parcela en la fecha de apertura— que ningún documento define, y que además daría resultados distintos
  según cómo se resuelvan los empates cuando una parcela tiene ciclos consecutivos.

El dueño del producto dictaminó el 2026-10-03 (**D-T3.1**): la acción de `water_stress` es **de la
parcela**.

## Decisión

- **La acción de `water_stress` es de la parcela, no del ciclo de cultivo.** El riego cuenta como acción
  registrada a tiempo si está en la bitácora **de esa parcela** dentro de la ventana de 48 h de la alerta,
  sin filtro por ciclo de cultivo.
- **Una entrada con `crop_cycle_id` nulo cuenta igual.** La ausencia de ciclo no invalida la acción.
- **El alcance no se restringe por tipo de entrada.** `alert_id` es la marca de la acción registrada tras
  la alerta, y aplica a cualquier tipo de entrada
  ([03](../03-modelo-datos.md#logbook_entry-la-tabla-que-se-sincroniza-offline)): reconocer la alerta no
  basta, pero registrar algo en respuesta sí.
- **La ventana sigue siendo de 48 h** para todas las reglas (D-T0.6,
  [11-metricas](../11-metricas.md#2-adopción-índice-de-adopción-digital)), y `alert_rule` no tiene columna
  de ventana de acción: `min_duration_min` gobierna cuánto debe durar una violación antes de que la regla
  dispare, no cuánto tiene el productor para responder.
- Detalle en [11 §2](../11-metricas.md#2-adopción-índice-de-adopción-digital).

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Acotar la acción al ciclo de cultivo de la alerta | `alert` no tiene `crop_cycle_id`: exigiría un cruce temporal que ningún doc define. Y `crop_cycle_id` admite `null`, así que descartaría el riego registrado por un teléfono sin caché de ciclo, que es una respuesta legítima al estrés hídrico |
| Exigir que la entrada de riego lleve `alert_id` | Elimina el caso que la decisión original quiere recoger: el productor riega y registra el riego sin abrir la alerta. La bitácora registra fechas, no horas, y el riego es la respuesta natural al estrés hídrico |
| Dejar la ambigüedad para que cada implementación la resuelva | Dos lecturas de la misma vista podrían contar acciones distintas y los números del índice dejarían de ser comparables. Una ambigüedad de la que depende un indicador se escribe |
| Una ventana de acción por regla | `alert_rule` no tiene columna para ella y ningún documento define ventanas por regla. Sería un modelo nuevo, no una aclaración |

## Consecuencias

**Positivas**

- El cálculo queda determinado: dos lecturas de la misma vista devuelven el mismo número de acciones.
- No se pierden riegos registrados sin ciclo, que son la forma más común de responder al estrés hídrico
  desde el campo sin conexión.
- La aclaración no cambia una sola línea de SQL: la vista ya filtraba por parcela, así que el docstring y
  las pruebas pasan a describir lo que el código ya hacía.

**Negativas / costos aceptados**

- Un riego registrado dentro de la ventana marca como atendida cualquier alerta de estrés hídrico de esa
  parcela que solape la ventana. Es el comportamiento buscado: la alerta y el riego son la misma respuesta
  al mismo problema en la misma parcela.
- La relación entre una alerta concreta y el riego que la atendió no queda registrada. Para eso haría falta
  `alert_id` en la entrada de riego, que es un cambio de modelo de datos, no una aclaración.

## Relacionado

[ADR-0024](0024-metricas-de-impacto-y-adopcion-digital.md),
[ADR-0022](0022-estres-hidrico-y-asimilacion.md),
[ADR-0023](0023-parcelas-con-riego-y-secano.md),
[11-metricas](../11-metricas.md),
[03-modelo-datos](../03-modelo-datos.md#logbook_entry-la-tabla-que-se-sincroniza-offline)
