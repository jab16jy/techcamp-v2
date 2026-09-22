# 00 — Glosario (lenguaje ubicuo)

El mismo término significa lo mismo en conversaciones, documentos, la interfaz y el código. La columna **Código** es el nombre obligatorio en el código, las tablas y la API.

## Dominio agrícola

| Término | Código | Definición |
|---|---|---|
| Organización | `organization` | Cooperativa, asociación, programa o productor individual. Es la unidad de permisos: todo dato pertenece a una organización. |
| Finca | `farm` | Predio de un productor. Tiene ubicación y municipio. |
| Parcela / lote | `plot` | Área cultivable con polígono dentro de una finca. Es la unidad de decisión: riego, alertas y métricas se calculan por parcela. |
| Cultivo | `crop` | Especie del catálogo (maíz, yuca, plátano…) con sus coeficientes Kc y umbrales. |
| Ciclo de cultivo | `crop_cycle` | Una siembra concreta de un cultivo en una parcela, desde la siembra hasta la cosecha. Una parcela tiene como máximo un ciclo activo. |
| Etapa fenológica | `growth_stage` | Inicial, desarrollo, media o final (FAO-56). Determina el Kc. |
| Bitácora | `logbook` | Registro de lo que hizo el productor: labores, insumos, riegos, costos, observaciones y cosechas. |
| Entrada de bitácora | `logbook_entry` | Un registro individual de la bitácora. |
| Cosecha | `harvest` | Entrada de bitácora con el rendimiento en kg. Es la fuente del indicador de productividad. |

## Agua y clima

| Término | Código | Definición |
|---|---|---|
| ET0 | `et0_mm` | Evapotranspiración de referencia (FAO Penman-Monteith), en mm/día. |
| Kc | `kc` | Coeficiente de cultivo según la etapa fenológica. |
| ETc | `etc_mm` | Evapotranspiración del cultivo: `ETc = Kc × ET0`. |
| Balance hídrico | `water_balance` | Contabilidad diaria del agua disponible en la zona de raíces: lluvia efectiva + riego − ETc. |
| Agotamiento | `depletion_mm` | Agua que falta para llegar a capacidad de campo. |
| Lámina de riego | `irrigation_depth_mm` | Agua que se recomienda aplicar, en mm (1 mm = 10 m³/ha). |
| Celda climática | `weather_cell` | Cuadrícula de 0,1° (~11 km) que comparten las parcelas cercanas para no repetir llamadas al proveedor de clima. |

## IoT

| Término | Código | Definición |
|---|---|---|
| Nodo | `node` | Dispositivo físico (ESP32 o similar) con uno o más sensores y un transporte (Wi-Fi, celular o LoRaWAN). |
| Sensor | `sensor` | Canal de medición de un nodo: una variable, a una profundidad, con su calibración. |
| Variable | `metric` | Magnitud medida: `soil_moisture`, `soil_temp`, `air_temp`, `air_rh`, `rain`, `battery_v`, `rssi`. |
| Lectura | `reading` | Valor de un sensor en un instante. Guarda el valor crudo (`raw_value`) y el calibrado (`value`). |
| Calibración | `calibration` | Función que convierte el valor crudo en unidades físicas (por ejemplo, ADC → % volumétrico). Es propia de cada sensor y tiene versiones. |
| Uplink / downlink | `up` / `down` | Mensaje del nodo al servidor / del servidor al nodo. |

## Decisión y riesgo

| Término | Código | Definición |
|---|---|---|
| Regla de alerta | `alert_rule` | Condición sobre variables (umbral, duración, histéresis) que abre una alerta. |
| Alerta | `alert` | Instancia de una regla que se cumplió en una parcela o un nodo. Estados: `open`, `acknowledged`, `resolved`. |
| Recomendación | `recommendation` | Acción sugerida con su justificación: regar X mm, aplicar un preventivo, revisar un nodo. |
| Riesgo climático | `risk_prediction` | Probabilidad calibrada de un evento (inundación o sequía) en un horizonte, con severidad. |
| Versión de modelo | `model_version` | Artefacto entrenado con sus métricas, su línea base y su estado de promoción. |
| Línea base | `baseline` | Método simple (una heurística) que un modelo debe superar para promoverse. |
| Índice de tecnificación | `technification_index` | Indicador compuesto de 0 a 100 por parcela ([11-metricas](11-metricas.md)). |
