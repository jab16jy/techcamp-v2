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
| Tiempo | ISO 8601 en UTC. Los rangos son `from` inclusivo y `to` exclusivo. |
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
POST   /farms                                  { org_id, name, municipality_code, location } → Farm
GET    /farms/{farm_id}/plots                  → Plot[]
POST   /farms/{farm_id}/plots                  { name, boundary: GeoJSON Polygon, system_flow_lph? } → Plot
PATCH  /plots/{plot_id}                        { name?, boundary?, system_flow_lph? } → Plot
PUT    /plots/{plot_id}/soil                   SoilProfile → SoilProfile
POST   /plots/{plot_id}/soil:autofill          → SoilProfile   # SoilGrids
GET    /crops                                  → Crop[] (con etapas y Kc)
POST   /plots/{plot_id}/cycles                 { crop_id, sown_on } → CropCycle
PATCH  /cycles/{cycle_id}                      { status?, expected_harvest_on? } → CropCycle
```

### Estado de la parcela (pantalla principal)

Una sola llamada que arma todo lo que el productor ve al abrir la app. Así se evitan varias solicitudes seguidas en 3G.

```
GET /plots/{plot_id}/status → {
  plot, active_cycle: { crop, stage, day_of_cycle },
  latest: { soil_moisture_pct, air_temp_c, air_rh_pct, at },
  water_balance: { depletion_mm, taw_mm, status: "ok|watch|irrigate" },
  recommendation: { depth_mm, duration_min, rationale[] } | null,
  open_alerts: Alert[],
  weather_next_3d: WeatherDay[],
  nodes: NodeHealth[],
  technification_index: { value, month }
}
```

### Nodos y sensores

```
POST   /nodes:claim                           { claim_code, plot_id } → Node & { mqtt: { username, password } }  # la contraseña se muestra una sola vez
GET    /nodes?plot_id=&status=                → Page<Node>
PATCH  /nodes/{node_id}                       { plot_id?, status? } → Node
POST   /nodes/{node_id}/credentials:rotate    → { password }
GET    /nodes/{node_id}/health                → { last_seen_at, battery_v, rssi, completeness_24h }
GET    /nodes/{node_id}/sensors               → Sensor[]
POST   /sensors/{sensor_id}/calibrations      { method, params, valid_from } → Calibration   # crea una versión nueva
```

### Lecturas y clima

```
GET /plots/{plot_id}/readings?metric=soil_moisture&from=&to=&resolution=raw|hour|day
    → { series: [{ sensor_id, depth_cm, points: [[t, value], …] }] }
GET /plots/{plot_id}/weather?days=7           → WeatherDay[]   # pronóstico + últimos observados
```

La resolución se elige según el rango: `raw` hasta 2 días, `hour` hasta 60 días y `day` para más. Si se pide `raw` con un rango mayor, el servidor responde `422`, para proteger la base.

### Riego

```
GET  /plots/{plot_id}/irrigation/recommendation?day=  → IrrigationRecommendation
GET  /plots/{plot_id}/water-balance?from=&to=         → WaterBalanceDay[]
```

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

### Bitácora (sincronización offline)

```
POST /sync/push {
  device_id,
  changes: [{ id, entity: "logbook_entry", op: "upsert|delete", data, client_updated_at }]
} → { results: [{ id, status: "applied|duplicate|conflict_overwritten|rejected", server_version, error? }] }

GET  /sync/pull?since=<server_version>&limit=500
  → { changes: [{ id, entity, op, data, server_version }], next_since, has_more }

POST /attachments:presign  { logbook_entry_id, content_type, bytes } → { upload_url, object_key }
```

El cliente sube la foto directo al almacenamiento de objetos con la URL prefirmada. La API nunca recibe los bytes ([ADR-0018](adr/0018-almacenamiento-de-objetos.md)).

### Riesgo, métricas y asistente

```
GET  /plots/{plot_id}/risk                     → RiskPrediction[]   # flood, drought, con model_version
GET  /plots/{plot_id}/metrics?month=2026-10    → PlotMetricMonthly
GET  /plots/{plot_id}/cycles/{cycle_id}/summary → CropCycleSummary
GET  /organizations/{org_id}/metrics?month=    → OrgMetrics
POST /assistant/messages  { conversation_id?, plot_id?, content } → text/event-stream (tokens + fuentes citadas)
POST /assistant/messages/{message_id}:feedback { helpful: bool } → 204
```

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
POST /dev/jobs/{name}:run     { day? } → { job_id }   # weather, water-balance, irrigation, risk, metrics
GET  /dev/outbox              → Notification[]    # SMS/WhatsApp simulados
POST /dev/scenarios/{name}:load → 202             # lo usa el simulador para crear datos base y fixtures
```

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
| `v` | Versión del esquema del payload. Una versión desconocida se descarta y se cuenta en una métrica. |
| `seq` | Contador monotónico del nodo. Sirve para detectar huecos (lecturas perdidas). |
| `ts` | Epoch en segundos, tomado del reloj del nodo sincronizado por NTP. Si falta o está desfasado más de 10 min hacia el futuro, se usa `received_at` y la lectura queda con `quality = 1`. |
| `m` | Mapa `channel_key → valor crudo`. La humedad de suelo llega como ADC crudo; la calibración se aplica en el servidor. |
| Buffer | Sin conexión, el nodo guarda lecturas (≥ 72 h) y las publica en orden al reconectar, con su `ts` original. |

**Downlink de configuración:**

```json
{ "v": 1, "cmd": "config", "interval_s": 900, "ntp": "pool.ntp.org" }
```

**LoRaWAN:** ChirpStack decodifica el payload binario con un codec y publica en su propio tópico (`application/{app_id}/device/{dev_eui}/event/up`). El ingestor tiene un adaptador para ese formato que produce el mismo mensaje interno que el adaptador de tópicos `tc/v1`.
