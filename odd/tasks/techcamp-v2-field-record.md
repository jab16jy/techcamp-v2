# TechCamp v2 — Referencia municipal EVA/AGROSAVIA (`field_record`)

> Feature doc de la épica **E17** ([docs/10-dag.md §1](../../docs/10-dag.md)): la tabla
> `field_record`, la ingesta de los datos EVA/AGROSAVIA, el método de `source_repository`
> para `mediana_EVA(cultivo, municipio, 3 años)` y el math de dominio que produce
> `relative_yield`.
>
> Regla de este doc: los docs mandan sobre el código (AGENTS.md regla 2). Cada
> decisión de diseño de abajo lleva su número `D-FR.n` y se aplica ANTES del
> código que la cumple.
>
> **Estado: nada de esto está implementado.** Esta unidad solo deja la épica
> escrita, con dueño, y su issue: **#355**. El origen es el follow-up explícito
> de D-T0.9 que E11 registrou en **#258**, y la decisión del demo-hardening que
> lo sacó de su alcance (**D-DH.11**, `odd/tasks/techcamp-v2-demo-hardening.md`).

## Objective

Que `relative_yield` deje de ser un hueco declarado y pase a ser la cifra que el
modelo de datos promete: el rendimiento del ciclo dividido por la mediana EVA
del cultivo en el municipio (3 años), **con la advertencia de que da contexto y
no mide impacto** ([docs/11:23](../../docs/11-metricas.md),
[docs/00:25](../../docs/00-glosario.md), ADR-0024).

## Problem

La tabla `field_record` no existe, pero el doc la da por hecha:

- [docs/03:389](../../docs/03-modelo-datos.md) la describe con `crop_id`,
  `municipality_code` (DANE), `year`, `period`, `area_sown_ha`,
  `area_harvested_ha`, `production_t`, `yield_t_ha` y `source` (`eva` o
  `agrosavia`).
- [docs/03:591](../../docs/03-modelo-datos.md) la pone como destino de migración de
  `datos_campo` de la v1.
- Ninguna épica de [docs/10](../../docs/10-dag.md) la nombra: hasta esta unidad el
  trabajo no tenía dueño.

El motivo de la ausencia es **D-T0.9** (`odd/tasks/techcamp-v2-e11-metrics.md`):
los datos EVA/AGROSAVIA de la v1 no se recuperaron. Sin la tabla no hay
`mediana_EVA(cultivo, municipio, 3 años)`, que es el denominador de la fórmula.

Mientras tanto el hueco ya está declarado en el código y en el esquema, y es
honesto:

| Hecho verificado | Dónde |
|---|---|
| `relative_yield: Mapped[decimal.Decimal \| None]`, `nullable=True` | `server/src/techcamp/metrics/adapters/orm.py:220` |
| El docstring dice "`relative_yield` null until `field_record` exists (D-T0.9)" | `server/src/techcamp/metrics/adapters/orm.py:143` |
| `CHECK (relative_yield is null or relative_yield >= 0)`: el esquema **invita** el hueco | `server/src/techcamp/metrics/adapters/orm.py:152` |
| El dominio escribe `relative_yield=None` | `server/src/techcamp/metrics/domain/cycle_summary.py:171` |
| La API lo expone como `float \| None` | `server/src/techcamp/metrics/adapters/api/router.py:214` |
| Ninguna migración crea `field_record`; ningún `.py` la usa más allá de docstrings y comentarios | `server/migrations/versions/`, `server/src/` |

## Why

- El hueco le quita al Productor una pregunta que sí hace: *"¿cómo estoy
  respecto a mi municipio?"* ([docs/11:43](../../docs/11-metricas.md)). El resto de
  las métricas de impacto ya están; esta es la que falta.
- La referencia municipal es **reutilizable**: la misma tabla alimenta la línea
  base de rendimiento municipal que [docs/08:28](../../docs/08-ml.md) le promete a
  la aptitud.
- Es trabajo que **ya está decidido que no se hace en E11** (D-T0.9) y que por
  eso necesita dueño propio, doc propio e issue propio: si no, se pierde (lo
  que pasó con `#258`, que quedó archivado como issue de seguimiento el día
  que se cerró E11).

## Scope

**Dentro:**

