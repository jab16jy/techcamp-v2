# Data card — M2 riesgo de inundación: fuentes y dataset

**Actualizado:** 2026-10-05 · **Tarea:** E10 T3 (fuentes) + E10 T4 (tabla municipio × mes)
(odd/tasks/techcamp-v2-e10-climate-risk.md) ·
**Docs:** [docs/08](../../docs/08-ml.md) §M2 riesgo de inundación, §Fuentes de datos de M2, §Estructura de `ml/`, §Reglas de gobierno

Este documento describe lo que T3 descarga y cómo lo normaliza, y lo que T4 arma sobre
esas cuatro tablas. T5 el harness. Nada aquí decide el modelo.

## Cómo se reproduce

```bash
# Red: sólo los cuatro hosts de abajo. Reanuda: un trozo ya cacheado no se vuelve a pedir.
uv run --locked --project ml python -m techcamp_ml.sources fetch
# Sin red: los parquets se reconstruyen desde ml/.cache/raw. El rango del clima lo decide
# el plan que escribió el fetch, no el día en que corre el parse.
uv run --locked --project ml python -m techcamp_ml.sources parse
# Sin red: la tabla municipio × mes sale de esos cuatro parquets, con su sha256 en el manifiesto.
uv run --locked --project ml python ml/datasets/flood_m2/build.py
```

