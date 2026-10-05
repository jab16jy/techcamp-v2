# Model Card: M2 — Riesgo de Inundación

Ficha del modelo M2 para predicción mensual de riesgo de inundación municipal en el Caribe colombiano, siguiendo el paso 1 del protocolo de experimentación de ML ([ADR-0020](../../../docs/adr/0020-protocolo-de-experimentacion-ml.md)) y las especificaciones de [docs/08-ml.md](../../../docs/08-ml.md).

## 1. Decisión que mejora
¿Me preparo para inundación este mes? (docs/08 §Inventario de modelos, §M2).
Permite al productor y a las organizaciones de productores tomar decisiones preventivas (proteger parcelas, habilitar drenajes, planificar cosecha o mover maquinaria e insumos) frente a la línea base simple de lluvia acumulada.

## 2. Etiqueta / objetivo
1 si hay un evento de inundación reportado en el municipio y el mes en las fuentes oficiales (UNGRD / DesInventar: eventos `INUNDACION`, `CRECIENTE SUBITA` y `AVENIDA TORRENCIAL`). 0 en cualquier otro caso (docs/08 §M2, §Fuentes de datos de M2).

## 3. Unidad
Municipio × mes (docs/08 §M2).
Las features climáticas y topográficas se agregan a nivel de municipio desde el centroide municipal del DANE MGN 2024.

## 4. Región
Los 7 departamentos del Caribe continental colombiano: Atlántico (08), Bolívar (13), Cesar (20), Córdoba (23), La Guajira (44), Magdalena (47) y Sucre (70), que abarcan 195 municipios según el MGN 2024 del DANE (docs/08 §M2 "Región"). El archipiélago de San Andrés, Providencia y Santa Catalina queda excluido por ser insular.

## 5. Horizonte
La predicción para el mes M se emite con datos climáticos e históricos hasta el último día del mes M−1; la etiqueta evalúa la ocurrencia de eventos en el mes M (docs/08 §M2 "Horizonte", docs/06 §8). `horizon_start` corresponde al primer día de M y `horizon_days` a los días del mes M.

## 6. Frecuencia de clase
Medida sobre el dataset real de T4 (`ml/data/flood_m2/dataset/flood_m2.parquet`) y verificada de nuevo en la corrida de T8:

| Bloque | Meses | Filas | Positivos | Prevalencia |
| --- | --- | --- | --- | --- |
| Train | 2019-01 → 2022-06 | 8 190 | 377 | 4,60 % |
| Validación | 2023-01 → 2024-06 | 3 510 | 184 | 5,24 % |
| Test (bloqueado) | 2025-01 → 2025-12 | 2 340 | 63 | 2,69 % |
| Total | 2019-01 → 2025-12 | 16 380 | 838 | 5,12 % |

Cada bloque son los 195 municipios de docs/08 §M2 "Región" por cada mes del rango, y los seis meses de brecha de cada par (1 170 filas = 195 × 6) se descartan en lugar de reasignarse a un lado (docs/08 §M2 "Partición").

Todos los municipio-mes sin evento registrado son negativos; no hay submuestreo en validación ni en test (docs/08 §M2 "Negativos", §Reglas de gobierno "Frecuencia real"). El peso de clases solo existe dentro de train, y únicamente en la regresión logística de la escalera.

Las tres columnas `precip_anomaly_1m/3m/6m` llegan nulas en el dataset publicado porque la climatología es del split, y T8 las deriva contra la climatología de train (2019–2022, `split.train_climatology_years()`) en una tabla derivada fuera de git; el manifiesto del dataset y su sha256 no se tocaron. Sobre el dataset real no quedó ninguna anomalía nula.

Los fixtures del harness usan 1 evento en 84 meses (1,19 %): prueban la forma y nunca un umbral.

## 7. Costo de falsos positivos y falsos negativos
- **Falso positivo (costo cualitativo):** Pérdida de recursos y tiempo al ejecutar medidas preventivas innecesarias, fatiga de alertas y pérdida de confianza en la plataforma. Para mitigar este costo, el uso operativo define la severidad `alto` con un umbral estricto que garantice una precisión mínima ≥ 0,7 en el conjunto de validación (docs/08 §M2 "Uso operativo", §Severidad).
- **Falso negativo (costo cualitativo):** Daño o pérdida total de cultivos, pérdida de insumos o afectación de infraestructura sin preparación previa. Para balancear este costo, el recall operativo obtenido con el umbral de precisión ≥ 0,7 se publica abiertamente, y si ningún modelo supera la compuerta se sirve la mejor línea base explicable (docs/08 §M2, docs/08 §Reglas de gobierno).

