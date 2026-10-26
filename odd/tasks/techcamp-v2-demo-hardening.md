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

## Decisions — `D-DH.1` … `D-DH.12`

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

### `D-DH.11` — `relative_yield` null se comunica como hueco (U11, #258)

La UI de métricas muestra `—` (y la razón D-T0.9) cuando `relative_yield` es
`null`. **No** se construye `field_record` ni se ingiere EVA/AGROSAVIA aquí: ese
trabajo completo se registra como épica con dueño en `docs/10` y como feature
doc / issue propios.

Razón: el hueco está declarado y es honesto; inventar un valor no lo es. El
trabajo grande (mediana EVA) es escala épica y necesita su propio doc y su
propio dueño.

### `D-DH.12` — La pantalla de riesgo declara su origen (U10, #277)

La tarjeta de riesgo nombra la línea base (`model_version.is_baseline`) y
distingue "severidad baja" de "sin cortes calibrados": con umbrales vacíos la
severidad **no** se muestra como `bajo`, sino como el estado que el modelo sí
puede afirmar.

Razón (la misma que da el issue): el riesgo que se sirve hoy es la línea base
`climatology_month`, sin umbrales calibrados, así que **toda** predicción sale
en `low`. Mostrar "bajo" sin decir de dónde viene es la trampa que el issue
describe: dos tarjetas en verde que el lector entiende como "no hay riesgo".

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
- **U8 (#342)**: `el-nino.spec.ts` verde 3/3 con el paso del outbox (SMS de
  escalación en `/dev/outbox`), en dos stacks limpios.
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