El `build` **se niega** si falta cualquiera de los cuatro parquets de
`ml/data/flood_m2/sources/`, diciendo cuál y que hay que correr el `parse`: los lee todos
antes de escribir nada, así que un caché incompleto no deja ni dataset ni manifiesto.
También se niega si un parquet no se puede rastrear hasta el plan del caché actual: el
`parse` escribe junto a cada parquet gobernado por un plan (`weather.plan.json`,
`labels.plan.json`) el `sha256` del plan que consumió, y el `build` compara ese digest con
el del plan que está en `ml/.cache/raw/`. Sin eso, un `fetch` que volvió a correr —o que
se cortó a medias— dejaba el parquet viejo legible y plausible, y el dataset se armaba
desde copias que el caché actual ya no respondería (docs/08:68, "el dataset se arma solo
desde esas copias"). Se niega además si el clima trae **dos filas para el mismo
municipio y el mismo día**: un diccionario se quedaría con la última, así que la ventana
dependería del orden de filas del parquet y no de una regla.

Los dos archivos se escriben junto a su destino y se renombran, y **el manifiesto se
publica antes que el dataset**: en ningún momento existe un parquet sin el manifiesto que
lo describe, y ninguno de los dos aparece a medio escribir. El `build` sin `--out` ni
`--manifest` publica en los directorios del `layout` que recibió, nunca en el árbol real
del repositorio. El manifiesto no lleva fecha: el build es función pura de los cuatro
parquets —cada uno leído una vez y hasheado de esa misma lectura—, así que el mismo caché
da el mismo archivo y el mismo `sha256` cualquier día.

Cada respuesta cruda queda en `ml/.cache/raw/<fuente>/` (fuera de git) con su URL, su
fecha de descarga y su `sha256` en `ml/.cache/raw/MANIFEST.tsv`. Los parquets de
`ml/data/flood_m2/sources/` salen **sólo** de esas copias: "reproducible o no existe"
(docs/08 §Reglas de gobierno). El manifiesto se escribe entero a un `.part` y se renombra
—igual que cada copia—, así que una interrupción no se lleva por delante la procedencia de
los demás archivos. Y un reanudar se guía por el manifiesto, no por el directorio: una
copia sin fila es una descarga de la que nadie sabe la URL ni el `sha256`, y se vuelve a
pedir.

## Fuentes

| Dato | Fuente | Licencia | Versión / descarga | Hash (primeros 16) |
|---|---|---|---|---|
| Municipios (punto) | DIVIPOLA, códigos municipios, `https://www.datos.gov.co/resource/gdxc-w37w.json` | CC BY-SA 4.0 | 2026-10-02T17:45:26Z | `ecf12e564067cb29` |
| Municipios (control) | DANE MGN 2024, capa Municipio 317, `https://portalgis.dane.gov.co/mparcgis/rest/services/MGN2024/Serv_CapasMGN_2024/MapServer/317/query` | CC BY-SA 4.0 (DANE) | 2026-10-02T17:45:27Z | `545d85378ba1784b` |
| Etiquetas 2019–2022 | UNGRD, `https://www.datos.gov.co/resource/wwkg-r6te.json` | CC BY-SA 4.0 | 2026-10-02T17:27:25Z | `171266650fe22614` |
| Etiquetas 2023–2024 | UNGRD, `https://www.datos.gov.co/resource/rgre-6ak4.json` | CC BY-SA 4.0 | 2026-10-02T17:27:28Z | `7769c6d43fc754f8` |
| Etiquetas 2025+ | UNGRD, `https://www.datos.gov.co/resource/2343-nuqp.json` | CC BY 4.0 | 2026-10-02T17:27:32Z | `7e54b5371993ebc3` |
| Clima diario | Open-Meteo archive, `https://archive-api.open-meteo.com/v1/archive`, `models=era5` | Open-Meteo, uso no comercial ([ADR-0021](../../docs/adr/0021-perfil-seminario-local.md)) | T3, 2026-10-02T18:02Z (trozos 000–002) y 2026-10-03T16:23Z (trozos 003–005), **6 de 6 trozos** | `archive_000` `956284edc5cbf9f7`, `archive_001` `255c33527febdf48`, `archive_002` `db28970e8074ec09`, `archive_003` `4dcdb69e8a9f32e4`, `archive_004` `5fb9a72d4ec76fb5`, `archive_005` `db9d8cf60087b059` |
| Elevación y vecino | Open-Meteo elevation (Copernicus GLO-90), `https://api.open-meteo.com/v1/elevation` | Open-Meteo, uso no comercial | 2026-10-02T17:28Z, 10 trozos (975 coordenadas) | trozo 000 de 10: `d52dc71cce74be05` (los diez en `MANIFEST.tsv`) |

Open-Meteo gratuito es de uso no comercial: cubre el seminario; producción necesita su
plan de pago (docs/08 §Fuentes de datos de M2).

## Región y rango

- **Región:** los 7 departamentos del Caribe continental (08, 13, 20, 23, 44, 47, 70),
  **195 municipios** (docs/08 §M2 "Región"). San Andrés queda fuera.
- **Municipios:** `code` DIVIPOLA de 5 dígitos, `name`, `department_code`,
  `department_name`, `lat`, `lon`. El control MGN 2024 tiene que dar exactamente el
  mismo conjunto de `mpio_cdpmp`; si no, la construcción falla.
- **Clima:** `precipitation_sum` y `soil_moisture_0_to_7cm_mean` diarios, desde
  **2018-06-30** (un día antes del inicio) hasta el último mes completo que ERA5 ya
  publicó del todo: con su retraso de ~5 días, el 2026-10-02 ese mes es **2026-08**.
  La serie empieza en 2018-07-01 para que las ventanas de seis meses de docs/08 §M2
  "Features" estén completas antes del primer mes etiquetado, 2019-01.

  **Estado al cerrar la descarga (2026-10-05): los 6 de 6 trozos en el caché**, con plan
  `plan_for=2026-10-05`, que declara las mismas tres ventanas que el 2026-10-02 porque el
  último mes completo no se movió. Cobertura para los 195 municipios, de punta a punta:

  | Cobertura | Municipio | Rango | Trozos |
  |---|---|---|---|
  | Completa (195) | los 195 municipios | 2018-06-30 → 2026-08-31 | `archive_000`…`archive_005` |

  El parquet de `ml/data/flood_m2/sources/weather.parquet`, rearmado el 2026-10-03 después
  del último trozo, tiene **582 075 filas**: 195 municipios × 2 987 días menos los dos
  días de frontera que `concat_windows` cuenta una sola vez. Ningún municipio queda con
  una serie truncada.

  La cuota fue el obstáculo de la primera pasada, no del código: el nivel gratuito de
  Open-Meteo pesa una consulta por variables, ubicaciones y dominios (10 000/día por IP,
  con cubos por minuto y por hora), y el archivo histórico pesa además por la longitud del
  rango pedido.
  `python -m techcamp_ml.sources fetch --source weather` reanuda donde se quedó y no
  vuelve a pedir lo que ya está en el caché: con los 6 trozos y su plan, corre en menos
  de un segundo y no toca la red. Un trozo cuyo `.request.json` declara otra ventana
  **se vuelve a pedir**: los mismos nombres de archivo respondían la ventana anterior en
  cuanto los meses avanzan, y una serie que termina antes de lo que su nombre promete no
  puede pasar como completa.

  **El rango lo decide el plan, no el reloj.** El `fetch` escribe `archive_plan.json` en
  `ml/.cache/raw/weather/` —con su fila en el manifiesto— **antes** del primer trozo: la
  lista completa de trozos que planeó para ese día, cada uno con su ventana y sus códigos.
  El `parse` no recibe ningún día: lee ese plan y se niega a armar el parquet si falta
  algún trozo planeado, o si alguno declara otra ventana u otros códigos, diciendo cuál.
  Mismo caché, mismo parquet, cualquier día en que corra (docs/08 §Reglas de gobierno: "el
  dataset se arma sólo desde esas copias"). Este caché **sí tiene plan**: `archive_plan.json`
  con sus 6 trozos, escrito antes del primer request. `parse --source weather` lo lee y se
  niega si algún trozo planeado falta o declara otra ventana u otros códigos.

- **Etiquetas:** ventana de consulta por dataset: 2019-01-01→2022-12-31,
  2023-01-01→2024-12-31 y 2025-01-01→(sin tope), en ese orden y sin mezclar años entre
  fuentes. La ventana es **semiabierta** (`>= inicio and < fin`): el 1 de enero del
  dataset siguiente es del siguiente, no de éste. Rango de los **datos** de la región:
  **2019-02-26 → 2025-12-02**.

  **La paginación se publica antes de paginar** (`labels_plan.json` en
  `ml/.cache/raw/labels/`, con su fila en el manifiesto), igual que el plan del archivo
  histórico: el `fetch` lo escribe abierto antes de la primera página y lo reescribe con
  todas las páginas que caminó cuando llega a una página corta. El `parse` **se niega**
  si el plan no está, si sigue abierto o si el caché no responde alguna página prometida,
  diciendo cuál. Socrata no publica un total de filas, así que sólo el `fetch` puede saber
  que una página fue la última: un `fetch` cortado dejaba páginas que nadie sabía si eran
  las últimas, y el `parse` armaba un parquet de etiquetas que se leía como completo
  (#241).

## Conteos

Etiquetas de inundación en la región: **1 508 reportes**, 180 de los 195 municipios con
al menos uno.

| Año | INUNDACION | CRECIENTE SUBITA | AVENIDA TORRENCIAL | Total |
|---|---|---|---|---|
| 2019 | 78 | 0 | 2 | 80 |
| 2020 | 140 | 26 | 0 | 166 |
| 2021 | 144 | 6 | 1 | 151 |
| 2022 | 603 | 54 | 6 | 663 |
| 2023 | 130 | 8 | 2 | 140 |
| 2024 | 144 | 14 | 2 | 160 |
| 2025 | 136 | 10 | 2 | 148 |

| Departamento | Reportes | Municipios con evento |
|---|---|---|
| 08 Atlántico | 166 | 22 / 23 |
| 13 Bolívar | 355 | 43 / 46 |
| 20 Cesar | 215 | 24 / 25 |
| 23 Córdoba | 178 | 27 / 30 |
| 44 La Guajira | 207 | 15 / 15 |
| 47 Magdalena | 198 | 27 / 30 |
| 70 Sucre | 189 | 22 / 26 |

**Filas descartadas** (`ml/data/flood_m2/sources/labels.drops.json`, regenerado en cada
`parse`): 8 477 códigos fuera de la región (el dataset es nacional), 0 eventos de otra
clase, 0 fechas ilegibles, 0 filas fuera de la ventana de su dataset. La descarga ya
filtra por evento; el parser vuelve a clasificar y cuenta, para que una fila perdida por
un valor raro sea visible. La cuenta no cambió al pasar la ventana a semiabierta y al
dejar de tapar `2343-nuqp` en 2027: 1 508 reportes antes y después.

**Otros conteos:** elevación 195 filas (2 a 1 307 m, media 90 m); los cuatro vecinos a
1 km se piden en la misma llamada y salen en las columnas `east_m`, `west_m`,
`north_m`, `south_m`, que son las que consume `techcamp.risk.domain.features.Neighbours`.
`parse --source elevation` se niega a armar la tabla si le falta **algún** municipio y
nombra cuántos son: una tabla corta dejaría las features de vecino de T4 sin definir para
los que faltan, sin decir nada.

## La tabla municipio × mes (T4)

Una fila por municipio y por mes M de la cobertura de etiquetas, ordenada por `code`,
`year`, `month`. Las features las calcula `techcamp.risk.domain.features`, el mismo módulo
que usa el serving: no hay una segunda implementación de ninguna ventana, ni de la
pendiente, ni de la estacionalidad (docs/08 §Reglas de gobierno, "Paridad de features").

### Cobertura de meses

La ventana es **2019-01 → 2025-12**, la que docs/08 §Fuentes de datos de M2 y D-T3.2
cierran para M2 (los tres consolidados UNGRD, sin DesInventar ni nada anterior a 2019).
Está **escrita, no medida**: `2343-nuqp` se declara abierta (`year_to=None`) para que la
consulta nunca esté topada, y el reporte más nuevo del parquet tampoco es una ventana —
un dataset cuyo horizonte se moviera con sus descargas crecería y menguaría con ellas. Los
meses de esa ventana son los que la descarga reclama, así que un mes dentro sin reporte es
un negativo; los de fuera **no son filas**, tengan clima o no.

El retraso de reporte sigue siendo un sesgo (sesgo 3 arriba), no un tope: un municipio que
reporta tarde aparece con menos eventos de los que tuvo, y los meses de 2026 seguirán sin
etiqueta hasta que `2343-nuqp` los publique.

### Contrato de columnas

| Columna | Qué es |
|---|---|
| `code` | DIVIPOLA de 5 dígitos del municipio |
| `department_code`, `department_name` | Departamento, de la cabecera DIVIPOLA |
| `year`, `month` | El mes M que se predice |
| `horizon_start`, `horizon_days` | Día 1 de M y sus días (docs/08 §M2 "Horizonte") |
| `precip_sum_1m` … `precip_sum_6m` | Lluvia acumulada de 1 a 6 meses hasta el último día de M−1 |
| `precip_anomaly_1m`, `_3m`, `_6m` | **Nulo en esta tabla**: la anomalía se mide contra la climatología de **train** y el split es de T5 |
| `soil_moisture_mean_1m` | Humedad de suelo media de M−1 |
| `elevation_m`, `slope_deg` | Del parquet de elevación, en la cabecera |
| `month_sin`, `month_cos` | Estacionalidad, enero en el origen de fase |
| `label` | 1 si hay evento de inundación reportado en ese municipio y mes, si no 0 |

Un día que falta deja su ventana en **nulo**, nunca en 0: 0 mm es una afirmación sobre el
clima que el dato no hace. Un municipio al que el archivo no respondió conserva su fila con
features nulas — el mes se conoce, lo que falta es la evidencia — y quitarlo submuestrearía
la región según cómo haya ido la descarga. Los reportes repetidos colapsan en la etiqueta
binaria del mes (sesgo 4 arriba), y `label` es `int`, no `bool`.

La región se verifica **en el build también**, no sólo en el `parse`: sin la guardia de
`assert_region` un dataset podría armarse sobre otra región y M2 entrenaría sobre
municipios que docs/08 §M2 "Región" no nombra.

### Conteos del dataset real

El build corrió sobre el **archivo real**, el 2026-10-05: los seis trozos de ERA5, los tres
consolidados UNGRD y la elevación de los 195 municipios. El `manifest.json` junto a este
documento es el recibo, con el `sha256` del parquet y el de cada uno de los cuatro
parquets de entrada.

| Medida | Dataset real |
|---|---|
| Filas | **16 380** (195 municipios × 84 meses, `2019-01` → `2025-12`) |
| Municipios | 195, **los 195 con los 84 meses completos** (ninguno truncado) |
| Positivos | **838** municipio-mes con evento (1 508 reportes colapsados) |
| Negativos | **15 542**, sin submuestreo en ningún punto |
| Prevalencia real | **5,12 %** (838 / 16 380) |
| Nulos | **0** fuera de `precip_anomaly_{1,3,6}m`, que van nulas por diseño |
| Parquet | 455 260 bytes, `sha256 7916e97fb4ea7bdc50b4d4cb1bb244683c5dcbf1bf32421ea8c32788983d0be5` |

Que no haya **ningún** nulo fuera de las tres anomalías es la prueba de que no es un dataset
parcial: la serie diaria cubre 2018-06-30 → 2026-08-31 para los 195 municipios, así que
toda ventana de uno a seis meses de cualquier mes etiquetado tiene su evidencia completa.
Un municipio al que el archivo no hubiera respondido habría conserved su fila con
features nulas, y no hay ninguno.

**La prevalencia es 5,12 %, no 9,2 %.** La etiqueta de docs/08 §M2 "Etiqueta" es binaria por
municipio y mes, así que los 1 508 reportes colapsan en **838** meses con evento: los 670
repetidos son varios reportes del mismo municipio en el mismo mes. Cualquier cálculo de
frecuencia que divida reportes por municipio-mes sobrestima por un factor de 1,8. T8 tiene
que leer la prevalencia de acá y no del conteo de reportes.

El `build` es función pura de los cuatro parquets, y eso se comprobó corriendo dos veces
seguidas sobre el mismo caché: los dos `manifest.json` salieron con el mismo `sha256`.

### El caché y sus planes

El caché de T3 se descargó el 2026-10-02, antes de que T4 escribiera los rastros de plan
(`weather.plan.json`, `labels.plan.json`, commit `5fac554`) y de que el `parse` publicara
el plan de paginación de etiquetas (`labels_plan.json`, `3bdca35`). Contra ese contrato el
build se negaba, con razón: un parquet sin rastro no se puede atribuir a este caché.

La reparación es la misma que ya resolvió el clima el 2026-10-05: volver a correr el paso
que escribe el plan, no el que descarga. `fetch --source labels` reporta **0 raw copies** —
las tres páginas ya estaban en el caché y cada una es corta frente a `SOCRATA_PAGE = 50 000`
(6 419, 1 494 y 2 072 registros), así que la caminata termina en `p000` de las tres fuentes
sin pedir nada. Después `parse` reescribe los cuatro parquets **byte a byte idénticos** a los
que T3 dejó y escribe los dos rastros. Ninguna descarga, ningún dato nuevo: sólo el
promiso de procedencia que faltaba.

## Decisiones de esta tarea

- **D-T3.1 — el punto del municipio es la cabecera, no el centroide.** La capa MGN 317
  responde `geometry: null`, así que no hay polígono del que calcular centroide. El
  punto es la cabecera municipal del DIVIPOLA, y MGN queda como control del conjunto de
  códigos. Consecuencia asumida: el clima de cada municipio se muestrea en su cabecera,
  que tiende a estar en la parte accesible y poblada.
- **Un mapeo de columnas por dataset** (docs/08 §Fuentes de datos de M2): `divipola` en
  `wwkg-r6te`, `codificaci_n_segun_divipola` en los otros dos. Un año nunca se arma con
  dos fuentes.
- **Normalización de eventos.** `wwkg-r6te` trae `INUNDACIÓN`, `INUNDACIoN` y
  `Creciente Subita` junto a las grafías canónicas (verificado 2026-10-02): sin doblar
  mayúsculas y tildes se perderían 10 reportes. Sobreviven `INUNDACION`,
  `CRECIENTE SUBITA` y `AVENIDA TORRENCIAL`.
- **DIVIPOLA numérico.** `wwkg-r6te` guarda el código como número y pierde el cero
  inicial: `8549` se rellena a `08549`, que es Piojó (Atlántico).
- **Cero no es evidencia faltante.** Un día `null` de ERA5 queda como nulo en el
  dataframe; nunca se convierte en 0 mm.
- **La consulta del clima es una constante** (`ARCHIVE_PARAMS`): el adaptador de
  serving de T6a tiene que repetir `models=era5`, las dos variables, `America/Bogota` y
  el día extra ("Paridad de features", docs/08 §Reglas de gobierno).

## Sesgos conocidos

1. **Sesgo de reporte** (docs/08 §M2 "Etiqueta"): los municipios más accesibles
   reportan más. 180 de 195 municipios tienen al menos un evento, pero la intensidad
   por municipio no es comparable con su exposición real al riesgo.
2. ** Cabecera, no centroide:** ver D-T3.1 arriba. En municipios extensos la cabecera
   puede no representar el clima de la parte inundable.
3. **Cobertura de etiquetas vs. clima:** el clima **termina el 2026-08-31** para los 195
   municipios y las etiquetas el **2025-12-02**, porque `2343-nuqp` todavía no tiene rows
   de 2026. El clima sobra por dos meses respecto de la ventana etiquetada, que es lo
   que hace falta para que la ventana de seis meses de enero de 2019 esté completa. Los
   meses de 2026 quedan sin etiqueta: no son negativos, son desconocidos, y por eso no
   son filas de la tabla.
4. **Duplicados de reporte:** 550 combinaciones (municipio, día, clase) aparecen más
   de una vez, con `source_row_id` distinto. La etiqueta de docs/08 es binaria por
   municipio y mes, así que en T4 se colapsan; T3 las conserva con su id de origen.
5. **Resolución de ERA5:** el archivo histórico devuelve el valor de la celda de 0,25°
   que contiene el punto, no el valor del punto (la respuesta devuelve 10,75 / -74,75 para
   una cabecera en 10,98 / -74,82). Dos municipios vecinos pueden compartir celda.
6. **Cuotas de la fuente:** el nivel gratuito de Open-Meteo pesa una consulta por sus
   **variables, ubicaciones y dominios** (documentación de Open-Meteo, "Rate
   Limiting": 10 000/día por IP, con cubos por minuto y por hora, y 3–5 peticiones
   concurrentes); el archivo histórico pesa además por la longitud del rango pedido.
   La descarga va en ventanas de cuatro años y en trozos de hasta 100 coordenadas
   (el tope que la API impone por petición), y reanuda desde el caché. No se puede
   calcular por adelantado cuántas peticiones gasta el rango completo, y por eso el
   code no lo inventa: mide y respeta el `Retry-After` que llega con el 429.

## Antes de 2019: cerrado, sin DesInventar (D-T3.2)

**Decisión del dueño (D-T3.2, 2026-10-02): el corte es 2019.** Las etiquetas del dataset
son sólo UNGRD 2019-2025 (los tres consolidados de datos.gov.co): 1 508 reportes en 180
de los 195 municipios. No se consulta DesInventar ni ningún consolidado previo a 2019, y
ninguna fuente se mezcla con otra dentro de un año (docs/08 §Fuentes de datos de M2).

Consecuencia para el modelo: la primera mitad de la serie no tiene etiquetas, así que no
hay positivos anteriores a 2019-01 y la línea base de climatología se calcula sólo con lo
que hay. Si algún día se quisiera abrir el corte, sería una decisión nueva (y un host
nuevo autorizado), no un ajuste de este dataset.

## Qué sigue

- **Build real:** hecho el 2026-10-05. `ml/datasets/flood_m2/manifest.json` ya está
  trackeado, con el `sha256` de la región entera y el de cada parquet de entrada.
- **T5:** harness con partición temporal de brecha ≥ 6 meses, hold-out por departamento,
  IC95 y compuerta. También es suyo rellenar `precip_anomaly_{1,3,6}m` con la climatología
  de sus años de train: la tabla las deja nulas a propósito.
- **Etiquetas de 2026:** decidir si el dataset arranca en 2019 o si se espera a que
  `2343-nuqp` publique 2026.
- **T6a:** el cliente de serving debe repetir `ARCHIVE_PARAMS` exactamente.
