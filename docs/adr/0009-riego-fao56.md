# ADR-0009: Riego con balance hídrico FAO-56 corregido por humedad de suelo medida

- **Estado:** Aceptada. Modificada por [ADR-0022](0022-estres-hidrico-y-asimilacion.md) (estrés hídrico por parcela y asimilación ponderada del sensor) y [ADR-0023](0023-parcelas-con-riego-y-secano.md) (parcelas de secano)
- **Fecha:** 2026-09-22

## Contexto

En la v1, `irrigation_service.py` usa `ET0_BASE`, una **constante por cultivo** (por ejemplo, Maíz = 5,2 mm/día), no una ET0 diaria real. Por eso la recomendación no responde al clima del día.

## Decisión

- ET0 diaria FAO Penman-Monteith desde Open-Meteo (`et0_fao_evapotranspiration`) por celda climática.
- Coeficiente de cultivo único (Kc por etapa, FAO-56) y balance diario del agotamiento en la zona de raíces (TAW, RAW, lluvia efectiva, riegos de la bitácora).
- Cuando hay humedad de suelo válida del sensor, corrige el agotamiento modelado de ese día con un peso según su calibración y su profundidad ([ADR-0022](0022-estres-hidrico-y-asimilacion.md)). El error modelo-observación se registra como métrica.
- La recomendación incluye lámina, minutos según el caudal del sistema y un `rationale` con los números usados. Una parcela de secano recibe en su lugar una recomendación sin lámina ([ADR-0023](0023-parcelas-con-riego-y-secano.md)).
- Detalle en [06 §5](../06-diseno-detallado.md#5-riego-balance-hídrico-fao-56).

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Mantener ET0 constante | Incorrecto: ignora el clima del día |
| Calcular Penman-Monteith propio con estaciones | No hay estaciones completas en las fincas; Open-Meteo ya entrega ET0 FAO |
| Kc dual (FAO-56 cap. 7) | Más parámetros de los que el piloto puede calibrar; se evalúa después |
| Modelo ML de riego | No hay datos de entrenamiento; el método físico es explicable y validado |

## Consecuencias

**Positivas**

- Recomendación físicamente fundamentada, explicable y probada contra los ejemplos de FAO-56.
- El sensor mejora el modelo; sin sensor igual hay recomendación (marcada como tal).

**Negativas / costos aceptados**

- Depende de parámetros de suelo (capacidad de campo, punto de marchitez) que SoilGrids estima con error; se corrigen con laboratorio o con la calibración en campo.
- Los valores de Kc deben revisarse para variedades locales.

## Relacionado

[ADR-0022](0022-estres-hidrico-y-asimilacion.md), [ADR-0023](0023-parcelas-con-riego-y-secano.md), [08-ml](../08-ml.md), [11-metricas](../11-metricas.md)
