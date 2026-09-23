# 00 — Glosario (lenguaje ubicuo)

El mismo término significa lo mismo en conversaciones, documentos, la interfaz y el código. La columna **Código** es el nombre obligatorio en el código, las tablas y la API.

## Dominio agrícola

| Término | Código | Definición |
|---|---|---|
| Organización | `organization` | Cooperativa, asociación, programa o productor individual. Es la unidad de permisos: todo dato pertenece a una organización. |
| Finca | `farm` | Predio de un productor. Tiene ubicación y municipio. |
| Parcela / lote | `plot` | Área cultivable con polígono dentro de una finca. Es la unidad de decisión: riego, alertas y métricas se calculan por parcela. |
| Sistema de riego | `irrigation_system` | Cómo se riega la parcela: `drip` (goteo), `sprinkler` (aspersión), `gravity` (gravedad) o `none`. Con un sistema, la parcela tiene su eficiencia (`irrigation_efficiency`) y su caudal. |
| Parcela de secano | `irrigation_system = none` | Parcela sin sistema de riego: el cultivo depende solo de la lluvia. Tiene balance hídrico, pero recibe una recomendación de secano en vez de una lámina. |
| Cultivo | `crop` | Especie del catálogo (maíz, yuca, plátano…) con sus coeficientes Kc y su fracción de agotamiento `p` por etapa. No tiene umbral de humedad propio: el estrés hídrico se define por parcela. |
| Origen del Kc | `kc_source` | De dónde salen los Kc de un cultivo: `fao56` (Tabla 12), `local` (validado en la región), `approximate` (tomado de un cultivo parecido, por ejemplo banano para plátano) o `none` (sin Kc validado, como el ñame). Con `none` no se recomienda lámina de riego. |
| Ciclo de cultivo | `crop_cycle` | Una siembra concreta de un cultivo en una parcela, desde la siembra hasta la cosecha. Una parcela tiene como máximo un ciclo activo. |
| Etapa fenológica | `growth_stage` | Inicial, desarrollo, media o final (FAO-56). Determina el Kc. |
| Bitácora | `logbook` | Registro de lo que hizo el productor: labores, insumos, riegos, costos, observaciones y cosechas. |
| Entrada de bitácora | `logbook_entry` | Un registro individual de la bitácora. |
| Cosecha | `harvest` | Entrada de bitácora con el rendimiento en kg y, si se vendió, los kg vendidos (`sold_kg`) y el precio (`sale_price_cop_per_kg`). Es la fuente del rendimiento y del margen. |
| Jornales | `labor_days` | Días de trabajo de una labor, incluida la mano de obra familiar. |
| Encuesta de inscripción | `plot_baseline` | Cómo producía la parcela antes de usar TechCamp: cultivo y rendimiento del último ciclo, costos aproximados y práctica de riego. Se registra al inscribir la parcela y es la referencia para medir el impacto. |
| Rendimiento relativo municipal | `relative_yield` | Rendimiento del ciclo dividido por la mediana EVA del cultivo en el municipio (3 años). Da contexto; no mide impacto. |

## Agua y clima

| Término | Código | Definición |
|---|---|---|
| ET0 | `et0_mm` | Evapotranspiración de referencia (FAO Penman-Monteith), en mm/día. |
| Kc | `kc` | Coeficiente de cultivo según la etapa fenológica. |
| ETc | `etc_mm` | Evapotranspiración del cultivo: `ETc = Kc × ET0`. |
| Balance hídrico | `water_balance` | Contabilidad diaria del agua disponible en la zona de raíces: lluvia efectiva + riego − ETc. |
| Agotamiento | `depletion_mm` | Agua que falta en la zona de raíces para llegar a capacidad de campo (Dr). |
| Agua disponible total | `taw_mm` | Agua que el suelo retiene entre capacidad de campo y punto de marchitez en la zona de raíces: `TAW = 1000 × (θFC − θWP) × Zr`. |
| Agua fácilmente aprovechable | `raw_mm` | Parte de TAW que el cultivo extrae sin estrés: `RAW = p × TAW`, con `p` de la etapa ajustado por ETc (FAO-56). |
| Estrés hídrico | `water_stress` | El cultivo está en estrés cuando `Dr > RAW` (Ks < 1). Se evalúa por parcela, con el suelo de la parcela y la etapa del cultivo; en humedad equivale a `θ < θ_estrés = θFC − p × (θFC − θWP)`. También es el código de la regla de alerta. |
| Asimilación del sensor | `assimilation` | Corrección del agotamiento modelado con el observado por el sensor: `Dr = Dr_modelo + K × (Dr_obs − Dr_modelo)`. `K` depende del tipo de calibración y de que la profundidad del sensor sea representativa. |
| Lámina de riego | `irrigation_depth_mm` | Agua que se recomienda aplicar, en mm (1 mm = 10 m³/ha). |
| Celda climática | `weather_cell` | Cuadrícula de 0,1° (~11 km) que comparten las parcelas cercanas para no repetir llamadas al proveedor de clima. |

## IoT

| Término | Código | Definición |
|---|---|---|
| Nodo | `node` | Dispositivo físico (ESP32 o similar) con uno o más sensores y un transporte (Wi-Fi, celular o LoRaWAN). |
| Sensor | `sensor` | Canal de medición de un nodo: una variable, a una profundidad, con su calibración. |
| Variable | `metric` | Magnitud medida: `soil_moisture`, `soil_temp`, `air_temp`, `air_rh`, `rain`, `battery_v`, `rssi`. |
| Lectura | `reading` | Valor de un sensor en un instante. Guarda el valor crudo (`raw_value`) y el calibrado (`value`). |
| Calibración | `calibration` | Función que convierte el valor crudo en unidades físicas (por ejemplo, ADC → % volumétrico). Es propia de cada sensor, tiene versiones y un tipo: `lab` (laboratorio por tipo de suelo) o `field` (en la parcela). |
| Uplink / downlink | `up` / `down` | Mensaje del nodo al servidor / del servidor al nodo. |

## Decisión y riesgo

| Término | Código | Definición |
|---|---|---|
| Regla de alerta | `alert_rule` | Condición sobre variables (umbral, duración, histéresis) que abre una alerta. |
| Alerta | `alert` | Instancia de una regla que se cumplió en una parcela o un nodo. Estados: `open`, `acknowledged`, `resolved`. |
| Recomendación | `recommendation` | Acción sugerida con su justificación: regar X mm, aplicar un preventivo, revisar un nodo. |
| Recomendación de secano | `rainfed` | Tipo (`kind`) de la recomendación hídrica diaria de una parcela de secano: déficit frente a RAW, lluvia pronosticada y consejos de manejo (`advice`), sin lámina ni minutos. |
| Riesgo climático | `risk_prediction` | Probabilidad calibrada de un evento (inundación o sequía) en un horizonte, con severidad. |
| Versión de modelo | `model_version` | Artefacto entrenado con sus métricas, su línea base y su estado de promoción. |
| Línea base | `baseline` | Método simple (una heurística) que un modelo de ML debe superar para promoverse. Solo se usa en ML; los datos de la parcela antes de TechCamp son la encuesta de inscripción (`plot_baseline`). |
| Índice de adopción digital | `digital_adoption_index` | Indicador compuesto de 0 a 100 por parcela que mide el uso de la plataforma: monitoreo, registro, decisión y acción ante alertas ([11-metricas](11-metricas.md)). |