| Unidad | Trabajo | Superficie |
|---|---|---|
| F1 | Recuperar (o declarar perdido para siempre) el `datos_campo` de la v1 | búsqueda en la v1; decisión escrita |
| F2 | Migración que crea `field_record` | `server/migrations/versions/`, `server/src/techcamp/metrics/adapters/orm.py` |
| F3 | Ingesta de EVA/AGROSAVIA (normalización de nombres de cultivos al catálogo `crop`) | ingesta + `source_repository` |
| F4 | Método de `source_repository` para `mediana_EVA(cultivo, municipio, 3 años)` | `server/src/techcamp/metrics/adapters/source_repository.py` |
| F5 | Math de dominio del `relative_yield` (puro, TDD) | `server/src/techcamp/metrics/domain/cycle_summary.py` |
| F6 | Lectura: resumen de ciclo y su línea en la pantalla de Indicadores | `server/src/techcamp/metrics/adapters/api/router.py`, `web/src/features/metrics/` |

**Fuera (no se tocan):**

- El **índice de adopción digital**: `relative_yield` no participa. Sus cuatro
  componentes son `monitoring`, `record_keeping`, `decision` y `risk_management`
  ([docs/11 §2](../../docs/11-metricas.md)). E11 entrega sus cinco figuras de
  `OrgMetrics` igual con `relative_yield` en `null` (D-T7.1).
- **Medir impacto con EVA**. ADR-0024 (brecha G11) ya lo rechazó: EVA
  consolidada no es una muestra comparable porque los productores que adoptan se
  autoseleccionan. `relative_yield` da contexto, no efecto.
- **Entrenar o reentrenar modelos** con EVA (ADR-0011, docs/08): la aptitud
  sigue siendo un score diferido.
- La deuda ya cerrada de E11 (D-T7.1, D-T7.2, D-T7.3) y el follow-up de D-T9.1
  (exponer el `crop_cycle_id` que `ActiveCycleView` no expone), que es **task
  de servidor aparte más un cambio en docs/04** y es dependencia del paso de
  lectura, no parte de esta épica.
- La tabla `municipality` de [docs/03:381](../../docs/03-modelo-datos.md), que
  tampoco existe en el código: la épica **no la crea** (ver D-FR.6).

## Decisiones — `D-FR.1` … `D-FR.8`

### `D-FR.1` — La épica no bloquea la demo del seminario

La ruta crítica de la demo es E0 → E2 → E3 → E4 → E6 → E9 → E15
([docs/10 §1](../../docs/10-dag.md)). E17 cuelga de E11 y **no** está en esa ruta:
la demo se entrega con `relative_yield` en `null`, y el demo-hardening se
compromete a que la UI muestre ese hueco como hueco (D-DH.11, unidad U11 de
#258, pendiente en esa cadena de PRs).

Razón: el valor de la cifra depende de datos externos que hoy no existen
(D-T0.9). Bloquear la demo por eso sería dejar el producto sin mostrar por un
problema de datos, no de producto.

### `D-FR.2` — `null` y nunca `0`

`null` es ausencia de evidencia; `0` es una afirmación sobre el mundo
(D-T0.3, [docs/11 §2](../../docs/11-metricas.md)). Un `0` en `relative_yield` se
leería como "esta parcela rindió cero frente a la referencia municipal" —un
fracaso del productor— cuando lo que pasó es que nunca se importó el
denominador. Con `null` el consumidor distingue "no lo sé" de "lo sé y es malo".

Razón: es la misma regla que ya decidió #243 (el denominador cero del cambio
frente a la encuesta) y que el repo escribe en todos los docs de métricas.

### `D-FR.3` — La pregunta del `org_id` queda ABIERTA y bloquea la migración

