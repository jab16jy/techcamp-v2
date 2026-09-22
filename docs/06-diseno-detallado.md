# 06 — Diseño detallado

Aquí se detallan los flujos que concentran el riesgo del sistema. Cada sección es independiente y se puede leer sola.

| # | Flujo | Por qué es crítico |
|---|---|---|
| 1 | [Ingesta de telemetría](#1-ingesta-de-telemetría) | Es la ruta más cargada y nunca debe perder ni duplicar datos |
| 2 | [Alta de un nodo](#2-alta-de-un-nodo) | Es la primera experiencia del técnico y la base de la seguridad IoT |
| 3 | [Evaluación de alertas](#3-evaluación-de-alertas) | Demasiadas alertas se ignoran; si faltan, hay pérdidas |
| 4 | [Notificaciones (outbox)](#4-notificaciones-outbox) | Tienen que llegar aunque falle el proveedor |
| 5 | [Riego FAO-56](#5-riego-balance-hídrico-fao-56) | Es la recomendación principal del producto |
| 6 | [Clima](#6-clima) | Es una dependencia externa con límites de uso |
| 7 | [Sincronización offline](#7-sincronización-offline-de-la-bitácora) | Es la promesa de que nada se pierde en el campo |
| 8 | [Riesgo climático](#8-inferencia-de-riesgo-climático) | Es el primer modelo propio de la v2 y el más expuesto a fuga de datos |
| 9 | [Asistente](#9-asistente-agronómico) | Controla el costo y evita respuestas inventadas |

## 1. Ingesta de telemetría

```mermaid
sequenceDiagram
  autonumber
  participant N as Nodo
  participant B as Mosquitto
  participant I as ingestor
  participant DB as PostgreSQL
  participant A as api (SSE)

  N->>B: PUBLISH tc/v1/{node}/up (QoS 1)
  B-->>N: PUBACK
  B->>I: entrega (sesión persistente)
  I->>I: validar esquema v, mapear channel_key → sensor
  I->>I: calibrar raw → value, marcar quality
  Note over I: acumula hasta 500 mensajes o 1 s
  I->>DB: INSERT … ON CONFLICT (sensor_id, time) DO NOTHING
  I->>DB: UPDATE node SET last_seen_at, status='online'
  I->>I: evaluar reglas en caliente para las parcelas afectadas
  I->>DB: INSERT alert + notification (misma transacción)
  I->>DB: NOTIFY plot_events
  DB-->>A: evento → SSE a los clientes suscritos
```

| Paso | Regla |
|---|---|
| Validación | Si `v` es desconocida, el `channel_key` no existe o el payload está mal formado, el mensaje se descarta, se cuenta en `ingest_rejected_total{reason}` y se guarda en `ingest_dead_letter` para diagnosticar. |
| Tiempo | Si `ts` está en el futuro (> 10 min) o falta, se usa `received_at` con `quality = 1`. Si es anterior a 30 días, se descarta. |
| Rango físico | Un valor calibrado fuera del rango de la variable (por ejemplo, humedad > 100 %) se guarda con `quality = 2` y no dispara alertas. |
| Lote | Inserción en lote cada 500 mensajes o 1 s. Con 110 msg/s de pico (año 3) queda muy lejos del límite. |
| Duplicados | La restricción `UNIQUE (sensor_id, time)` más `ON CONFLICT DO NOTHING` hace idempotente la entrega "al menos una vez" de QoS 1. |
| Huecos | Un salto en `seq` incrementa `ingest_gap_total`. La completitud diaria por nodo es un SLI ([11-metricas](11-metricas.md)). |

> **Límite conocido:** la librería MQTT confirma el mensaje al recibirlo, así que si el ingestor cae entre la confirmación y la inserción se pierde como máximo un lote (≤ 1 s). Se detecta como hueco de `seq`. Si la completitud baja del SLO, se pasa a confirmación manual después del `INSERT`.

## 2. Alta de un nodo

```mermaid
sequenceDiagram
  autonumber
  actor T as Técnico
  participant P as PWA
  participant API as api
  participant DB as PostgreSQL
  participant B as Mosquitto (Dynamic Security)
  participant N as Nodo

  T->>P: escanea el QR del nodo (claim_code)
  P->>API: POST /nodes:claim {claim_code, plot_id}
  API->>DB: node.status = provisioned, crea sensores según el modelo de hardware
  API->>B: createClient(node_id, password) + rol "node" con ACL tc/v1/%u/#
  API-->>P: credenciales MQTT (se muestran una sola vez)
  T->>N: portal cautivo del nodo: Wi-Fi + credenciales
  N->>B: CONNECT + PUBLISH status=online
  B->>API: (vía ingestor) primera lectura
  API-->>P: SSE node.status online → "Nodo funcionando"
  T->>P: calibración en campo: lectura en seco y saturado → POST /sensors/{id}/calibrations
```

- El `claim_code` va impreso en el nodo, es de un solo uso y se genera al fabricar o flashear el nodo.
- La contraseña se guarda como hash en la base. Mosquitto guarda la suya en el plugin Dynamic Security. Rotarla invalida la anterior de inmediato.
- Si se pierde un nodo, se marca `retired` y se deshabilita su cliente MQTT.

## 3. Evaluación de alertas

Hay cuatro fuentes de alertas, todas en el mismo módulo `alerts`:

| Fuente | Dónde se evalúa | Ejemplo |
|---|---|---|
| Umbral sobre lecturas | `ingestor`, en caliente, después de cada lote | Humedad de suelo < umbral del cultivo durante ≥ 6 h |
| Pronóstico | `worker`, después de actualizar el clima | Lluvia > 50 mm en 24 h con el suelo ya saturado |
| Modelo | `worker`, después de la inferencia de riesgo | Probabilidad de inundación con severidad `alto` o `crítico` |
| Salud del nodo | `worker`, cada 5 min | Sin lecturas durante 3 intervalos; batería < 3,4 V |

Para evitar alertas intermitentes (flapping), cada regla define `min_duration_min` e `hysteresis`. Una alerta de humedad baja abre con < 20 % sostenido 6 h y solo se resuelve con > 23 % (20 + 3) sostenido 1 h.

```mermaid
stateDiagram-v2
  [*] --> Pending: condición verdadera
  Pending --> [*]: condición falsa antes de min_duration
  Pending --> Open: sostenida ≥ min_duration
  Open --> Acknowledged: usuario reconoce
  Open --> Escalated: crítica sin reconocer 2 h
  Escalated --> Acknowledged: usuario reconoce
  Open --> Resolved: condición falsa + histéresis
  Acknowledged --> Resolved: condición falsa + histéresis o cierre manual
  Escalated --> Resolved: condición falsa + histéresis
  Resolved --> [*]
```

- **Una sola alerta abierta** por (`rule_id`, `plot_id`/`node_id`). Lo garantiza un índice único parcial en la base, no el código.
- `Pending` vive en memoria del evaluador y en el estado de la regla; no se persiste como alerta.
- Escalar una alerta crítica notifica por SMS o WhatsApp al técnico asignado.
- Las alertas de nodo van al técnico, no al productor ([01-requisitos](01-requisitos.md), escenario C).

**Reglas de fábrica** (`org_id = null`). Los umbrales se validan con un agrónomo antes del piloto:

| Código | Condición | Severidad |
|---|---|---|
| `water_stress` | Humedad de suelo < `crop.stress_threshold_pct` durante 6 h | warning; crítica si dura 48 h |
| `waterlogging` | Humedad de suelo > capacidad de campo + 5 durante 24 h | warning |
| `heat_stress` | Temperatura del aire > 35 °C durante 3 h | warning |
| `fungal_risk` | Humedad relativa > 85 % durante ≥ 10 h en el día y temperatura media de 20–30 °C | warning |
| `heavy_rain_forecast` | Pronóstico > 50 mm en 24 h | warning; crítica si el suelo está saturado |
| `flood_risk` / `drought_risk` | Severidad del modelo ≥ `alto` | crítica |
| `node_offline` | Sin lecturas durante 3 intervalos | warning (al técnico) |
| `node_battery_low` | `battery_v` < 3,4 V | info (al técnico) |

## 4. Notificaciones (outbox)

```mermaid
sequenceDiagram
  autonumber
  participant E as Evaluador de alertas
  participant DB as PostgreSQL
  participant W as worker
  participant P as Web Push / SMS

  E->>DB: BEGIN, INSERT alert, INSERT notification pending por destinatario y canal, COMMIT
  loop cada 5 s
    W->>DB: SELECT … WHERE status='pending' AND next_attempt_at <= now() FOR UPDATE SKIP LOCKED LIMIT 50
    W->>P: enviar
    alt éxito
      W->>DB: status = sent
    else 410 Gone (suscripción vencida)
      W->>DB: borrar push_subscription y probar el siguiente canal
    else error temporal
      W->>DB: attempts++, next_attempt_at = now() + backoff
    end
  end
```

| Regla | Valor |
|---|---|
| Garantía | La alerta y su notificación se guardan en la misma transacción: no hay alerta sin aviso ni aviso sin alerta. |
| Reintentos | Backoff exponencial: 1 min, 5 min, 30 min, 2 h; máximo 5 intentos, luego `failed`. |
| Circuit breaker | Por proveedor. Con 5 fallos seguidos se abre 5 min; mientras tanto las críticas pasan al canal alterno. |
| Horas de silencio | 20:00–05:00: solo notificaciones críticas; el resto se agrupa para las 05:00. |
| Agrupación | Varias alertas no críticas de la misma finca en 15 min se envían en una sola notificación. |
| Canales por severidad | `info`: solo dentro de la app. `warning`: push. `critical`: push y, si no se reconoce, SMS o WhatsApp. |

## 5. Riego: balance hídrico FAO-56

Método de **coeficiente de cultivo único** de FAO-56 (capítulos 6 y 8). La humedad medida por el sensor corrige el modelo cada día ([ADR-0009](adr/0009-riego-fao56.md)).

| Símbolo | Cálculo | Fuente |
|---|---|---|
| ET0 | mm/día | Open-Meteo `et0_fao_evapotranspiration` de la celda |
| Kc | Por etapa; interpolación lineal durante el desarrollo | `crop_stage` |
| ETc | `Kc × ET0` | — |
| TAW | `1000 × (θFC − θWP) × Zr` | `soil_profile`; Zr es la profundidad de raíz en metros |
| RAW | `p × TAW` | `crop_stage.depletion_fraction_p` |
| Pe | `0,8 × P` si P > 5 mm; si no, 0 | Lluvia de la celda o del pluviómetro del nodo |
| Dr | `Dr(i) = clamp(Dr(i−1) − Pe − I + ETc, 0, TAW)` | Balance diario |
| Dr observado | `1000 × (θFC − θobs) × Zr` | Promedio diario de humedad del sensor en la zona de raíces |

```mermaid
flowchart TD
  start([Job diario 04:30 America/Bogota]) --> cyc{¿Parcela con ciclo activo?}
  cyc -- no --> fin([fin])
  cyc -- sí --> et[Obtener ET0, lluvia y pronóstico de la celda]
  et --> bal[Dr = balance del día anterior + ETc − Pe − riego registrado]
  bal --> obs{¿Hay humedad de sensor válida en las últimas 24 h?}
  obs -- sí --> asm[Dr = Dr observado<br/>guardar el error modelo − observado]
  obs -- no --> keep[Mantener Dr modelado<br/>marcar recomendación sin sensor]
  asm --> dec
  keep --> dec{Dr ≥ RAW?}
  dec -- no --> ok[Estado ok o watch<br/>sin riego]
  dec -- sí --> rain{¿Lluvia pronosticada en 48 h ≥ Dr?}
  rain -- sí --> wait[Posponer: va a llover]
  rain -- no --> rec[lámina = Dr / eficiencia del sistema<br/>minutos = lámina × área_m² / caudal_lph × 60]
  rec --> save[Guardar irrigation_recommendation + rationale]
  ok --> save
  wait --> save
  save --> notif[Push informativo: Hoy riegue 12 mm, unos 40 min]
```

- **Eficiencia del sistema:** goteo 0,90, aspersión 0,75, gravedad 0,60 (configurable por parcela).
- **`rationale`** guarda los números usados (ET0, Kc, Dr, pronóstico). Lo muestra la interfaz y lo usa el asistente para explicar la recomendación.
- El error entre modelo y observación es el SLI "error de humedad" ([11-metricas](11-metricas.md)).

## 6. Clima

- Las parcelas se agrupan en **celdas de 0,1°** (`weather_cell`). Se consulta una vez por celda, no por parcela.
- Un job cada 3 h actualiza el pronóstico de las celdas activas, y uno diario consolida el día anterior como observado (`is_forecast = false`).
- Llamadas externas con `httpx`: timeout de 10 s, 3 reintentos con backoff y jitter, y circuit breaker.
- **Degradación:** si Open-Meteo falla se usa el último dato guardado, marcado `stale`, y la interfaz muestra la hora del dato. Una recomendación de riego con clima de más de 24 h de antigüedad se marca como de baja confianza.

## 7. Sincronización offline de la bitácora

```mermaid
sequenceDiagram
  autonumber
  actor U as Productor
  participant UI as PWA (UI)
  participant L as Dexie (IndexedDB)
  participant S as Sincronizador
  participant API as api

  U->>UI: registra una cosecha (sin señal)
  UI->>L: upsert logbook_entry (UUIDv7, client_updated_at) + outbox
  UI-->>U: "Guardado en el teléfono" (pendiente de subir)
  Note over S: se activa al abrir la app, con el evento online,<br/>cada 60 s si está visible y 2 s después de escribir
  S->>API: POST /sync/push (lotes ≤ 100)
  API->>API: por cambio: aplicar si es nuevo o más reciente (LWW)
  API-->>S: results[] con server_version
  S->>L: marcar como sincronizado y borrar del outbox
  S->>API: GET /sync/pull?since=cursor
  API-->>S: cambios de otros dispositivos y usuarios
  S->>L: aplicar y guardar el nuevo cursor
  S->>API: fotos pendientes: presign → PUT a S3 → confirmar
```

| Caso | Comportamiento |
|---|---|
| El mismo cambio llega dos veces | `duplicate`: sin efecto (mismo `id` y mismo `client_updated_at`) |
| Dos dispositivos editan la misma entrada | Gana el `client_updated_at` mayor; el perdedor recibe `conflict_overwritten` y la interfaz lo avisa. Se acepta porque las entradas de bitácora casi nunca las editan dos personas a la vez ([ADR-0013](adr/0013-sincronizacion-offline.md)). |
| Reloj del teléfono muy desfasado | El servidor rechaza un `client_updated_at` más de 24 h en el futuro (`rejected`); la interfaz pide corregir la hora. |
| Token vencido sin conexión | Se sigue escribiendo en local. Al volver la señal se renueva el token antes de sincronizar. |
| iOS | No hay Background Sync API: la sincronización ocurre con la app en primer plano. |
| Almacenamiento | Se pide `navigator.storage.persist()` para que el navegador no borre IndexedDB. Las fotos se comprimen en el cliente a ≤ 200 KB. |

## 8. Inferencia de riesgo climático

```mermaid
flowchart LR
  j([Job diario 06:00]) --> f[Features por celda activa<br/>lluvia acumulada 1–6 meses, temperatura,<br/>humedad de suelo, estacionalidad]
  f --> m{¿Hay modelo promovido<br/>para el evento?}
  m -- sí --> p[predict_proba → calibrador → probabilidad]
  m -- no --> h[Heurística de línea base]
  p --> s[Severidad según umbrales del artefacto]
  h --> s
  s --> r[(risk_prediction + model_version_id + top_factors)]
  r --> al[Evaluar reglas flood_risk / drought_risk]
```

- **Paridad entre entrenamiento y producción:** las features se calculan con el mismo módulo y **la misma fuente** en los dos lados (archivo histórico de Open-Meteo para entrenar, pronóstico y observados recientes de Open-Meteo para inferir). El modelo se construye con el protocolo del [ADR-0020](adr/0020-protocolo-de-experimentacion-ml.md); detalle en [08-ml](08-ml.md#m2-riesgo-de-inundación).
- Cada predicción guarda `model_version_id`: toda alerta se puede rastrear hasta el modelo exacto.

## 9. Asistente agronómico

```mermaid
sequenceDiagram
  autonumber
  actor U as Usuario
  participant API as api / assistant
  participant F as Fachadas (farms, irrigation, alerts, risk)
  participant DB as pgvector
  participant L as LLM (puerto)

  U->>API: pregunta + plot_id
  API->>API: límite diario del usuario y presupuesto mensual
  alt límite o presupuesto agotado
    API-->>U: respuesta sin LLM: datos de la parcela + documentos relevantes
  else
    API->>F: estado estructurado de la parcela
    API->>DB: embedding de la pregunta → top 5 fragmentos (similitud ≥ 0,75)
    API->>L: sistema + hechos de la parcela + fragmentos + pregunta (stream)
    L-->>API: tokens
    API-->>U: SSE: tokens + fuentes citadas
    API->>API: guardar mensaje, tokens y costo
  end
```

**Reglas del prompt:**

- Responder en español sencillo.
- Usar solo los hechos de la parcela y los fragmentos entregados, y citar la fuente.
- Si no hay información suficiente, decirlo.
- **Nunca inventar dosis de agroquímicos:** remitir a la etiqueta del producto registrado ante el ICA y al técnico.
- El asistente **explica** recomendaciones y alertas que calcula el sistema; no calcula riego ni riesgo por su cuenta.
