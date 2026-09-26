# 09 — Cuellos de botella, confiabilidad y seguridad

> **Aplica al perfil `production` (futuro).** En el perfil seminario ([ADR-0021](adr/0021-perfil-seminario-local.md)) solo aplican la sección de [seguridad](#seguridad) (credenciales por nodo, aislamiento entre organizaciones, validación de entradas) y el manejo de fallas del simulador. Backups, DR y monitoreo no aplican.

**Conclusión:** en el piloto el mayor riesgo es un **punto único de falla (el VPS)**, no la carga. Se acepta, con backups continuos y una restauración ensayada. El campo tolera caídas cortas del servidor porque los nodos guardan 72 h de lecturas y la PWA funciona offline. Cada mejora de escala tiene un **disparador medible**: no se construye antes de necesitarla.

## Puntos únicos de falla

| Componente | ¿Punto único? | Impacto si cae | Mitigación en el piloto | Mejora posterior (y cuándo) |
|---|---|---|---|---|
| VPS | Sí | Todo el servidor cae | Backups WAL continuos a S3 (pgBackRest); restauración ensayada cada trimestre; nodos y PWA siguen funcionando offline | Segundo host en espera cuando haya contratos con SLA > 99,5 % |
| PostgreSQL | Sí | API e ingesta caen | Lo mismo que el VPS; `readyz` falla rápido | Réplica en streaming con failover (Patroni) al pasar de 2.000 nodos o tener SLA |
| Mosquitto | Sí | Los nodos acumulan lecturas en su buffer | Persistencia en disco, reinicio automático, buffer de 72 h en los nodos | Puente a un broker en clúster (EMQX) si se necesitan más de ~50.000 conexiones |
| Gateway LoRa de una zona | Sí, para esa zona | Los nodos de la zona no suben datos | Buffer en el nodo, alerta `node_offline` agrupada por gateway | Dos gateways con cobertura solapada en zonas críticas |
| Open-Meteo | Externo | Sin pronóstico nuevo | Último dato guardado marcado `stale`; circuit breaker | Autoalojar Open-Meteo o tener un segundo proveedor |
| API de LLM | Externo | Asistente sin narrativa | Respuesta sin LLM (datos más documentos) | Segundo proveedor detrás del mismo puerto |
| Proveedor de auth | Externo | No se puede iniciar sesión (con sesión activa se sigue usando la app) | Tokens de refresco de larga duración; la app funciona offline con la sesión vigente | Evaluar autoalojar la autenticación si el proveedor se vuelve un riesgo contractual |
| Push / SMS | Externo | Avisos retrasados | Outbox con reintentos y canal alterno | Segundo proveedor de SMS |

## Cuellos de botella esperados

| Cuello de botella | Síntoma | Solución | Disparador |
|---|---|---|---|
| Inserción de lecturas | Latencia de ingesta p95 > 60 s | Lotes más grandes o `COPY`; más instancias del ingestor con suscripción compartida | > 500 msg/s sostenidos |
| Consultas de series largas | API p95 > 300 ms en `/readings` | Obligar el uso de agregados (ya se rechaza `raw` en rangos largos), índices y chunks más pequeños | Consultas lentas en `pg_stat_statements` |
| Tablero de organización | Consultas pesadas al cargar | Tablas precalculadas (`plot_metric_monthly`, `crop_cycle_summary`) | Ya en el diseño |
| Ráfaga de reconexión | Pico de mensajes al volver un gateway | Lotes + `ON CONFLICT`; el nodo publica el buffer con pausas | Siempre activo |
| Conexiones SSE | Memoria o descriptores del proceso `api` | Una conexión `LISTEN` por proceso compartida entre clientes; límite de 1 stream por usuario | > 2.000 streams concurrentes |
| Jobs diarios (riego, riesgo) | No terminan antes de las 05:00 | Procesar por celda y en paralelo en el worker | Duración del job > 30 min |
| Pool de conexiones de Postgres | Esperas por conexión | Tamaño de pool por proceso; PgBouncer en modo transacción | > 100 conexiones activas |
| Costo del LLM | Tope mensual alcanzado | Caché del prompt fijo, modelo más barato, límites por usuario | Gasto > 80 % del tope a mitad de mes |

**Preguntas del método de Karan Pratap Singh:**

| Pregunta | Respuesta |
|---|---|
| ¿Hacen falta réplicas? | No en el piloto; sí al tener SLA o más de 2.000 nodos. |
| ¿Hace falta sharding? | No: ~30 GB/año en el año 3 caben de sobra en un solo nodo; la partición por tiempo de TimescaleDB alcanza. |
| ¿Hace falta caché? | No hay Redis. Las celdas climáticas son la caché de Open-Meteo, las tablas precalculadas son la de métricas, la PWA cachea en el cliente y la memoria de Postgres cubre el resto. |
| ¿Qué pasa con un pico de tráfico? | El pico real es la ráfaga de reconexión IoT, que se absorbe con lotes. El tráfico humano (10 rps de pico) es bajo. |

## Modos de falla

| Falla | Detección | Respuesta automática | Dato perdido |
|---|---|---|---|
| Nodo sin conexión | `node_offline` después de 3 intervalos | Alerta al técnico | Ninguno si vuelve antes de 72 h |
| Sensor descalibrado o dañado | Valores con la bandera 2 de `quality`; varianza cero durante 24 h | Alerta `sensor_suspect` al técnico; se excluye del balance hídrico | Ninguno (se guarda el crudo) |
| Reloj del nodo desfasado | `ts` fuera de tolerancia | Se usa `received_at`, `quality = 1` | Precisión temporal del buffer |
| Ingestor cae | `readyz`, métrica de lag del broker | Reinicio por Compose; el broker guarda los mensajes de la sesión persistente | ≤ 1 lote (ver [06 §1](06-diseno-detallado.md#1-ingesta-de-telemetría)) |
| Worker cae | Edad del job pendiente más antiguo | Reinicio; los jobs se retoman (`SKIP LOCKED`) | Ninguno |
| Proveedor externo caído | Circuit breaker abierto | Dato `stale` o canal alterno | Ninguno |
| Disco lleno | Alerta de Prometheus al 80 % | Política de compresión y retención | Ninguno si se atiende a tiempo |
| Corrupción o borrado accidental | — | Restauración a un punto en el tiempo (PITR) | ≤ RPO |

## Recuperación ante desastres

| Objetivo | Piloto | Cómo |
|---|---|---|
| **RPO** (pérdida máxima) | 5 min | Archivo continuo de WAL a S3 con pgBackRest |
| **RTO** (tiempo de recuperación) | 4 h | Script de reconstrucción: VPS nuevo + Compose + restauración + rotar DNS |
| Backup completo | Semanal, más incremental diario | pgBackRest |
| Ensayo | Trimestral, restaurando en un VPS temporal | Checklist en el runbook |

Las fotos en S3 usan versionado del bucket. La configuración (`infra/`) está en git; los secretos, en un gestor de secretos o en variables cifradas, nunca en el repositorio.

## Seguridad

| Amenaza | Control |
|---|---|
| Nodo suplantado o comprometido | Credenciales únicas por nodo, ACL `tc/v1/%u/#`, TLS (8883), rotación y revocación inmediatas |
| Acceso a datos de otra organización | Filtro obligatorio por `org_id` en cada repositorio (mixin compartido) más tests de aislamiento entre organizaciones en CI. Row Level Security de Postgres se evalúa como defensa adicional en v2.x. |
| Token robado | JWT de vida corta (≤ 1 h) con refresco; validación de firma e `iss`/`aud` con JWKS |
| Abuso de la API | Límite de solicitudes por usuario e IP en la API; límite de conexiones en Caddy |
| Inyección y datos maliciosos | Pydantic en todos los bordes; SQL parametrizado (SQLAlchemy); el payload MQTT se valida con esquema |
| Prompt injection en el asistente | Los fragmentos RAG y el texto del usuario van como datos, no como instrucciones; el asistente no tiene herramientas que escriban; salida solo como texto |
| Fotos con metadatos sensibles | El cliente elimina los metadatos EXIF (incluida la ubicación GPS) al comprimir |
| Datos personales (Ley 1581) | Consentimiento versionado, exportación y borrado (`DELETE /me`), mínimo de datos personales; los datos para investigación se anonimizan |
| Secretos | Variables de entorno desde el gestor de secretos; `gitleaks` en CI |
| Dependencias | Renovate/Dependabot y escaneo de imágenes |

## Observabilidad

| Señal | Herramienta | Ejemplos |
|---|---|---|
| Logs | JSON a stdout con `request_id` y `node_id` | Errores de ingesta, jobs fallidos |
| Métricas | Prometheus (`/metrics`) + Grafana | `ingest_messages_total`, `ingest_lag_seconds`, `alert_dispatch_latency_seconds`, `sync_batches_total{status}`, `llm_cost_cop_total` |
| Salud | `healthz` / `readyz` | Chequeos de Compose y de un monitor externo de disponibilidad |
| Errores | Sentry (o equivalente autoalojado) en la API y la PWA | Excepciones con contexto |

Las alertas operativas (disco, lag de ingesta, jobs atrasados, SLO en riesgo) llegan al equipo, no a los productores.
