# Hardening de demo: sin trampas operativas ni resultados que mienten

> Feature doc del trabajo de endurecimiento de la demo del(stack de seminario +
> simulador de escenarios de E16). Cubre los issues #341, #214, #335, #333, #334,
> #339 (parcial), #337, #340, #342, #277 y #258.
>
> Regla de este doc: los docs mandan sobre el código (AGENTS.md regla 2). Cada
> decisión de diseño de abajo lleva su número `D-DH.n` y se aplica ANTES del
> código que la cumple.

## Objective

Que la aplicación pueda evaluarse en vivo —stack de seminario con simulador de
escenarios A–E— sin trampas operativas (pasos que el presentador tiene que
recordar) ni comportamientos que parezcan fallos del producto (una tarjeta en
verde que significa "no hay riesgo", un `200 OK` donde no se corrió nada, un
clima que el escenario anterior sobrescribió).

## Problem

La demo vive en la intersección de tres piezas que hoy no se hablan:

1. **El stack de seminario** (`infra/compose.yaml`) levanta servicios que se
   bloquean entre sí, y uno de ellos (`minio-init`) depende de una imagen que
   no se puede descargar de forma anónima.
2. **El simulador de escenarios de E16** (`server/src/techcamp/simulator/`)
   corre escenarios sobre el mismo stack y deja estado residual entre corridas.
3. **El gate de CI** (`just gate`) usa la base de datos del stack de seminario
   y la degrada al terminar.

Cada pieza funciona por separado. Juntas producen: un stack que no levanta, un
balance que corre con el clima del escenario anterior, una recomendación que
nunca se emite pero la API dice que sí, y un `just gate` que deja la demo sin
base de datos.

## Why

La demo se evalúa en vivo y con una persona al otro lado de la mesa. Cada
"trampa" cuesta credibilidad: si el presentador tiene que recordar un
`podman rm -f`, si un `just gate` tumba el stack, o si la pantalla de riesgo
dice "bajo" cuando en realidad no hay umbrales calibrados, el público
concluye que el producto no funciona —cuando lo que falla es la preparación.

Los issues ya existen, con evidencia y con opciones investigadas. Este doc no
redescubre: decide.

## Scope

**Dentro:**

| Unidad | Issue | Módulo | Trabajo |
|---|---|---|---|
| U1 | #341 | `server/tests`, `justfile` | El gate de tests no puede matar el stack vivo |
| U2 | #214 | `infra`, `server/src/techcamp/irrigation`, `server/src/techcamp/farms`, `justfile` | El stack de seminario levanta y opera sin trucos |
| U3 | #337 | `server/src/techcamp/simulator` | YAML que no parsea responde problem+json, no una traza |
| U4 | #334 | `server/src/techcamp/alerts` | Un nodo retirado nunca abre `node_offline` |
| U5 | #333 | `server/src/techcamp/irrigation` | `replace=true` no deja parcelas de secano fantasma |
| U6 | #335 | `server/src/techcamp/simulator/scenarios` | Una celda climática por escenario |
| U7 | #340 | `server/src/techcamp/farms` (o verificación) | El catálogo reproduce la aritmética FAO-56 |
| U8 | #342 | `server/tests`, `web/e2e` | El paso del outbox del escenario A es ganable |
| U9 | #339 | `justfile`, `README` | Higiene entre corridas |
| U10 | #277 | `web/src/features/risk` | La pantalla de riesgo climático existe |
| U11 | #258 | `web/src/features/metrics` | `relative_yield` null pero honesto |

**Fuera (no se tocan):**

- El harness ML, el test set y la compuerta de promoción (ADR-0020).
- La cadena de PRs de E16 (#343 → #352): ya mergeada en `main` (verificado
  con `gh pr list`); este trabajo arranca **encima de `main`**, no la rebasa.
- `field_record` y la ingesta de EVA/AGROSAVIA (queda como épica con dueño,
  D-DH.11).