## 8. Métrica principal
- **PR-AUC con la frecuencia real:** Área bajo la curva Precision-Recall calculada sobre la prevalencia natural de la clase (docs/08 §M2, docs/11-metricas.md §3).
- **Brier score:** Error cuadrático medio de la probabilidad calibrada respecto al desenlace real (docs/08 §M2, docs/11-metricas.md §3).

## 9. Mejora mínima útil (compuerta de promoción)
Para que un modelo candidato reemplace a la línea base servida en producción, debe superar la compuerta estadística en el conjunto de test bloqueado (ADR-0020 paso 8, docs/08 §Reglas de gobierno):
- Superar a la mejor línea base (climatología o heurística) en PR-AUC con límite inferior del intervalo de confianza del 95 % (IC95 por bootstrap pareado) estrictamente mayor que cero (`CI95_low > 0`).
- El Brier score no puede empeorar respecto al de la mejor línea base.
- Si ningún modelo supera la compuerta, se sirve en producción la mejor línea base de validación registrada (docs/08 §M2 "Línea base servida"). No existe `force_promote`.

### Escalera y cortes operativos (validación, corrida del 2026-10-05)

| Rung | Tipo | PR-AUC (IC95 bootstrap) | Brier |
| --- | --- | --- | --- |
| lightgbm | modelo | 0,107727 [0,085272, 0,138898] | 0,048774 |
| climatology_month | línea base | 0,098390 [0,079492, 0,122723] | 0,048885 |
| logistic_regression | modelo | 0,079444 [0,064833, 0,101407] | 0,204120 |
| rainfall_6m | línea base | 0,052085 [0,044023, 0,060815] | 0,049724 |

Candidato único a la compuerta: **lightgbm**, por mayor PR-AUC en validación (el Brier desempata). Línea base de comparación: **climatology_month**, la mejor de la escalera de líneas base. Búsqueda cuasialeatoria de 12 sorteos por modelo con semilla 20261005 y early stopping sobre validación (ADR-0020 paso 6); el registro completo está en `ml/experiments/log.csv`.

Cortes operativos (ADR-0020 paso 7, sobre validación y con el candidato ya calibrado por Platt):

- `alto`: umbral 0,356862, precisión 1,000000, **recall 0,005435** (1 de los 184 eventos de validación).
- `crítico`: existe, con el mismo umbral 0,356862, precisión 1,000000 y recall 0,005435. La curva alcanza 0,85 de precisión en el mismo corte donde alcanza 0,70, así que los dos códigos parten del mismo umbral y `crítico` no agrega separación.

El recall de `alto` se publica porque es el número que decide si una alerta es accionable (docs/08 §M2 "Uso operativo"): sobre este dataset, el corte que alcanza precisión 0,7 alcanza **un solo evento de validación**. Es el resultado honesto de la regla de docs/08 sobre una curva que apenas dobla la prevalencia, y es el argumento para revisar la regla —o la frecuencia— con el dueño antes de un piloto.

### Compuerta en el test bloqueado (una lectura, ADR-0020 paso 8)

| | PR-AUC | Brier |
| --- | --- | --- |
| Candidato `lightgbm` | 0,054305 | 0,027650 |
| Línea base `climatology_month` | 0,049315 | 0,026581 |

Bloque de 2 340 filas con 63 positivos (2,69 %). Mejora pareada: **+0,004990**, IC95 **[−0,012013, +0,040028]**.

**`promote = false`**, por las dos reglas de la compuerta: `improvement_ic95_lower_bound_not_above_zero` y `brier_worse_than_the_best_baseline`. Se sirve **`climatology_month`** (docs/08 §M2 "Línea base servida", D-T0.5), registrada como `model_version` con `is_baseline`.

No es un fallo de la corrida: con 63 positivos en 2 340 meses, el IC95 pareado de una mejora de 0,005 no puede separarse de cero, y la línea base además gana en Brier. Que la validación sugiriera una mejora y el test no la confirme es exactamente lo que la brecha de seis meses y el bloqueo del test existen para detectar.

### Registro y servicio (ADR-0020 paso 10)

