# 04 — Diseño de API

El sistema tiene tres contratos:

| Contrato | Quién lo usa | Estilo |
|---|---|---|
| **API HTTP** | PWA | REST JSON en `/api/v1`, descrita con OpenAPI; el cliente TypeScript se genera desde el esquema |
| **Stream** | PWA | Server-Sent Events en `/api/v1/stream` ([ADR-0015](adr/0015-tiempo-real-sse.md)) |
| **MQTT** | Nodos IoT | Tópicos `tc/v1/{node_id}/…` ([ADR-0004](adr/0004-mqtt-y-lorawan.md)) |

## Convenciones HTTP

| Tema | Regla |
|---|---|
| Autenticación | `Authorization: Bearer <JWT>` emitido por el proveedor de auth ([ADR-0014](adr/0014-autenticacion.md)). El backend valida la firma con JWKS y resuelve la membresía y el rol desde su propia base. |
| Autorización | Cada recurso se resuelve dentro de las organizaciones del usuario. Un recurso de otra organización responde `404`, no `403`, para no revelar que existe. |
| Errores | `application/problem+json` (RFC 9457): `type`, `title`, `status`, `detail`, `errors[]`. |
| Paginación | Por cursor: `?limit=50&cursor=<opaco>` → `{ "items": [], "next_cursor": "…" }`. |
| Idempotencia | Todo `POST` que crea algo acepta `Idempotency-Key`; la sincronización usa además los UUID del cliente. |
| Tiempo | ISO 8601 en UTC, siempre con offset (`Z` u offset explícito). Los rangos son `from` inclusivo y `to` exclusivo; un valor sin offset responde `422`. |
| Unidades | En el nombre del campo: `depth_mm`, `temp_c`, `area_ha`. |
| Límites de uso | 120 solicitudes/min por usuario; asistente 10 preguntas/día por usuario; respuesta `429` con `Retry-After`. |
| Versionado | Por ruta (`/v1`). Solo se agregan campos; cualquier cambio incompatible va a `/v2`. |

## Endpoints

Se muestran como firmas: el esquema completo vive en OpenAPI cuando exista el código.

### Identidad y organización

```
GET    /me                                   → User & { memberships: Membership[] }
POST   /me/consent                           { version } → 204
GET    /organizations/{org_id}/members        → Membership[]
POST   /organizations/{org_id}/invitations    { phone|email, role } → Invitation
DELETE /me                                   → 202   # borrado de datos personales (Ley 1581)
GET    /me/export                             → 202 { job_id }
```

### Fincas, parcelas y ciclos

```
GET    /farms?org_id=                          → Page<Farm>
POST   /farms                                  { org_id, name, municipality_code, location, technician_id? } → Farm
PATCH  /farms/{farm_id}                        { name?, technician_id? } → Farm
GET    /farms/{farm_id}/plots                  → Plot[]
POST   /farms/{farm_id}/plots                  { name, boundary: GeoJSON Polygon, irrigation_system: none|drip|sprinkler|gravity, irrigation_efficiency?, system_flow_lph? } → Plot   # none = secano
PATCH  /plots/{plot_id}                        { name?, boundary?, irrigation_system?, irrigation_efficiency?, system_flow_lph? } → Plot
PUT    /plots/{plot_id}/soil                   SoilProfile → SoilProfile
POST   /plots/{plot_id}/soil:autofill          → SoilProfile   # SoilGrids
GET    /plots/{plot_id}/baseline               → PlotBaseline
PUT    /plots/{plot_id}/baseline               { enrolled_on, crop_id, last_yield_kg_ha, last_cost_cop_ha?, irrigation_practice } → PlotBaseline   # encuesta de inscripción
GET    /crops                                  → Crop[] (con etapas, Kc y kc_source)
POST   /plots/{plot_id}/cycles                 { crop_id, sown_on } → CropCycle
PATCH  /cycles/{cycle_id}                      { status?, expected_harvest_on? } → CropCycle
```

