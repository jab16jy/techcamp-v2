# Data card — M2 riesgo de inundación: fuentes

**Actualizado:** 2026-10-02 · **Tarea:** E10 T3 (odd/tasks/techcamp-v2-e10-climate-risk.md) ·
**Docs:** [docs/08](../../docs/08-ml.md) §M2 riesgo de inundación, §Fuentes de datos de M2, §Reglas de gobierno

Este documento describe lo que T3 descarga y cómo lo normaliza. T4 arma sobre estas
cuatro tablas la tabla municipio × mes; T5 el harness. Nada aquí decide el modelo.

## Cómo se reproduce

```bash
# Red: sólo los cuatro hosts de abajo. Reanuda: un trozo ya cacheado no se vuelve a pedir.
uv run --locked --project ml python -m techcamp_ml.sources fetch
# Sin red: los parquets se reconstruyen desde ml/.cache/raw.
uv run --locked --project ml python -m techcamp_ml.sources parse
```

Cada respuesta cruda queda en `ml/.cache/raw/<fuente>/` (fuera de git) con su URL, su
fecha de descarga y su `sha256` en `ml/.cache/raw/MANIFEST.tsv`. Los parquets de
`ml/data/flood_m2/sources/` salen **sólo** de esas copias: "reproducible o no existe"
(docs/08 §Reglas de gobierno).

## Fuentes

| Dato | Fuente | Licencia | Versión / descarga | Hash (primeros 16) |
|---|---|---|---|---|
| Municipios (punto) | DIVIPOLA, códigos municipios, `https://www.datos.gov.co/resource/gdxc-w37w.json` | CC BY-SA 4.0 | 2026-10-02T17:45:26Z | `ecf12e564067cb29` |
| Municipios (control) | DANE MGN 2024, capa Municipio 317, `https://portalgis.dane.gov.co/mparcgis/rest/services/MGN2024/Serv_CapasMGN_2024/MapServer/317/query` | CC BY-SA 4.0 (DANE) | 2026-10-02T17:45:27Z | `545d85378ba1784` |
| Etiquetas 2019–2022 | UNGRD, `https://www.datos.gov.co/resource/wwkg-r6te.json` | CC BY-SA 4.0 | 2026-10-02T17:27:25Z | `171266650fe22614` |
| Etiquetas 2023–2024 | UNGRD, `https://www.datos.gov.co/resource/rgre-6ak4.json` | CC BY-SA 4.0 | 2026-10-02T17:27:28Z | `7769c6d43fc754f8` |
| Etiquetas 2025+ | UNGRD, `https://www.datos.gov.co/resource/2343-nuqp.json` | CC BY 4.0 | 2026-10-02T17:27:32Z | `7e54b5371993eb8` |
| Clima diario | Open-Meteo archive, `https://archive-api.open-meteo.com/v1/archive`, `models=era5` | Open-Meteo, uso no comercial ([ADR-0021](../../docs/adr/0021-perfil-seminario-local.md)) | T3, descarga en curso al redactar esta tarjeta | ver `MANIFEST.tsv` |
| Elevación y vecino | Open-Meteo elevation (Copernicus GLO-90), `https://api.open-meteo.com/v1/elevation` | Open-Meteo, uso no comercial | 2026-10-02T17:28:00Z | `d52dc71cce74be05` |

Open-Meteo gratuito es de uso no comercial: cubre el seminario; producción necesita su
plan de pago (docs/08 §Fuentes de datos de M2).

## Región y rango

- **Región:** los 7 departamentos del Caribe continental (08, 13, 20, 23, 44, 47, 70),
  **195 municipios** (docs/08 §M2 "Región"). San Andrés queda fuera.
- **Municipios:** `code` DIVIPOLA de 5 dígitos, `name`, `department_code`,
  `department_name`, `lat`, `lon`. El control MGN 2024 tiene que dar exactamente el
  mismo conjunto de `mpio_cdpmp`; si no, la construcción falla.
- **Clima:** `precipitation_sum` y `soil_moisture_0_to_7cm_mean` diarios, desde
  **2018-06-30** (un día antes del inicio) hasta el último mes completo. La serie
  empieza en 2018-07-01 para que las ventanas de seis meses de docs/08 §M2 "Features"
  estén completas antes del primer mes etiquetado, 2019-01.
- **Etiquetas:** del **2019-01-01** (primera región) al **2025-12-31**.

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
clase, 0 fechas ilegibles. La descarga ya filtra por evento; el parser vuelve a
clasificar y cuenta, para que una fila perdida por un valor raro sea visible.

**Otros conteos:** elevación 195 filas (2 a 1 307 m, media 90 m); los cuatro vecinos a
1 km se piden en la misma llamada y salen en las columnas `east_m`, `west_m`,
`north_m`, `south_m`, que son las que consume `techcamp.risk.domain.features.Neighbours`.

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
3. **Cobertura de etiquetas vs. clima:** el clima llega al último mes completo
   (2026-09-30 con esta descarga) y las etiquetas terminan el 2025-12-31, porque
   `2343-nuqp` todavía no tiene rows de 2026. Los meses de 2026 quedan sin etiqueta:
   no son negativos, son desconocidos, y T4 no debe contarlos como tales.
4. **Duplicados de reporte:** 550 combinaciones (municipio, día, clase) aparecen más
   de una vez, con `source_row_id` distinto. La etiqueta de docs/08 es binaria por
   municipio y mes, así que en T4 se colapsan; T3 las conserva con su id de origen.
5. **Resolución de ERA5:** el archivo histórico devuelve el valor de la celda de 0,25°
   que contiene el punto, no el valor del punto (la respuesta devuelve 10,75 / -74,75 para
   una cabecera en 10,98 / -74,82). Dos municipios vecinos pueden compartir celda.
6. **Cuotas de la fuente:** Open-Meteo gratuito mide por variables × días, no por
   coordenadas. La descarga va en ventanas de cuatro años (2 920 de peso por llamada,
   bajo el tope horario de 5 000) y reanuda desde el caché; sin eso, 24 llamadas de
   25 coordenadas gastaban 70 080 de peso contra un tope diario de 10 000.

## Antes de 2019: sin resolver

docs/08 §Fuentes de datos de M2 deja los consolidados anuales de la UNGRD (1998–2021) y
la exportación de DesInventar Colombia como respaldo, **sólo si su formato permite el
mismo mapeo por municipio y día**, unidos con un año de corte fijo y nunca mezclando
fuentes dentro de un año.

**No se pudo verificar en esta tarea**: los hosts de esos archivos no están en la lista
de hosts autorizados para T3, así que no se descargó ni un esquema. La decisión queda
abierta para el dueño: sin ellos, el dataset empieza en 2019, como está hoy. Lo que hace
falta para cerrarlo es comprobar si esos archivos traen el municipio como código DIVIPOLA
o sólo como nombre (con los problemas de tildes y de cambios de nombre que implica), y si
traen fecha de inicio por evento.

## Qué sigue

- **T4:** tabla municipio × mes con **todos** los negativos (ningún submuestreo),
  unidad y mes según docs/08 §M2 "Unidad"/"Horizonte", features del módulo compartido
  `techcamp.risk.domain.features` y el hash del dataset.
- **T5:** harness con partición temporal de brecha ≥ 6 meses, hold-out por departamento,
  IC95 y compuerta.
- **Etiquetas de 2026:** decidir si el dataset arranca en 2019 o si se espera a que
  `2343-nuqp` publique 2026.
- **T6a:** el cliente de serving debe repetir `ARCHIVE_PARAMS` exactamente.