La versión registrada es `risk_flood@2026-10-05-climatology_month`, con `is_baseline=true` y `promoted=false`: la compuerta no promovió, y lo que se registra es lo que ella dejó sirviendo (`GateRun.served`).

| Campo | Valor |
| --- | --- |
| `artifact_uri` | `s3://ml-artifacts/models/risk_flood/2026-10-05-climatology_month/climatology.json` |
| `artifact_sha256` | `a5cf94c4f89b3f77fdeb90efc3765c978a65c773ac2cf9789d4da45e92a427fb` (596 bytes) |
| `dataset_hash` | `7916e97fb4ea7bdc50b4d4cb1bb244683c5dcbf1bf32421ea8c32788983d0be5` (el manifiesto de T4) |
| `git_commit` | `89ee0a6` |
| `baseline_metrics` | PR-AUC 0,098390 [0,079492, 0,122723] · Brier 0,048885 |
| `thresholds` | `{}` |

La línea base se fitteó sobre las 8 190 filas de train (377 positivos, prevalencia 4,60 %), que es donde se vive la climatología por mes que sirve; la validación y el test nunca entraron al fit.

**`thresholds` va vacío a propósito, y toda predicción de esta versión queda en `low`.** Los cortes de arriba (`alto=0,356862`) son del LightGBM calibrado, no de una climatología que nunca se calibró. docs/08 §M2 "Severidad" dice que los umbrales viajan con la versión del modelo: una versión sin cortes calibrados no tiene cortes, y `severity_for` lee un umbral ausente como evidencia faltante, con el techo en la severidad inferior. Poner un umbral aquí sería inventar un número que ninguna validación produjo, y con la prevalencia real de la región (octubre 8,5 %, enero 0,0 %) ningún corte promisorio cambiaría el hecho de que la señal de esta línea base es la estacionalidad.

El artefacto viaja en el bucket `ml-artifacts`, separado del de fotos (ADR-0018; docs/08 §Estructura de `ml/`). El worker lo baja por el endpoint **interno** de la red y verifica su `sha256` contra el de la fila antes de deserializarlo (docs/03 §Integridad del artefacto: un objeto cambiado en el bucket no se ejecuta).

### Robustez (ADR-0020 paso 9; train y validación, nunca test)

Reporte adicional dejando fuera un departamento a la vez (docs/08 §M2 "Partición"), con la configuración elegida reentrenada en el train de cada pliegue (PR-AUC):

| Departamento | PR-AUC | Departamento | PR-AUC |
| --- | --- | --- | --- |
| ATLÁNTICO | 0,0948 | LA GUAJIRA | 0,1091 |
| BOLÍVAR | 0,1180 | MAGDALENA | 0,1137 |
| CESAR | 0,0828 | SUCRE | 0,1208 |
| CÓRDOBA | 0,0724 | prevalencia de validación | 0,0524 |

Por temporada calendario: Q1 0,0419 · Q2 0,1161 · Q3 0,0208 · Q4 0,2231.

El candidato es fuerte en el cuarto trimestre y casi inútil en el primero y el tercero, que es la misma forma que muestra la climatología de train: los eventos reportados del Caribe se concentran entre abril y noviembre, con el pico en octubre y noviembre. CESAR y CÓRDOBA son los departamentos más débiles, y CÓRDOBA queda apenas por encima de la prevalencia de su bloque.

## 10. Limitaciones
- **Inundación fluvial y ruptura de diques:** M2 se alimenta exclusivamente de precipitación local acumulada, humedad del suelo y topografía municipal. No modela dinámica hidrológica fluvial ni rupturas de diques o jarillones, que constituyen el principal mecanismo de inundación en zonas bajas de la región (por ejemplo, La Mojana, cuencas de los ríos Cauca, Magdalena, Sinú y San Jorge, como el desastre de Cara de Gato en 2021). En un piloto, las alertas hidrológicas del IDEAM por nivel de río deben operar primero como regla de línea base complementaria (docs/08 §M2 "Limitación", brecha G07).
- **Sesgo de reporte en etiquetas:** Las etiquetas provienen de los consolidados de emergencias de la UNGRD y DesInventar, los cuales exhiben sesgo de notificación: municipios con mayor densidad poblacional, mayor accesibilidad o capacidad institucional tienden a reportar emergencias más consistentemente que municipios rurales dispersos o remotos (docs/08 §M2 "Etiqueta").