### Estado de la parcela (pantalla principal)

Una sola llamada que arma todo lo que el productor ve al abrir la app. Así se evitan varias solicitudes seguidas en 3G.

```
GET /plots/{plot_id}/status → {
  plot, active_cycle: { crop, stage, day_of_cycle } | null,
  latest: { soil_moisture_pct, air_temp_c, air_rh_pct, at },
  water_balance: { depletion_mm, taw_mm, raw_mm, stress_moisture_pct, status: "ok|watch|irrigate|stress" } | null,
  recommendation: { kind, depth_mm?, duration_min?, advice[]?, rationale } | null,
  open_alerts: Alert[],
  weather_next_3d: WeatherDay[],
  nodes: NodeHealth[],
  digital_adoption_index: { value, month } | null
}
```

En una parcela de secano el `status` nunca es `irrigate`: cuando `Dr > RAW` es `stress`, y `recommendation.kind = rainfed` trae `advice[]` sin `depth_mm` ni `duration_min` ([ADR-0023](adr/0023-parcelas-con-riego-y-secano.md)).

La parcela pasa por el mismo control de acceso que el resto de `/plots/{plot_id}`: `404` si no existe o es de otra organización. Un dato que falta llega como `null`, nunca como un cero:

| Campo | Regla |
|---|---|
| `plot` | Resumen de la parcela: `{ id, org_id, farm_id, name, area_ha, irrigation_system }`. Sin polígono: la pantalla de inicio no lo dibuja y la respuesta se mantiene pequeña para 3G; el polígono está en `GET /farms/{farm_id}/plots`. |
| `active_cycle` | `null` sin ciclo activo. `stage` es la clave de la etapa (`initial`, `development`, `mid`, `late`), o `null` si el cultivo no tiene etapas de Kc (`kc_source = none`). `day_of_cycle` es el día del ciclo hoy (`America/Bogota`), con la fecha de siembra como día 1. Si la siembra es posterior a hoy, `day_of_cycle` y `stage` son `null`: todavía no hay día del ciclo. La interfaz traduce la clave. |
| `latest` | La humedad de suelo sale del sensor representativo de la parcela ([06 §5](06-diseno-detallado.md#5-riego-balance-hídrico-fao-56): uno cerca de Zr/2 o el promedio de dos en la zona de raíces); sin él, de la lectura más reciente de cualquier profundidad. Por métrica, la lectura válida más reciente de la parcela en las últimas 24 h (válida: calibrada, `value` no nulo, y sin el bit de fuera de rango en `quality` (valores 2 y 3, [03](03-modelo-datos.md#diagrama-entidad-relación))); sin lectura, esa métrica es `null`. `at` es la hora del valor más reciente devuelto, o `null` si todos son `null`. |
| `water_balance` | El último balance diario consolidado (el de ayer); `null` si no hay. |
| `recommendation` | La de hoy, con el mismo objeto `rationale` que guarda el cálculo (incluye `forecast_rain_7d_mm`, la lluvia esperada que muestra la tarjeta de secano); la interfaz arma el porqué a partir de él. `null` si no se ha calculado. |
| `open_alerts` | Alertas de la parcela con `state <> 'resolved'` (abiertas y reconocidas), las `critical` primero y luego de la más reciente a la más antigua. |
| `weather_next_3d` | Las filas de pronóstico de hoy y los dos días siguientes, con su marca `stale`. |
| `nodes` | Un `NodeHealth` por nodo de la parcela: `{ node_id, status, last_seen_at, completeness_24h }`. La batería y el RSSI quedan fuera hasta que un nodo los reporte. |
| `digital_adoption_index` | `null` hasta que E11 calcule el índice ([11-metricas](11-metricas.md)). |

La composición vive en el módulo `home` ([05 §Módulos](05-arquitectura.md#módulos-c4-nivel-3), D-T0.1).

### Nodos y sensores

```
POST   /nodes:claim                           { claim_code, plot_id } → Node & { mqtt: { username, password } }  # la contraseña se muestra una sola vez
GET    /nodes?plot_id=&status=                → Page<Node>
PATCH  /nodes/{node_id}                       { plot_id?, status? } → Node
POST   /nodes/{node_id}/credentials:rotate    → { password }
GET    /nodes/{node_id}/health                → { last_seen_at, battery_v, rssi, completeness_24h }   # completeness_24h es una razón 0–1, no un porcentaje
GET    /nodes/{node_id}/sensors               → Sensor[]
POST   /sensors/{sensor_id}/calibrations      { method, kind: lab|field, params, rmse_pct?, valid_from } → Calibration   # crea una versión nueva; 409 `calibration_version_conflict` si otra petición ganó la carrera de `version`, y el cliente reintenta
```

### Lecturas y clima

```
GET /plots/{plot_id}/readings?metric=soil_moisture&from=&to=&resolution=raw|hour|day
    → { series: [{ sensor_id, depth_cm, points: [[t, value], …] }] }
GET /plots/{plot_id}/weather?days=7           → WeatherDay[]   # pronóstico + últimos observados
```

La resolución se elige según el rango: `raw` hasta 2 días, `hour` hasta 60 días y `day` para más. Si se pide `raw` con un rango mayor, el servidor responde `422`, para proteger la base.

`metric` es una de las variables del [glosario](00-glosario.md) (`soil_moisture`, `soil_temp`, `air_temp`, `air_rh`, `rain`, `water_flow`, `battery_v`, `rssi`); una métrica desconocida responde `422` y no una serie vacía, que sería indistinguible de una parcela sin sensores de esa variable. La consulta recorre hasta 500 nodos de la parcela, por encima de lo que la escala de [02-estimaciones](02-estimaciones.md) necesita.

`GET /plots/{plot_id}/weather` acepta `days` entre 1 y 16 (por defecto 7; fuera de ese rango responde `422`). La respuesta entrega los últimos `days` registros observados (`is_forecast=false`, día en `[today-days, today-1]`) seguidos de las filas de pronóstico (`is_forecast=true`, día en `[today, today+days-1]`), ordenados por día y observado antes de pronóstico; `today` es la fecha en `America/Bogota`. Cada elemento `WeatherDay` tiene la forma:
`{ day: string, is_forecast: bool, et0_mm: float | null, rain_mm: float | null, tmin_c: float | null, tmax_c: float | null, rh_mean_pct: float | null, fetched_at: string, stale: bool }`.
El indicador `stale` evalúa la degradación de la celda completa contra el instante de consulta (`is_stale(max(fetched_at), now)`): es `true` si la captura más reciente supera las 6 h (dos refrescos de 3 h perdidos) y `false` en caso contrario, repetido por elemento. Una parcela sin celda asignada o cuya celda no tiene filas almacenadas responde `[]`.

### Riego

```
GET  /plots/{plot_id}/irrigation/recommendation?day=  → IrrigationRecommendation
GET  /plots/{plot_id}/water-balance?from=&to=         → WaterBalanceDay[]
```

`GET /plots/{plot_id}/irrigation/recommendation` acepta `day` (por defecto la fecha de hoy en `America/Bogota`). Si la parcela no existe o pertenece a otra organización, responde `404` ("Plot not found"). Si para el día consultado no hay recomendación calculada y guardada, responde `404` con el título "Recommendation not found". La respuesta entrega un objeto `IrrigationRecommendation` con la forma:
`{ plot_id: string, day: string, kind: string, depth_mm: float | null, duration_min: int | null, advice: string[], rationale: object }`.
`kind` toma uno de los valores del árbol de decisión (`irrigate`, `postpone`, `not_needed`, `no_kc`, `rainfed`). En parcelas de secano (`kind = rainfed`), `depth_mm` y `duration_min` son `null`, y `advice` entrega la lista ordenada de códigos agronómicos aplicables (`delay_sowing`, `rain_expected`, `conserve_moisture`, `prioritize_harvest`, `no_action`). `rationale` contiene el objeto JSON almacenado con los insumos numéricos y banderas del cálculo (`et0_mm`, `kc`, `kc_source`, `kc_approximate`, `p`, `raw_mm`, `taw_mm`, `depletion_model_mm`, `depletion_mm`, `k`, `without_sensor`, `forecast_rain_48h_mm`, `low_confidence`, `forecast_missing`, etc.); `/status` (E9) entrega este mismo objeto, y la interfaz lo presenta.

`GET /plots/{plot_id}/water-balance` acepta `from` y `to` (fechas en formato `YYYY-MM-DD`). Por defecto, `to` es el día de ayer en `America/Bogota` (último balance diario consolidado) y `from` es `to − 29 días` (rango de 30 días). Si `from > to` o el rango supera los 366 días contando ambos extremos, responde `422`. Si la parcela no existe o pertenece a otra organización, responde `404` ("Plot not found"). Retorna `WaterBalanceDay[]` ordenado por día ascendente (o `[]` si no hay filas en el rango). Cada elemento tiene la forma:
`{ day: string, etc_mm: float, effective_rain_mm: float, irrigation_mm: float, taw_mm: float, raw_mm: float, depletion_model_mm: float, depletion_mm: float, soil_moisture_obs_pct: float | null, assimilation_k: float, stress_moisture_pct: float, status: string }`.
El campo `status` se deriva de `compute_water_balance_status` (`ok`, `watch`, `irrigate`, `stress`). En parcelas de secano (`irrigation_system = none`), el estado nunca es `irrigate`: cuando `Dr > RAW`, reporta `stress`. En el límite `Dr = RAW` sigue en `watch`, porque el estrés hídrico empieza *por encima* de RAW ([ADR-0022](adr/0022-estres-hidrico-y-asimilacion.md)). Una parcela con sistema de riego, en cambio, reporta `irrigate` desde `Dr ≥ RAW` ([06 §5](06-diseno-detallado.md#5-riego-balance-hídrico-fao-56)). Con `RAW ≤ 0` (suelo degenerado) el estado siempre es `ok`, sin importar `Dr` ni el tipo de parcela.


### Alertas y notificaciones

```
GET    /alerts?org_id=&plot_id=&state=open     → Page<Alert>
POST   /alerts/{alert_id}:acknowledge          → Alert
POST   /alerts/{alert_id}:resolve              { note? } → Alert
GET    /alert-rules?org_id=                    → AlertRule[]
POST   /alert-rules                            AlertRule → AlertRule
PATCH  /alert-rules/{rule_id}                  → AlertRule
POST   /push-subscriptions                     { endpoint, keys } → 201
DELETE /push-subscriptions/{id}                → 204
```

`GET /alerts` exige `org_id` y lista solo esa organización (la membresía primero: un no miembro recibe `404`, no una lista vacía), con página por cursor y `next_cursor` solo cuando la página vino llena. Un elemento `Alert` tiene la forma:
`{ id: string, org_id: string, rule_id: string, rule_code: string, plot_id: string | null, node_id: string | null, state: "open|acknowledged|resolved", severity: "info|warning|critical", evidence: object, opened_at: string, acknowledged_at: string | null, resolved_at: string | null, escalated_at: string | null, resolution_note: string | null }`.

`POST /alerts/{alert_id}:acknowledge` y `POST /alerts/{alert_id}:resolve` devuelven el `Alert` actualizado; el cuerpo del `resolve` es opcional (`{ "note": "…" }`). Errores: `404` ("Alert not found") si la alerta no existe o es de otra organización, `403` si quien llama es `viewer`, `409` si la transición no aplica (resolver solo desde `acknowledged`, [docs/06 §3](06-diseno-detallado.md#3-alertas)) o si otra escritura ganó la carrera del estado.

Un elemento `AlertRule` tiene la forma:
`{ id: string, org_id: string | null, code: string, metric: string | null, operator: "<" | ">" | null, threshold: float | null, hysteresis: float, min_duration_min: int, severity: "info|warning|critical", crop_id: int | null }`.
`GET /alert-rules?org_id=` devuelve las reglas de fábrica (`org_id = null`, las del sistema) más las de esa organización, a cualquier miembro, incluido `viewer`; un no miembro recibe `404`. `POST /alert-rules` exige rol `owner` de esa organización (`403` en cualquier otro rol, `404` si no es miembro) y `threshold` es obligatorio: solo la regla de fábrica `water_stress` no lo tiene. `PATCH /alert-rules/{rule_id}` cambia `threshold`, `hysteresis`, `min_duration_min` y `severity` de una regla propia (`owner`); una regla de fábrica o de otra organización responde `404` ("Alert rule not found"), y un `null` explícito responde `422` (las reglas de la organización no admiten umbrales nulos).

El service worker del navegador se registra contra estos endpoints porque `PushManager.subscribe()` exige una `applicationServerKey`: la clave pública VAPID llega al cliente web **en tiempo de compilación**, por la variable `VITE_VAPID_PUBLIC_KEY` (expuesta por Vite al bundle del navegador), y debe coincidir con el par de claves VAPID del servidor que lee de su configuración (T7b). No es un endpoint: la clave es pública por diseño y no hay nada que proteger, así que no se agrega una ruta solo para entregarla.

`POST /push-subscriptions` registra la suscripción del service worker del navegador (`keys` trae `p256dh` y `auth`) y responde `201 { id }`. Como `endpoint` es único ([docs/03](03-modelo-datos.md)), registrar de nuevo el mismo `endpoint` actualiza esa misma fila: la vuelve a ligar a quien la registra y reemplaza sus `keys` (un navegador con claves nuevas no falla ni duplica). `DELETE /push-subscriptions/{id}` elimina solo la fila de quien la creó y responde `204`; una suscripción de otro usuario o inexistente responde `404`.

### Bitácora (sincronización offline)

```
POST /sync/push {
  device_id,
  changes: [{ id, entity: "logbook_entry|extension_visit", op: "upsert|delete", data, client_updated_at }]
} → { results: [{ id, status: "applied|duplicate|conflict_overwritten|rejected", server_version, error? }] }

GET  /sync/pull?since=<server_version>&limit=500
  → { changes: [{ id, entity, op, data, server_version }], next_since, has_more }

POST /attachments:presign  { logbook_entry_id | extension_visit_id, content_type, bytes } → { upload_url, object_key }
```

`data` lleva los campos de `logbook_entry` según su `kind`, incluidos `sold_kg`, `sale_price_cop_per_kg`, `labor_days` y `alert_id` ([03](03-modelo-datos.md#logbook_entry-la-tabla-que-se-sincroniza-offline)).

**Push.** Un lote trae como máximo 100 cambios; con más responde `422`. Cada cambio se aplica por separado: un `rejected` no anula el resto del lote. Resultado por cambio, con el registro guardado leído bajo bloqueo ([06 §7](06-diseno-detallado.md#7-sincronización-offline-de-la-bitácora)):

| Situación | `status` |
|---|---|
| El `id` no existe | `applied` |
| Mismo `id` y mismo `client_updated_at` | `duplicate`, sin escribir |
| `client_updated_at` más reciente que el guardado | `applied` |
| `client_updated_at` más antiguo que el guardado | `conflict_overwritten`, sin escribir; `server_version` es el del registro guardado |

`rejected` lleva un `error` estable que la interfaz traduce a su texto:

| `error` | Cuándo |
|---|---|
| `clock_skew` | `client_updated_at` más de 24 h en el futuro |
| `invalid` | Falla una regla de campos (el `CHECK` por `kind`, `sold_kg > yield_kg`, un tema fuera de los cinco) |
| `not_found` | La parcela, la finca, el ciclo, la alerta o el `id` no son visibles para quien sincroniza: un `id` de otra organización o de la otra entidad responde igual, para no revelar que existe ([09](09-cuellos-de-botella.md#seguridad)) |
| `alert_plot_mismatch` | `alert_id` es de otra parcela |
| `forbidden` | El rol no puede escribir esa entidad |

**Borrado.** Un cambio `op: "delete"` lleva en `data` el registro tal como se guardó por última vez, sin `deleted_at`: el servidor pone el `deleted_at` y no valida los campos (una entrada rechazada por `invalid` se puede borrar). Si el registro existe, la última escritura decide igual que en un `upsert` y, si gana, se guarda `deleted_at`, el `client_updated_at` y un `server_version` nuevo. Si el servidor nunca lo recibió (se creó y se borró sin conexión), responde `applied` sin escribir nada y con `server_version: null`: nadie más lo tiene. Los borrados son definitivos: no hay forma de restaurar una entrada, y un `upsert` sobre un `id` borrado responde `rejected` (`not_found`).

**Roles.** `logbook_entry`: `owner`, `technician` y `producer`; `viewer` recibe `forbidden`. `extension_visit`: solo `technician`, y `technician_id` debe ser quien sincroniza.

**Pull.** Devuelve los `logbook_entry` y `extension_visit` de todas las organizaciones de quien sincroniza, mezclados por `server_version` ascendente. Un registro con `deleted_at` llega como `op: "delete"`. Las dos tablas comparten la secuencia, así que un solo cursor las cubre.

**Fotos.** El cliente sube la foto directo al almacenamiento de objetos con la URL prefirmada. La API nunca recibe los bytes ([ADR-0018](adr/0018-almacenamiento-de-objetos.md)). `POST /attachments:presign` crea la fila de `attachment` (`object_key`, `content_type`, `bytes`) y responde la URL: no hay paso de confirmación, y una subida que nunca llega queda huérfana para el job de limpieza. Exige `bytes ≤ 200 KB` y `content_type` `image/jpeg` o `image/webp` (`422` si no), y que la entrada o visita ya esté sincronizada y sea visible para quien la pide (`404` si no).

### Visitas de extensión y bandeja del técnico

Las visitas se crean y editan con `/sync/push` (`entity: "extension_visit"`), porque el técnico las registra en el campo sin conexión.

```
GET  /me/tray                                  → [{ farm, open_alerts: Alert[], last_visit_on }]   # fincas asignadas al técnico
GET  /farms/{farm_id}/visits                   → Page<ExtensionVisit>
GET  /organizations/{org_id}/visits?from=&to=  → Page<ExtensionVisit>   # exportación de visitas por organización
```

`GET /me/tray` devuelve las fincas cuyo `technician_id` es quien llama, en todas sus organizaciones; a cualquier otro usuario le responde `[]` (una bandeja vacía no revela nada, así que no hay `403`). `open_alerts` son las alertas con `state <> 'resolved'` de las parcelas de la finca, con el mismo orden que en `/status`. `farm` es un resumen de la finca: `{ id, org_id, name, municipality_code }`, sin ubicación ni técnico, por la misma razón que `plot` en `/status`. `last_visit_on` es el `visited_on` de la visita más reciente no borrada, o `null`. Orden: más alertas `critical` abiertas primero, luego más alertas abiertas, luego la visita más antigua (las nunca visitadas primero) y por último el nombre de la finca. Vive en el módulo `home`, como `/status`.

`GET /farms/{farm_id}/visits` responde a cualquier miembro de la organización de la finca. `GET /organizations/{org_id}/visits` es la exportación (RF-19): `owner` y `technician`; otro rol recibe `403` y un no miembro `404`. Las dos son páginas con cursor, de la más reciente a la más antigua.

### Riesgo, métricas y asistente

```
GET  /plots/{plot_id}/risk                     → RiskPrediction[]   # flood, drought, con model_version
GET  /plots/{plot_id}/metrics?month=2026-10    → PlotMetricMonthly
GET  /plots/{plot_id}/cycles/{cycle_id}/summary → CropCycleSummary
GET  /organizations/{org_id}/metrics?month=    → OrgMetrics
POST /assistant/messages  { conversation_id?, plot_id?, content } → text/event-stream (tokens + fuentes citadas)
POST /assistant/messages/{message_id}:feedback { helpful: bool } → 204
```

`GET /plots/{plot_id}/risk` devuelve la predicción vigente de la celda de la parcela por evento (`flood`, `drought`): la del mes en curso de la versión promovida o, sin ella, de la línea base; si el mes en curso todavía no tiene predicción (ERA5 llega con ~5 días de retraso, [06 §8](06-diseno-detallado.md#8-inferencia-de-riesgo-climático)), la más reciente. Un evento sin predicción no aparece; una parcela sin celda responde `[]`. Mismo control de acceso que `/plots/{plot_id}`: `404` si no existe o es de otra organización. Cada elemento:
`{ event_type: "flood"|"drought", horizon_start: date, horizon_days: int, probability: number, severity: "low"|"high"|"critical", top_factors: [{ feature: string, value: number, contribution: number }], model_version: { name: string, version: string, is_baseline: bool, metrics: object }, created_at: string }`. `model_version.metrics` son las métricas de la versión que respondió, o las de su línea base (`baseline_metrics`, [03](03-modelo-datos.md)) cuando la que sirve es una línea base; `null` solo si la fila no tiene ninguna de las dos.

### Operación

```
GET /healthz   → 200 si el proceso vive
GET /readyz    → 200 si la base y el broker responden
GET /metrics   → Prometheus (solo en la red interna)
```

`/metrics` puede existir en ambos perfiles; Prometheus y Grafana, que lo consumen, solo se despliegan en el perfil `production` (futuro) ([ADR-0021](adr/0021-perfil-seminario-local.md)).

### Solo perfil seminario (`/dev`)

Estos endpoints no se registran en el perfil `production` ([ADR-0021](adr/0021-perfil-seminario-local.md)).

```
POST /dev/auth/otp            { phone } → 204     # el código se imprime en la consola del api
POST /dev/auth/otp/verify     { phone, code } → { access_token, token_type }   # 401 si el código es inválido o expiró
POST /dev/jobs/{name}:run     { day? } → { job_id }   # weather, irrigation, risk, metrics
GET  /dev/outbox              → Notification[]    # SMS/WhatsApp simulados
POST /dev/scenarios/{name}:load → 202             # lo usa el simulador para crear datos base y fixtures
```

`weather` es el único nombre que encola los dos jobs de su agenda diaria —el refresco del pronóstico y la consolidación de `day` (por defecto, ayer)— y por eso responde `{ job_id, consolidate_job_id }` ([06-diseno-detallado.md §6](06-diseno-detallado.md#6-clima)); los demás nombres encolan un solo job. En `weather`, `day` debe ser un día ya transcurrido: hoy todavía es pronóstico y un día futuro no tiene clima observado, así que ambos se responden `422`. En `irrigation`, un solo job calcula el balance (D−1) y la recomendación (D); `day` toma por defecto el día de hoy (local) y un día posterior a hoy responde `422` porque el día de balance (D−1) debe haber terminado. En `risk`, un solo job predice el mes de `day` con datos hasta el último día del mes anterior ([06 §8](06-diseno-detallado.md#8-inferencia-de-riesgo-climático)); `day` toma por defecto el día de hoy (local) y un día posterior a hoy responde `422` porque el mes de un día que todavía no ocurrió tiene una ventana de datos que no existe.

`GET /dev/outbox` lista las filas `notification` de canal `sms` o `whatsapp` —las que el adaptador del perfil `seminar` simula ([06 §4](06-diseno-detallado.md#4-notificaciones-outbox))— de la más reciente a la más antigua, como máximo 50. No pide sesión ni `org_id` (es una bandeja de una sola sala con un solo stack detrás) y no incluye las filas `push`, que las muestra el service worker del navegador. Cada elemento tiene la forma:
`{ id: string, alert_id: string, user_id: string, org_id: string, channel: "sms"|"whatsapp", status: "pending|sent|failed", attempts: int, rule_code: string, severity: "info|warning|critical", created_at: string, sent_at: string | null, last_error: string | null }`.
`rule_code` y `severity` vienen de la alerta porque una bandeja que solo dijera "sms enviado" no diría qué llegó, y `last_error` está porque el mensaje que el despachador dio por perdido es justo el que la sala necesita ver.

## Stream (SSE)

```
GET /api/v1/stream?farm_id=…
event: reading        data: { plot_id, metric, value, at }
event: alert.opened   data: Alert
event: alert.updated  data: Alert
event: node.status    data: { node_id, status, at }
```

El cliente se reconecta solo con `Last-Event-ID`. El servidor envía un `:keepalive` cada 20 s para que los proxies no cierren la conexión.

## Contrato MQTT

| Tópico | Dirección | QoS | Retenido | Contenido |
|---|---|---|---|---|
| `tc/v1/{node_id}/up` | nodo → servidor | 1 | no | Telemetría |
| `tc/v1/{node_id}/status` | nodo → servidor | 1 | sí | `online` / `offline` (este último como Last Will) |
| `tc/v1/{node_id}/down` | servidor → nodo | 1 | no | Configuración; comandos de actuador en v2.x |

**ACL del broker:** el usuario MQTT es el `node_id` y solo puede publicar y suscribirse en `tc/v1/%u/#`. Un nodo comprometido no puede escribir por otro.

**Uplink:**

```json
{
  "v": 1,
  "seq": 18234,
  "ts": 1760000400,
  "fw": "1.0.3",
  "m": { "sm_10": 2310, "sm_30": 2250, "st_10": 27.4, "at": 31.2, "rh": 68, "bat": 3.92, "rssi": -71 }
}
```

| Campo | Regla |
|---|---|
| `v` | Versión del esquema del payload. Una versión desconocida se descarta y se cuenta en una métrica. Un valor de otro tipo (booleano, decimal, texto) hace el payload mal formado. |
| `seq` | Contador monotónico del nodo. Sirve para detectar huecos (lecturas perdidas). |
| `ts` | Epoch en segundos, tomado del reloj del nodo sincronizado por NTP. Si falta o está desfasado más de 10 min hacia el futuro, se usa `received_at` y la lectura queda con `quality = 1`. |
| `m` | Mapa `channel_key → valor crudo`. La humedad de suelo llega como ADC crudo; la calibración se aplica en el servidor. Un valor no finito (`NaN`, `Infinity`) hace el payload mal formado: `json` los decodifica, pero ninguna bandera de `quality` describe una lectura no finita. |
| Buffer | Sin conexión, el nodo guarda lecturas (≥ 72 h) y las publica en orden al reconectar, con su `ts` original. |

**Downlink de configuración:**

```json
{ "v": 1, "cmd": "config", "interval_s": 900, "ntp": "pool.ntp.org" }
```

**LoRaWAN:** ChirpStack decodifica el payload binario con un codec y publica en su propio tópico (`application/{app_id}/device/{dev_eui}/event/up`). El ingestor tiene un adaptador para ese formato que produce el mismo mensaje interno que el adaptador de tópicos `tc/v1`.