- El caso de uso `delete_plot` en farms (follow-up de #333, D-DH.7).

## Constraints

- **TDD**: RED → GREEN → REFACTOR. Cada bug entra por una prueba que falla.
- **Commits work-unit**: una unidad de trabajo = un commit = un PR encadenado
  con `Fixes #N`.
- **Conventional Commits** sin atribución de IA.
- **`just` para todo gate**; durante la implementación se corren pruebas
  acotadas y el `just gate` completo se corre una sola vez, al cierre.
- **Verificación**: `verifier` de gentle-ai (sin RDD). Este doc usa la
  estructura de ODD del repo (`Objective`, `Problem`, `Why`, `Scope`,
  `Decisions`, `Acceptance criteria`, `Checks`), no la de RDD.
- **Sin atribución de IA** en los commits y en los PRs.

## Base y cadena de PRs

`gh pr list` al 2026-10-25: la cadena de E16 (#344 → #352) está **mergeada** en
`main`. Este trabajo es una cadena nueva sobre `main`, en el orden U1…U11
(agrupable por módulo si el reviewer gana foco).

```
main ──▶ U1 (#341) ──▶ U2 (#214) ──▶ U3 (#337) ──▶ U4 (#334) ──▶ U5 (#333)
      ──▶ U6 (#335) ──▶ U7 (#340) ──▶ U8 (#342) ──▶ U9 (#339)
      ──▶ U10 (#277) ──▶ U11 (#258)
```

## State verified before writing (no se redescubre nada)

Verificado sobre `main` (worktree `../techcamp-v2-demo-hardening`, commit
`a6382f1`):

| Issue | Estado real sobre `main` |
|---|---|
| #340 | **Resuelto** con la opción (b) del owner: `docs/06-diseno-detallado.md:427,467` y los commits `8b873f2` + `b3279bf` (edades de siembra dentro de `mid`, tripwire de E ya en `verdict.passed === true`). La opción (a) del brief **no** es lo que el repo hizo. |
| #342 | El fix del "outbox del paso" **ya está** en `b3279bf` (backfill pausado a 50 ms → los límites de flush del ingestor quedan separados y la escalada de 48 h cabe en la ventana). Falta la verificación en vivo y el cierre. |
| #341 | Vigente: `server/tests/conftest.py:39` degrada a `base` al final de la sesión sin distinguir la base del gate de la base del stack. |
| #214 | Parcial. El bootstrap sin SQL **ya existe** (`server/src/techcamp/simulator/provision.py` crea organización, membresía, granja, parcela, ciclo, perfil de suelo y nodos sin reclamar). Vigentes: `minio-init` con `quay.io/minio/mc:latest`; sin receta de recreación de contenedores; `irrigation:run` sin distinguir "corrió" de "se saltó"; `soil:autofill` con `root_depth_cm = None`. |
| #337 | Vigente: `server/src/techcamp/simulator/scenario_schema.py:490` deja `yaml.safe_load` sin envolver. |
| #334 | Vigente: `server/src/techcamp/alerts/application/evaluate_node_health.py:71` llama `nodes.list_for_org` sin filtro de estado. |
| #333 | Vigente: `server/src/techcamp/irrigation/adapters/jobs.py:75-88` mete toda parcela de secano en el alcance del balance; `server/tests/simulator/test_scenario_load.py:367` **pina el bug**. |
| #335 | Vigente: los cuatro fixtures declaran `cell: {lat: 11.0, lon: -74.8}`. |
| #339 | `just scenario-clean` existe (`justfile`, receta `scenario-clean`); falta comprobar que cubre el estado residual del e2e y que está documentado. |
| #277 | `web/src/lib/api/schema.d.ts` **ya tiene** `RiskPredictionView` (línea 2650) y la ruta `GET /plots/{plot_id}/risk` (línea 863). Falta toda la capa de UI (`web/src/features/risk/` no existe). |
| #258 | Vigente: `relative_yield` solo existe en `web/src/lib/api/schema.d.ts:1357`; la UI de métricas no lo lee. |

## Decisions — `D-DH.1` … `D-DH.14`

### `D-DH.1` — El gate tiene su propia base de tests (U1, #341)

Dos piezas, y las dos hacen falta:

1. **El gate no corre los tests del server sobre la base del stack.** El
   contenedor de Postgres del worktree aloja **dos** bases: `techcamp` (la del
   stack de seminario) y `techcamp_test` (la del gate). `just db-up` crea las
   dos; `just db-test-up` crea la segunda si falta (un worktree cuyo contenedor
   es anterior a esta receta no la tiene); las recetas `gate`, `gate-full` y
   `gate-release` apuntan los tests del server a `techcamp_test`.
2. **El marcador `TECHCAMP_TEST_DB=1` decide si la sesión dropea el esquema.**
   `server/tests/session_teardown.py` lo lee: solo una sesión que dice
   "esta base es mía" hace `alembic downgrade base` al terminar.

Razón (verificada, no supuesta): el `DATABASE_URL` del gate **es** la base del
stack —la receta `gate` la deriva de `db-env` y el stack apunta al mismo
contenedor por `POSTGRES_PORT`—, así que el `downgrade` de la sesión se lleva
por delante el esquema del stack vivo: el worker muere con
`ConnectorException` y el siguiente escenario necesita un `alembic upgrade
head` a mano.

El marcador por sí solo no bastaría: si el gate apuntara a la base del stack y
se marcara, seguiría dropeando el esquema del stack. Por eso el marcador
necesita una base que sea de verdad del gate — y la segunda base lo es. La
dirección del error es la segura: **por defecto no se dropea nada**; una
sesión sin el marcador (un `uv run pytest` directo, que AGENTS.md documenta
como herramienta) deja el esquema donde está. En CI la base es desechable y
exclusiva del job, así que ahí el marcador sí va y el `downgrade` sigue
happeneciendo.

La garantía de #89 (que el esquema de una corrida sea exactamente el de las
migraciones, sin tablas de una corrida anterior) sigue intacta: la sesión del
gate dropea su base al terminar, y `gate-full` además la recrea entera.

### `D-DH.2` — Defensa en profundidad: pre-flight de esquema en el gate de escenarios (U1, #341)

La rama `web/e2e/*` de la receta `gate` corre, antes de Playwright, un
pre-flight contra la base **del stack**: `alembic current` dice si el esquema
está en `base` y, si lo está, `alembic upgrade head` lo vuelve a `head`.

Razón: si el esquema del stack estuviera en `base`, el escenario fallaría por
una razón que no es del escenario, y el mensaje de error no lo diría. El
pre-flight dice en voz alta en qué estado estaba la base y la repara.

### `D-DH.3` — `minio-init` deja de depender de una imagen que no se descarga (U2, #214)

`minio-init` se reconstruye desde el mismo contexto local `server` que
`migrate`, `api` y `worker`, y crea los buckets con `boto3` (ya dependencia del
proyecto server, `server/pyproject.toml:14`), en vez de `quay.io/minio/mc`.

Razón (verificada, no supuesta): en este entorno `quay.io` deniega **todo** pull
anónimo —`quay.io/minio/mc:latest` y también `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z`
fallan con `unauthorized`— y `docker.io/minio/mc` y `docker.io/minio/minio`
fallan con `requested access to the resource is denied`. Docker Hub sí sirve
otras imágenes (`eclipse-mosquitto:2`, `timescale/timescaledb-ha:pg16`):
el bloqueo es de esos repositorios, no del registro. Ninguna imagen de MinIO es
descargable, así que "pinar un digest" no se puede verificar ni depender de
ello. La imagen local del server no se descarga de ningún registro: se construye.

Además, `minio-init` deja de bloquear a `api`: `api` depende de que el
contenedor `minio` esté **healthy**, no de que `minio-init` haya terminado con
código 0. El init es *best-effort* y su fallo se reporta, no tumba el stack.

### `D-DH.4` — `just stack-rebuild` y el runbook de la demo (U2, #214, U9, #339)

Una receta `just stack-rebuild` hace el ciclo completo que hoy el presentador
tiene que recordar: `podman rm -f` de los contenedores del proyecto,
`podman-compose up` de nuevo y espera de health. El runbook (en `README.md`)
dice `uvx podman-compose` (el binario `podman-compose` no está instalado en la
máquina de la demo) y el `podman rm -f` que hace falta tras un rebuild de
imagen.

Razón: #214 y #339 son el mismo problema visto desde dos lados —el stack no
levanta limpio y el estado residual no está documentado en ningún lado.

### `D-DH.5` — `irrigation:run` distingue "corrió" de "se saltó" (U2, #214)

`POST /dev/jobs/irrigation:run` responde `skipped` con el motivo cuando el
balance del día se saltó por falta de clima observado, y dice qué correr antes
(`weather:run`).

Razón: hoy la respuesta es `succeeded` y el presentador cree que el balance
corrió. La razón ya existe en el dominio (`DailyBalanceResult.skip_reason`);
lo que falta es que viaje hasta la respuesta.

### `D-DH.6` — `soil:autofill` avisa que falta la profundidad de raíz (U2, #214)

`POST /plots/{plot_id}/soil:autofill` sigue **sin** inventar `root_depth_cm`
(la profundidad de raíz es una decisión de cultivo, no una propiedad del suelo —
decisión T5 ya documentada en `server/src/techcamp/farms/domain/models.py:323`),
pero la respuesta **avisa** que falta y qué se necesita para el balance
hídrico.

Razón: la alternativa (llenarlo con un valor inventado) rompe la regla del repo
—no inventar valores. El aviso convierte un hueco silencioso en una instrucción
accionable, que es lo que el issue pide ("que lo llene, **o** que la respuesta
avise que falta").

### `D-DH.7` — El balance diario solo toma parcelas de secano con ciclo activo (U5, #333)

`eligible_plot_ids` deja de meter toda parcela con `irrigation_system = none`:
una parcela de secano entra al alcance del balance **solo si tiene un ciclo
activo**. `docs/06 §5` se enmienda: la rama del consejo de siembra
(`delay_sowing`) sale del flujo del balance diario.

Razón: la parcela fantasma de `replace=true` es exactamente una parcela de
secano cuyo ciclo se cerró —sin ciclo activo—, y por eso quedaba en el
alcance para siempre y emitía `delay_sowing` todos los días. El caso de uso
`delete_plot` en farms (que cerraría ciclos y retiraría nodos de verdad) queda
como follow-up: es escala mayor y no hace falta para que la demo no mienta.

**Consecuencia de producto que el owner acepta con esta decisión:** una parcela
de secano recién creada y todavía no sembrada deja de recibir `delay_sowing`.
Queda anotado en el doc dueño y en el PR.

### `D-DH.8` — `retired` es terminal para la regla `node_offline` (U4, #334)

`evaluate_node_health` excluye los nodos con estado terminal `retired`; **no**
filtra a `ONLINE`.

Razón: un nodo retirado no va a volver a publicar, así que "no publica desde
hace N horas" es un hecho esperado, no una falla. Pero un nodo `online` que
se cayó tiene que seguir pudiendo abrir la alerta: por eso el filtro excluye
`retired` y no exige `ONLINE`.

### `D-DH.9` — `load_scenario` traduce el error del parser (U3, #337)

`yaml.safe_load` se envuelve y su `yaml.YAMLError` se re-lanza como
`ScenarioSchemaError` —el mismo tipo que ya traduce la ruta a
`problem+json`—.

Razón: el módulo de esquema no debe depender de la excepción de una
biblioteca de terceros en su contrato; el adaptador (la ruta) ya sabe
traducir `ScenarioSchemaError` a `problem+json`.

### `D-DH.10` — Cada fixture climateático vive en su propia celda (U6, #335)

Los cuatro fixtures se graban en su propio punto geográfico (el grabador ya
deriva la celda de las coordenadas) y cada YAML actualiza su pin
`weather_fixture_sha256`. `docs/06 §10` y ADR-0021 se amendan si asumían la
celda compartida.

Razón: `weather_daily` tiene clave `(cell_id, day, is_forecast)`. Dos fixtures
en la misma celda se pisan el mismo día —y los escenarios se anclan a "hoy",
así que el solapamiento es real. La alternativa (una columna de escenario en
`weather_daily`) es un cambio de esquema en el módulo de clima: más caro que
regrabar, y no compra nada que el recorder no compre.

Los cuatro puntos se eligen **dentro del municipio de Santa Marta** (DIVIPOLA
47001), que es el municipio que el loader declara: así la celda cambia y el
municipio sigue siendo cierto.

**Los cuatro puntos, y por qué estos** (elegidos barriendo el polígono del
municipio que publica el DANE —capa 317 de MGN 2024— y pidiendo el archivo de
Open-Meteo en cada punto, 113 candidatos dentro del municipio):

| Fixture | Punto grabado | Celda (0,1°) | ET0 observado | ETc |
|---|---|---|---|---|
| `el-nino-caribe` (A) | 11,22 N / 74,23 O | 11,2 / 74,2 | 4,019 mm/día | 4,822 mm/día |
| `veranillo-caribe` (E) | 11,12 N / 74,23 O | 11,1 / 74,2 | 4,159 mm/día | 4,990 mm/día |
| `normal-caribe` | 11,14 N / 74,13 O | 11,1 / 74,1 | 4,164 mm/día | 4,996 mm/día |
| `rainy-season-caribe` (B) | 11,22 N / 74,09 O | 11,2 / 74,1 | 3,160 mm/día | 3,792 mm/día |

El punto de cada fixture se eligió por dos condiciones que la aritmética de
`docs/06 §10` impone, no por conveniencia:

1. **La aritmética del escenario sobrevive.** Para E, `ETc` tiene que quedar
   entre 4,81 y 5,13 mm/día para que `Dr = ETc × día` cruce `RAW = 77 mm` el
   **día 16** (el día 15 queda en 74,9 mm y el 16 en 79,8 mm: los números que
   `docs/06:468` ya afirma). Para A, `ETc ≈ 5` mantiene `p = 0,55` y
   `θ_estrés = 15,3 %`. Ninguno de los cuatro fixtures abre un día de
   `fungal_risk` (RH > 85 % **y** temperatura media en 20-30 °C), salvo el de B,
   que es el que debe abrirlo.
2. **La celda es distinta de la de los otros tres.** La clave de `weather_daily`
   es `(cell_id, day, is_forecast)`, así que dos fixtures en la misma celda se
   pisan el mismo día.

Lo que la regrabación **cambia** en los documentos, y solo esto: el fixture de
`normal` pasó de `ET0 ≈ 4,3` a `ET0 ≈ 4,2 mm/día` (su comentario en el YAML y
el bloque de `docs/06 §10`). La aritmética de A y de E **no se mueve**: los
números de `docs/06:440-446` y `docs/06:465-469` siguen siendo los del
documento, porque `ETc` sigue roundando a 5 y `RAW` a 77.

### `D-DH.11` — `relative_yield` null se comunica como hueco (U11, #258)

La UI de métricas muestra `—` (y la razón D-T0.9) cuando `relative_yield` es
`null`. **No** se construye `field_record` ni se ingiere EVA/AGROSAVIA aquí: ese
trabajo completo se registra como épica con dueño en `docs/10` y como feature
doc / issue propios.

Razón: el hueco está declarado y es honesto; inventar un valor no lo es. El
trabajo grande (mediana EVA) es escala épica y necesita su propio doc y su
propio dueño.

### `D-DH.12` — La pantalla de riesgo declara su origen (U10, #277)

**Decidido por el owner (2026-10-25):** la tarjeta nombra la línea base y
distingue "severidad baja" de "sin cortes calibrados"; con umbrales vacíos la
severidad **no** se muestra como `bajo`.

La tarjeta de riesgo nombra la línea base (`model_version.is_baseline`) y
distingue "severidad baja" de "sin cortes calibrados": con umbrales vacíos la
severidad **no** se muestra como `bajo`, sino como el estado que el modelo sí
puede afirmar.

Razón (la misma que da el issue): el riesgo que se sirve hoy es la línea base
`climatology_month`, sin umbrales calibrados, así que **toda** predicción sale
en `low`. Mostrar "bajo" sin decir de dónde viene es la trampa que el issue
describe: dos tarjetas en verde que el lector entiende como "no hay riesgo".

### `D-DH.14` — El ruido del canal de suelo del escenario A baja a 0,1 (U8, #342)

El ruido de la rampa de humedad de suelo de A pasa de **±0,8** a **±0,1**
puntos de porcentaje. La apertura de la alerta sigue siendo la documentada
(≈ 10,7) y la lectura sigue siendo imperfecta; lo que cambia es que el
critical cae **dentro** de la ventana de 14 días.

Por qué 0,1: la rampa cae 9 puntos en 14 días, o sea **0,64 %/día**. Un ruido
de ±0,8 es **1,24 días de la propia pendiente de la rampa**: la serie con
ruido se dispara por encima de θ_estrés los días 9, 10 y 11 (76/48/11 muestras
por encima del umbral), y `sustained_run` es estricto, así que cada disparo
rompe la corrida de 6 h. La primera ventana limpia de 24 muestras terminaba en
el día ≈ 12,8, la alerta abría en el día ≈ 12,9 y el critical (48 h después,
≈ 14,9) **nunca existía** dentro de la ventana: el paso del outbox no podía
ganarse nunca. Con 0,1 la alerta abre en el ≈ 10,7 documentado y el critical
cae en el ≈ 13, dentro de la ventana.

Descartadas, y por qué:

- **Acortar `min_duration`**: cambia la semántica de la regla (6 h es lo que
  la regla dice), no la aritmética de la demo.
- **Extender el backfill** a más de 14 días: mueve el cruce documentado y
  rompe el pin del test del servidor (`sample 1025`).
- **Cambiar la semilla**: es suerte, no un arreglo.
- **Relajar el oráculo**: debilita el criterio del escenario, que es justo lo
  que #342 vino a demostrar.

El criterio de aceptación se reescribe con lo que el spec realmente hace: el
spec de A es **un** test con pasos nombrados, no tres tests.

## Acceptance criteria

- **U1 (#341)**: con el stack de seminario vivo, `just gate server/tests/simulator`
  no degrada la base del stack; el worker sobrevive y el siguiente escenario
  corre sin intervención manual.
- **U2 (#214)**: `just stack-rebuild` levanta el stack completo sin
  `minio-init` descargando nada de un registro; `POST /dev/jobs/irrigation:run`
  responde `skipped` con motivo cuando falta el clima observado;
  `POST /plots/{id}/soil:autofill` avisa que falta `root_depth_cm`.
- **U3 (#337)**: un YAML inválido devuelve `problem+json` con el detalle del
  error de parseo, no una traza.
- **U4 (#334)**: un nodo `retired` sin publicar no abre alerta; un nodo activo
  sin publicar **sí** la abre en ~3 × `interval_s`.
- **U5 (#333)**: cargar `dry-spell-rainfed` con `replace=true` dos veces deja
  **una** parcela en el alcance del balance y **cero** recomendaciones
  `delay_sowing` espurias.
- **U6 (#335)**: dos escenarios con fixtures distintos no pisan las filas del
  otro; cada fixture tiene su propia celda y su pin actualizado.
- **U7 (#340)**: el pipeline grabado de E responde `water_stress` con el cruce
  en el día documentado y el tripwire del e2e queda en `verdict.passed === true`.
- **U8 (#342)**: `el-nino.spec.ts` termina en verde con el paso del outbox (el
  SMS de escalación `critical` de `water_stress` en `/dev/outbox`), en dos
  corridas sobre stack limpio.
- **U9 (#339)**: `just scenario-clean` deja el stack sin estado residual del e2e
  y el runbook lo dice.
- **U10 (#277)**: `GET /plots/{plot_id}/risk` se ve en la interfaz con
  severidad/probabilidad/horizonte/factores, y la línea base se declara.
- **U11 (#258)**: la UI de métricas muestra `—` y la razón cuando
  `relative_yield` es `null`.

## Checks

- Por unidad: pruebas acotadas del módulo tocado (RED → GREEN → REFACTOR).
- `just gate <paths>` con los tests nombrados, una vez al cierre de cada
  unidad.
- `just gate-lane main` antes de cortar la cadena de PRs.
- `just gate-release` antes de cada PR (regla pre-PR #234).
- `just gate-full` una vez al cerrar.
- Verificación final con el agente `verifier` de gentle-ai (sin RDD).

## Pruebas en vivo (la aceptación real)

1. Stack de seminario limpio → cargar E → `water_stress` se abre con el cruce
   documentado y su recomendación.
2. Stack limpio → cargar A → la escalación `critical` llega al outbox.
3. `replace=true` dos veces → sin parcelas ni nodos fantasma, sin alertas
   espurias.
4. Correr el gate de servidor con el stack vivo → el stack sobrevive.
5. Navegar la pantalla de riesgo con datos.

## Review (RDD)

> No aplica: este trabajo se verifica con el agente `verifier` de gentle-ai,
> no con el ciclo RDD. La sección queda como marcador de la verificación que
> corre al cierre.

## Open questions (owner)

- **`D-DH.7` (U5)**: la decisión elimina la rama de consejo de siembra de
  `docs/06 §5`. Si el owner prefiere conservar `delay_sowing` para las
  parcelas de secano **nunca sembradas** (y sacar solo las que ya tuvieron un
  ciclo cerrado), la regla que discrimina es "tiene algún ciclo" en vez de
  "tiene un ciclo activo", y `docs/06 §5` no se toca. **Decisión tomada en
  esta unidad: se aplica la opción del brief (ciclo activo) y se amendia
  `docs/06 §5`.**

## Progress / evidence

### U1 — #341 (el gate no tumba el stack vivo)

- **Commit**: este commit.
- **Qué cambió**: `server/tests/session_teardown.py` (nuevo) decide con el
  marcador `TECHCAMP_TEST_DB` si la sesión dropea el esquema;
  `server/tests/conftest.py` usa esa decisión; `justfile` crea y usa
  `techcamp_test` para los tests del server y añade el pre-flight del gate de
  escenarios; `.github/workflows/ci.yml` marca la base del job (desechable y
  exclusiva) para que el `downgrade` siga ahí.
- **Prueba**: `server/tests/test_session_teardown.py` — el marcador decide, el
  `downgrade` solo ocurre con el marcador, y el esquema de una base ajena
  sobrevive al teardown. RED verificado: sin el guard fallan 2 de las 3.
- **Comprobación en vivo** (base del worktree, puerto 60621):

  | Corrida | `techcamp_test` después | `techcamp` (la del stack) después |
  |---|---|---|
  | pytest con `TECHCAMP_TEST_DB=1` (lo que hace el gate) | en `base` (dropeada) | `a7d3f1c95b28 (head)` |
  | pytest sin el marcador (lo que hace un `uv run pytest` directo) | — | `a7d3f1c95b28 (head)` |

  El stack sobrevive en los dos casos; la garantía de #89 sigue en el gate.

### U3 — #337 (un YAML que no parsea responde problem+json)

- **Commit**: este commit.
- **Qué cambió**: `load_scenario` envuelve `yaml.safe_load` y re-lanza como
  `ScenarioSchemaError`, el tipo que la ruta ya traduce a 500 + `problem+json`
  (`server/src/techcamp/simulator/adapters/api/dev_scenarios.py:157`).
- **Prueba**: `test_a_yaml_that_does_not_parse_is_a_broken_contract` — un YAML
  inválido en un directorio temporal (el repositorio no se toca) es un contrato
  roto, no una traza. RED verificado: sin el arreglo la prueba falla con
  `YAMLError`; con él, 24 de 24 en verde.

### U4 — #334 (un nodo retirado nunca abre `node_offline`)

- **Commit**: este commit.
- **Qué cambió**: `evaluate_node_health` excluye los nodos `retired` del
  barrido. Solo `retired`: la regla no se convierte en una regla "el nodo está
  online", porque un nodo que se cayó es justo el caso para el que existe.
- **Pruebas**: `test_a_retired_node_that_never_published_opens_no_alert` y
  `test_an_active_node_that_never_published_opens_the_alert`. RED verificado:
  sin el arreglo la primera falla; con él, 9 de 9 en verde.

### U5 — #333 (`replace=true` no deja parcelas de secano fantasma)

- **Commit**: este commit.
- **Qué cambió**: el alcance del balance diario son las parcelas con **ciclo
  activo** (`irrigation/adapters/jobs.py::eligible_plot_ids`). La parcela de una
  carga reemplazada conserva su fila, pero su ciclo quedó cerrado, así que
  sale del alcance y deja de recibir una recomendación `delay_sowing` cada día.
  `docs/06 §5` se amendó: el flujo del job y el alcance.
- **Consecuencia de producto que el owner acepta con `D-DH.7`**: el job diario
  ya no pide consejo de siembra a una parcela de secano sin ciclo. La regla
  `delay_sowing` sigue viva en el dominio (`decide_recommendation` la sigue
  devolviendo) y el caso de uso la sigue dando cuando se le pregunta
  directamente; lo que cambió es a quién pregunta el job.
- **Pruebas**: `test_a_rainfed_replace_leaves_one_parcel_in_the_balance_scope`
  (dos cargas con `replace=true` → una sola parcela en el alcance),
  `test_fanout_queues_one_job_per_eligible_plot_and_none_for_irrigated_without_cycle`
  (la parcela de secano sin ciclo ya no se encola) y
  `test_fanout_rerun_same_day_does_not_duplicate_jobs` (con ciclo activo).
  `irrigation` + `simulator`: 398 en verde, 27 omitidos.

### U6 — #335 (cada fixture climateático en su propia celda)

Los cuatro fixtures se regrabaron desde el archivo de Open-Meteo, cada uno en su
propio punto dentro de Santa Marta, y cada YAML actualizó su pin
`weather_fixture_sha256`.

| Fixture | Celda antes | Celda ahora | Punto grabado |
|---|---|---|---|
| `el-nino-caribe` | 11,0 / 74,8 | **11,2 / 74,2** | 11,22 N / 74,23 O |
| `veranillo-caribe` | 11,0 / 74,8 | **11,1 / 74,2** | 11,12 N / 74,23 O |
| `normal-caribe` | 11,0 / 74,8 | **11,1 / 74,1** | 11,14 N / 74,13 O |
| `rainy-season-caribe` | 11,0 / 74,8 | **11,2 / 74,1** | 11,22 N / 74,09 O |

- **Los cuatro dentro del municipio**: cada punto se comprobó contra el
  polígono de la capa 317 de MGN 2024 que publica el DANE (`FeatureServer`, no
  el `MapServer`, que es el que devuelve `geometry: null`).
- **La regrabación es real, no sintética**: los cuatro fixtures llevan
  `synthetic: false` y su `source_url` con el punto y la ventana de cada uno.
- **Pines**: los seis YAMLs los llevan (los tres que comparten
  `normal-caribe.json` comparten también su pin, que es el mismo archivo).
- **Regresión de no-solape** (`server/tests/simulator/test_scenario_weather.py`):
  - `test_every_fixture_has_its_own_cell` — las cuatro celdas son distintas y
    todas dentro de Santa Marta.
  - `test_two_scenarios_never_write_over_each_other` — cargar E después de A
    deja las filas de A intactas: es el fallo que #335 reportó, afirmado.
- **Documentos enmendados**: `docs/06 §10` (la regla de grabación y el bloque
  de los dos YAML de ejemplo), `ADR-0021` (la promesa de que cada escenario
  responde a su clima, que solo era cierta si las celdas son distintas) y el
  docstring de `MUNICIPALITY_CODE` en el loader.
- **Lo que se movió**: el ET0 de `normal` pasó de ≈ 4,3 a ≈ 4,2 mm/día. La
  aritmética de A (`ETc ≈ 5`, `p = 0,55`, `θ_estrés = 15,3 %`) y la de E
  (`ETc ≈ 5`, `RAW = 77`, el cruce del día 16) **no se mueven**.

#### El hallazgo de la corrida de los seis escenarios (2026-10-26)

La regrabación de B **rompió el escenario `rainy-season`**, y nada en el repo lo
decía. `evaluate_org_fungal_risk` decide sobre el cell-day **anterior** al día
del job (D10, D19), y el loader ancla los días observados antes de hoy: el día
que la regla lee es el **último día observado** del fixture. En la captura de B
ese día quedó en **84 %** de humedad relativa, y la regla es `air_rh > 85`: la
alerta no abría y el e2e contestaba `missing_alert: fungal_risk — no alert
open`.

| Fixture de B | Último día observado | ¿Abre `fungal_risk`? |
|---|---|---|
| antes de la regrabación de #335 | 87,0 % | sí |
| tras la regrabación de #335 | 84,0 % | **no** |
| ahora (este commit) | 90,0 % | sí |

- **Qué cambió** (`Refs #335`): B se regrabó en **la misma celda** (11,2 N /
  74,1 O — la propiedad de #335 no se toca) con la ventana del archivo
  desplazada al **2017-11-08**. El día que la regla lee queda en **90 %** y
  24,4 °C de media (dentro de 20-30 °C); 10 de los 14 días observados están
  sobre 85 % («humedad alta sostenida», `docs/06 §10`); el ET0 medio sigue en
  3,03 mm/día y la lluvia observada en 130,4 mm — el mismo carácter del
  fixture. El pronóstico tampoco cambia de carácter: 9,7 mm de lluvia máxima,
  muy por debajo del umbral de `heavy_rain_forecast` (> 50 mm), así que B sigue
  abriendo **solo** la alerta que su oráculo espera.
- **La regresión que faltaba**
  (`server/tests/simulator/test_scenario_weather.py`):
  `test_the_fixture_can_open_the_fungal_alert_its_scenario_expects` — el
  fixture cuyo escenario espera la alerta tiene un día que la abre, y los tres
  que no la esperan no tienen uno. RED verificado: con el fixture de la
  regrabación el test falla; con el de ahora, 5 de 5 en verde.
- **Verificación en vivo**: `rainy-season` verde (7,6 s) contra el stack del
  worktree (base 60621, api 8010, worker, ingestor, web 5175).

### U7 — #340 (el catálogo reproduce la aritmética FAO-56)

**El trabajo ya estaba hecho en `main`** por la decisión del owner (opción (b)):
las edades de siembra de los escenarios caen dentro de la etapa `mid` del
catálogo (`docs/06:427,467`), y el tripwire del piloto E ya estaba en
`verdict.passed === true` (commits `8b873f2` + `b3279bf`). El owner confirmó
que esa decisión manda sobre la opción (a) del brief.

- **Verificación en vivo** (stack del worktree: base 60621, api 8010, worker,
  ingestor, web 5175):

  ```
  ✓  [scenarios] › dry-spell-rainfed.spec.ts:47 › @pr dry-spell-rainfed:
     the full loop runs and the oracle passes (10.0s)
  ```

  El `expect(verdict.passed, ...)` del checker pasó, y con él la aserción de
  la recomendación `rainfed` con el consejo `conserve_moisture` y el
  `water_stress` que la aritmética de `docs/06 §10` promete.

- **Lo que falta**: cerrar el issue con esta evidencia (`Fixes #340`).

### U8 — #342 (el paso del outbox del escenario A es ganable)

El issue reportaba que `el-nino.spec.ts` fallaba 3/3 en el paso *the outbox
carries the critical page*: el SMS de escalación nunca llegaba. La causa raíz
estaba en la aritmética del ruido, no en el paso: el critical **nunca existía**
dentro de la ventana de 14 días (D-DH.14).

- **Diagnóstico** (forense sobre la base del worktree): las alertas salían en
  `warning`, los jobs en `succeeded` y las notificaciones en `push` — sin SMS.
  La serie de suelo con ruido se disparaba por encima de θ_estrés los días 9,
  10 y 11; `sustained_run` es estricto, así que la corrida de 6 h no se
  completaba hasta el día ≈ 12; el critical (48 h después) caía en el ≈ 14,9,
  fuera de la ventana.
- **Qué cambió**: el ruido de la rampa de suelo de A pasa de 0,8 a 0,1
  (`server/src/techcamp/simulator/scenarios/el-nino.yaml`, con el porqué
  escrito en el propio YAML). La apertura documentada (≈ 10,7) se conserva y
  el critical cae en el ≈ 13, dentro de la ventana.
- **Verificación en vivo** (stack del worktree: base 60621, api 8010, worker,
  ingestor, web 5175; `just scenario-clean` antes de cada corrida):

  ```
  ✓  [scenarios] › e2e/scenarios/el-nino.spec.ts:52 › @pr el-nino: water
     stress ends critical, the advice is to irrigate, the outbox carries the
     page (2.1m)
  1 passed (2.1m)
  ```

  Y el outbox, con el SMS que el paso espera:

  ```json
  [{"channel":"sms","status":"sent","rule_code":"water_stress",
    "severity":"critical","sent_at":"2026-10-26T01:57:08Z"}]
  ```

- **Lo que además se encontró**: el runner de backfill publicaba contra
  `http://localhost:8000` —el default del script— en vez del API del stack
  (8010), así que la primera corrida de la sesión murió con `ConnectError`
  antes de llegar al paso del outbox. El harness ahora le pasa `--api`
  (`web/e2e/scenarios/support/harness.ts`).

### U9 — #339 (higiene entre corridas)

Los cuatro hallazgos de la ronda, verificados uno por uno:

| Hallazgo | Estado verificado |
|---|---|
| El mensaje de timeout del harness decía `/healthz` | **Corregido**: el harness consulta `/health` (`harness.ts:116`) |
| El tripwire del piloto E | **Volteado**: `expect(verdict.passed).toBe(true)` (`dry-spell-rainfed.spec.ts:100`) |
| `ml/uv.lock` con revisión de lockfile vieja | **Consistente**: `uv lock --check` en `ml/` resuelve sin quejarse |
| El e2e deja su organización en la base | **Cubierto**: `just scenario-clean` existe, y ahora el README y `docs/06 §10` lo dicen |

- **Commit**: este commit (enmienda de `docs/06 §10`).

### U10 — #277 (la pantalla de riesgo climático existe)

La pantalla no existía: el backend de E10 estaba completo y probado
(`GET /plots/{plot_id}/risk` → `RiskPredictionView`), pero `web/src/features/`
no tenía ningún módulo de riesgo y el tipo generado ni siquiera estaba en
`web/src/lib/api/schema.d.ts`.

- **Qué cambió** (`web/src/features/risk/`, nuevo):
  - `api/usePlotRisk.ts` — la llamada tipada a `GET /plots/{plot_id}/risk`, con
    la query persistida (`meta: { persist: true }`): la pantalla abre sin
    conexión con la última respuesta que el teléfono vio.
  - `api/riskCopy.ts` — la traducción del contrato a palabras: el evento, la
    probabilidad, el horizonte, los `top_factors` con su unidad y **de dónde
    sale el número**.
  - `components/RiskCard.tsx` — la tarjeta de un evento.
  - `containers/RiskScreen.tsx` — la pantalla, con sus tres estados (cargando,
    error, "sin predicciones de riesgo" en palabras, no una pantalla vacía).
- **La decisión del owner (D-DH.12)**: la versión que sirve hoy es la línea
  base `climatology_month`, registrada con `thresholds={}`, así que su
  severidad es **siempre** `low` — no porque el riesgo sea bajo, sino porque no
  hay corte con el que comparar la probabilidad (D-T9.2). La tarjeta **no**
  muestra esa severidad: nombra el origen —"climatology_month v1 · línea base ·
  sin cortes calibrados"— en vez de un "Riesgo bajo" que nadie midió. Una
  versión calibrada sí muestra la suya.
- **Un evento que esta build no conoce se omite entero**: una tarjeta con el
  nombre inventado afirmaría un riesgo que el servidor nunca dijo.
- **Ruta**: `app/routes.tsx` declara `riesgo` con `RiesgoRoute`, y el estado de
  la parcela (Inicio) pone la fila "Predicciones de esta parcela" que la abre.
- **Pruebas**: 25 nuevas en tres archivos (`riskCopy.test.ts`, `RiskCard.test.tsx`,
  `RiskScreen.test.tsx`), más la ruta en `app/routes.test.tsx`.
- **Docs**: `docs/07` enmendado — el ítem 6 del Inicio y la sección "**Riesgo
  climático** (Inicio, E10; RF-12)" con las reglas de la tarjeta.
- **Por la skill `impeccable`**, como toda UI del repo.
- **Hueco declarado, no arreglado**: la base viva **no tiene** filas en
  `risk_prediction` ni una `model_version` registrada — el registro de la línea
  base `climatology_month` es trabajo de la lane de E10, fuera de este alcance.
  En la demo, la pantalla abre con "Sin predicciones de riesgo", que es la
  respuesta honesta; la tarjeta con datos se verifica por pruebas, no en vivo.

### U11 — #258 (`relative_yield` null, pero honesto)

El campo `relative_yield` ya estaba en el esquema, con su hueco declarado
(`nullable=True`, `CHECK (relative_yield is null or relative_yield >= 0)`), pero
**nadie lo leía**: la UI de métricas no lo mostraba (#258).

- **Lo que la UI hace ahora**: la sección "Ciclo actual" de la pantalla de
  Indicadores muestra el rendimiento relativo municipal. Cuando el valor es
  `null` muestra **`—`** y la razón en palabras ("Todavía no hay datos del
  municipio para comparar: el dato de campo municipal no existe aún.").
  **Nunca `0`**: un cero afirmaría que la parcela rinde cero frente a su
  municipio, que nadie midió (D-T0.3).
- **El habilitador del servidor (D-DH.13)**: el web no puede nombrar un ciclo
  —`ActiveCycleView` no lleva el id del ciclo y no hay listado de ciclos
  (D-T9.1)— así que el servidor resuelve el ciclo activo en la lectura:
  `GET /plots/{plot_id}/cycles/active/summary`. La ruta se declara **antes**
  de la que lleva un UUID de ciclo, para que `active` no se lea como un
  `crop_cycle_id`. Una parcela sin ciclo activo responde `404`
  (`NoActiveCropCycleError`), y la sección simplemente no aparece.
  **Desvío de lo que D-T9.1 nombraba**: D-T9.1 era una nota del lane de E11
  ("naming a cycle is a separate server task"); la ruta elegida es la que
  resuelve el ciclo activo, no expone el id en `ActiveCycleView`. Evita tocar
  el read model del módulo `home` y deja al web en una sola petición.
- **La épica del denominador (D-DH.11)**: `field_record` **no** se construye
  aquí. El trabajo grande —la tabla, la ingesta de EVA/AGROSAVIA y
  `mediana_EVA(cultivo, municipio, 3 años)`— queda como épica **E17** con su
  propio feature doc (`odd/tasks/techcamp-v2-field-record.md`, decisiones
  `D-FR.1` … `D-FR.8`) y su issue **#355**, y `docs/10` la nombra como
  dependencia de E11 que **no está en la ruta crítica de la demo**: la demo se
  entrega con `relative_yield` en `null`.
- **Decisión de producto que queda ABIERTA** (escrita, no resuelta): si
  `field_record` es tabla de referencia sin `org_id`, la regla de aislamiento de
  `docs/09 §Seguridad` no aplica igual que al resto de `metrics`. Hay que
  decidirlo **antes** de escribir la migración.
- **Pruebas**: 4 nuevas en `server/tests/metrics/test_metrics_read_api.py`
  (265 en verde en `tests/metrics/`) y 4 en la web para la sección
  (`RelativeYieldSection.test.tsx`; 53 en verde en las pruebas de métricas).

### U2 — #214 (el stack de seminario levanta y opera sin trucos)

Dos unidades: el servidor que responde la verdad, y la infraestructura que
levanta.

#### U2a — el servidor responde la verdad

- **Qué cambió**:
  - `POST /dev/jobs/irrigation:run` responde `status: "skipped"` con el motivo
    cuando el día de balance (D−1) no tiene clima observado, y **no** encola un
    job que no escribiría nada. Con clima, responde `status: "queued"` y el
    `job_id`.
  - `POST /plots/{id}/soil:autofill` responde con `warnings`: qué le falta al
    perfil para que el balance corra (la profundidad de raíz no sale de
    SoilGrids: es una decisión del cultivo).
  - `web/src/lib/api/schema.d.ts` regenerado contra el router (venía
    desactualizado: le faltaba la ruta de escenarios).
- **Pruebas**: `test_dev_route_says_skipped_when_the_balance_day_has_no_observed_weather`
  y `test_autofilling_soil_says_the_root_depth_is_missing`. `irrigation` +
  `farms`: 329 en verde.

#### U2b — la infraestructura levanta

- **Qué cambió**:
  - `minio-init` se construye desde el contexto local del server y crea los
    buckets con `boto3` (`server/src/techcamp/init_buckets.py`): la imagen
    `quay.io/minio/mc` deniega pulls anónimos y un init que no puede arrancar es
    un stack que no levanta. El init es *best-effort*: si el almacén no
    contesta, el stack levanta igual y el log lo dice; si el almacén contesta y
    la creación falla, es una mala configuración y para con código 1.
  - `just stack-rebuild` automatiza el `podman rm -f` que el presentador
    olvidaba tras reconstruir una imagen (el contenedor viejo se queda con la
    imagen vieja), y espera a que la API conteste `/health`.
  - El README trae el runbook de la demo: `uvx podman-compose` (el binario no
    está instalado en la máquina de la demo), la recreación de contenedores,
    la carga de escenarios, `just scenario-clean` entre corridas y la nota
    operativa del ingestor (matarlo por PID de python, no con `pkill -f`, que
    deja huérfano el hijo).
- **Verificado**: `podman-compose config` resuelve los nueve servicios con el
  `minio-init` construyendo desde el server. La imagen de MinIO no se puede
  descargar en este entorno (`quay.io` deniega todo el registro, también
  `docker.io/minio/mc` y `docker.io/minio/minio`), así que el arranque completo
  del stack con MinIO no se pudo correr aquí; el resto del stack sí.