`field_record` es **tabla de referencia, no dato de inquilino**: lleva
`municipality_code` y no lleva `org_id`. La regla de aislamiento de
[docs/09 §Seguridad](../../docs/09-cuellos-de-botella.md) ("filtro obligatorio por
`org_id` en cada repositorio") no aplica igual que al resto del módulo
`metrics`.

**Este doc no cierra esa pregunta.** La respuesta es del dueño del producto y
tiene que estar escrita **antes** de escribir la migración, no después (es el
orden que pide #258). Ver §"Decisión de producto abierta (owner)".

La recomendación técnica de este doc es **no** llevar `org_id`, por estas
razones: la referencia municipal es pública y la misma para todos; si llevara
`org_id` dejaría de ser una referencia y cada organización tendría la suya
—que no se puede comparar con nada y no es la mediana EVA del municipio que
promete [docs/11:23](../../docs/11-metricas.md).

### `D-FR.4` — El orden de trabajo es fijo

1. **Recuperar (o declarar perdido para siempre) el `datos_campo` de la v1.**
2. **Migración** que crea `field_record`.
3. **Método de `source_repository`** para `mediana_EVA(cultivo, municipio, 3 años)`.
4. **Math de dominio.**
5. **Lectura.**

Razón: el paso 1 puede terminar en "perdido para siempre", y eso **no invalida**
los pasos 2 a 5: la tabla se crea y queda vacía, la mediana devuelve `null`
cuando no hay evidencia, y `relative_yield` sigue en `null` — sin inventar
nada. Al revés no funciona: escribir la migración antes de saber si hay datos
deja la épica sin poder decidir su alcance.

### `D-FR.5` — El texto de la lectura declara que es contexto, no impacto

Cuando `relative_yield` se muestre, la lectura va acompañada de la advertencia de
que EVA consolidada no es una muestra comparable (ADR-0024, brecha G11) y de que
el efecto de la tecnología se mide contra la encuesta de inscripción
([docs/11 §43](../../docs/11-metricas.md)), no contra la media municipal.

Razón: un número sin esa advertencia es exactamente la lectura que ADR-0024
rechazó. El campo sirve para ubicar la parcela frente al municipio; cualquier
uso como "efecto de TechCamp" es un error de lectura que el producto no debe
permitir.

### `D-FR.6` — La épica no crea la tabla `municipality`

[docs/03:381](../../docs/03-modelo-datos.md) promete `municipality` (código,
nombre, departamento, `centroid`, `boundary`) y el código **no la tiene**
(`server/src/techcamp/farms/domain/models.py:75-76`: "`municipality_code` es
DIVIPOLA como texto plano (no existe aún la tabla `municipality`)"). Crear esa
tabla es otra épica; esta no la absorbe.

Razón: son dos promesas distintas del mismo doc y meterlas en una épica la
vuelve más grande y menos legible. Lo que sí hace esta épica es **no decidir en
silencio** con qué código se empareja la mediana (pregunta abierta abajo).

### `D-FR.7` — Los criterios de la mediana se escriben antes de codificarlos

La fórmula está escrita ([docs/11:23](../../docs/11-metricas.md),
[docs/00:25](../../docs/00-glosario.md)): `rendimiento /
mediana_EVA(cultivo, municipio, 3 años)`. Los **criterios** de la mediana no
están escritos en ningún doc: qué años entran en la ventana, qué pasa si hay
menos de tres años de evidencia, qué pasa si la mediana da 0, si se mezclan
fuentes `eva` y `agrosavia`.

Se fijan en el paso de math de dominio (F5) con TDD, y **docs/11 se actualiza en
la misma unidad de trabajo** (AGENTS.md regla 3: un cambio que necesita otro
diseño actualiza el doc o agrega un ADR primero, en la misma unidad que el
código).

Razón: inventar el criterio en el código y dejarlo fuera del doc es
exactamente el modo de fallo que el repo quiere evitar.

### `D-FR.8` — La ingesta es un proceso aparte de la migración

La migración crea la tabla y nada más. Los datos entran por un proceso de
ingesta que **normaliza los nombres de cultivos al catálogo `crop`**
([docs/03:591](../../docs/03-modelo-datos.md): "Normalizar nombres de cultivos al
catálogo `crop`"), porque EVA y AGROSAVIA traen el nombre del cultivo como
texto y el catálogo tiene `crop.code` y `crop.name_es`.

Razón: una migración que además trae datos de fuera no es una migración, es
una ingesta disfrazada, y no se puede repetir ni auditar. La reproducibilidad
de la fuente (qué archivo, qué fecha, qué licencia) es de la ingesta.

## Orden de trabajo (detalle)

| # | Paso | Termina cuando |
|---|---|---|
| 1 | Recuperar el `datos_campo` de la v1, o escribir que se perdió para siempre | Hay una decisión escrita, no un supuesto |
| 2 | Migración que crea `field_record` | Una cabeza Alembic; `up`/`down` probados |
| 3 | Ingesta EVA/AGROSAVIA + método `source_repository` de `mediana_EVA` | La mediana sale de datos reales de prueba, con su criterio escrito en docs/11 |
| 4 | Math de dominio | El dominio divide el rendimiento por la mediana; `null` si no hay evidencia, nunca `0` |
| 5 | Lectura | El resumen de ciclo y la pantalla de Indicadores muestran la cifra con su advertencia |

## Decisión de producto abierta (owner)

**Antes de escribir la migración (F2) hay que responder:**

> ¿`field_record` es tabla de referencia pública —sin `org_id`, con la regla de
> aislamiento de docs/09 exceptuada explícitamente para esta tabla— o lleva
> `org_id` y se convierte en dato de inquilino, atado a una organización?

La respuesta escribe el CHECK, los índices y la regla de `source_repository`. La
recomendación técnica de este doc es la primera (D-FR.3), porque la segunda
deja de ser una referencia municipal —pero la decisión es del dueño del
producto, no del que implemente.

## Acceptance criteria (de la épica, no de esta unidad)

- La migración crea `field_record` con las columnas de [docs/03:389](../../docs/03-modelo-datos.md)
  y una sola cabeza Alembic.
- `source_repository` expone `mediana_EVA(cultivo, municipio, 3 años)` con los
  criterios de D-FR.7 escritos en docs/11.
- El dominio calcula `relative_yield` como `rendimiento / mediana_EVA`, con
  `null` (nunca `0`) cuando no hay denominador ni evidencia.
- La lectura muestra la cifra **con la advertencia de contexto, no impacto**, y
  `—` cuando es `null`.
- El índice de adopción digital no cambia: sus cuatro componentes siguen siendo
  los de [docs/11 §2](../../docs/11-metricas.md).

## Checks

- TDD: RED → GREEN → REFACTOR, en cada paso.
- `just gate <paths>` con los tests nombrados, por paso.
- `just gate-lane` antes de cortar la cadena de PRs; `just gate-release` antes
  de cada PR (regla pre-PR #234); `just gate-full` al cerrar la épica.
- Doc de la decisión de producto escrito **antes** de la migración.

## Review (RDD)

> No aplica todavía: la épica no ha empezado. Cuando arranque, cada unidad se
> revisa con el ciclo RDD del repo.

## Open questions (owner)

- **¿`field_record` lleva `org_id` o no?** (D-FR.3) — es la que bloquea F2.
- **¿Con qué espacio de códigos se empareja la mediana?** `field_record.municipality_code`
  es DANE ([docs/03:389](../../docs/03-modelo-datos.md)), pero el único código de
  municipio que existe en el código es `farm.municipality_code`, texto libre
  DIVIPOLA (`server/src/techcamp/farms/domain/models.py:75-76`), y la tabla
  `municipality` que promete [docs/03:381](../../docs/03-modelo-datos.md) no existe.
  Sin resolver esto, la mediana no se puede emparejar con la parcela.
- **¿`crop_id` y `municipality_code` son claves foráneas?** [docs/03:389](../../docs/03-modelo-datos.md)
  lista `field_record` entre las "tablas derivadas (sin relaciones propias)" y al
  mismo tiempo le da dos columnas que apuntan a otras tablas.
- **¿Dónde vive el `datos_campo` de la v1?** [docs/08:28](../../docs/08-ml.md) lista
  EVA (26.402 filas) y suelos AGROSAVIA (13.762) entre lo que se reutiliza de la
  v1, mientras D-T0.9 dice que los datos EVA/AGROSAVIA de la v1 no se
  recuperaron. Los dos docs no pueden ser ciertos a la vez: el paso 1 de esta
  épica es resolverlo y dejar por escrito cuál era.

## Progress / evidence

- Sin avance: la épica no ha empezado. Esta unidad deja el feature doc, la fila
  en [docs/10 §1](../../docs/10-dag.md) y el issue **#355**
  (`type:feature`, `area:server`, `epic:e11`).

## Referencias

- [docs/03:389](../../docs/03-modelo-datos.md) — la tabla `field_record` que el doc
  promete; [docs/03:591](../../docs/03-modelo-datos.md) — su destino de migración.
- [docs/11:23](../../docs/11-metricas.md) — la fórmula; [docs/11:77](../../docs/11-metricas.md) —
  por qué hoy es `null`; [docs/00:25](../../docs/00-glosario.md) — el término.
- [ADR-0024](../../docs/adr/0024-metricas-de-impacto-y-adopcion-digital.md) — contexto,
  no impacto.
- [docs/09 §Seguridad](../../docs/09-cuellos-de-botella.md) — el aislamiento por `org_id`.
- [docs/10 §1](../../docs/10-dag.md) — la épica E17 y su dependencia de E11.
- `odd/tasks/techcamp-v2-e11-metrics.md` — D-T0.3, D-T0.9, D-T7.1, D-T9.1.
- `odd/tasks/techcamp-v2-demo-hardening.md` — D-DH.11.
- #258 (el follow-up de D-T0.9), #355 (esta épica).
