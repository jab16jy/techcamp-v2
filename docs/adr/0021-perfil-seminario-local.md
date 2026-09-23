# ADR-0021: Perfil de seminario: ejecución local con emuladores

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

TechCamp v2 es un **proyecto de seminario**. No sale a producción salvo que el proyecto crezca. Se ejecuta en `localhost` con emuladores y se presenta en demostraciones. El diseño original ([05-arquitectura](../05-arquitectura.md), [ADR-0017](0017-despliegue-compose-caddy.md)) asume un piloto real con VPS, proveedores de SMS, gateways LoRa y backups.

Hace falta que el sistema:

1. Levante con **un comando** en un portátil, sin cuentas pagas.
2. Tenga una **demo reproducible** que no dependa del clima del día, de hardware ni de internet en el salón.
3. Pueda pasar a producción **sin reescribir el dominio** si el proyecto crece.

## Decisión

Dos **perfiles de ejecución** sobre la misma base de código. Cambian los **adaptadores**, nunca el dominio ni los casos de uso (los puertos del [ADR-0002](0002-monolito-modular.md)).

| Puerto / pieza | Perfil `seminar` (por defecto) | Perfil `production` (futuro) |
|---|---|---|
| Nodos IoT | **Simulador de escenarios** que publica por MQTT con el contrato real | Nodos ESP32 reales, Wi-Fi o celular |
| LoRaWAN | No se usa | ChirpStack ([ADR-0004](0004-mqtt-y-lorawan.md)) |
| Autenticación | Emisor local de JWT: el código OTP se imprime en consola; el backend valida por JWKS igual que en producción | Supabase Auth ([ADR-0014](0014-autenticacion.md)) |
| SMS / WhatsApp | Adaptador que escribe en el log y en una bandeja visible en `/dev/outbox` | Proveedor real ([ADR-0016](0016-notificaciones-outbox.md)) |
| Web Push | Real (en `localhost` el navegador lo permite) | Real |
| Clima | Open-Meteo gratis (uso no comercial) **más fixtures grabados** por escenario | Open-Meteo con plan comercial si aplica |
| Almacenamiento de objetos | MinIO en Docker | S3 compatible ([ADR-0018](0018-almacenamiento-de-objetos.md)) |
| LLM | API real con **tope de presupuesto de USD 5** y modo sin LLM disponible | API real con tope mensual ([ADR-0007](0007-llm-por-api.md)) |
| Jobs diarios | Programados **y** ejecutables al instante con `POST /dev/jobs/{name}:run` | Solo programados |
| Proxy y TLS | Vite dev server; túnel HTTPS (cloudflared o ngrok) solo para probar en un teléfono | Caddy con TLS automático |
| Backups, monitoreo, DR | No aplica | pgBackRest, Prometheus, Grafana ([09](../09-cuellos-de-botella.md)) |

- El perfil se elige con una sola variable (`TECHCAMP_PROFILE=seminar|production`) y un perfil de Docker Compose.
- Los endpoints `/dev/*` **solo existen** en el perfil `seminar`; en `production` no se registran.
- Los ADRs 0004 (LoRaWAN), 0014 (Supabase Auth), 0016 (proveedor de SMS), 0017 (VPS) y 0018 (almacenamiento de objetos S3) siguen vigentes **para producción futura**. No se implementan en el seminario salvo que sobre tiempo.

### Simulador de escenarios

Es la pieza central de la demo. Detalle en [06 §10](../06-diseno-detallado.md#10-simulador-de-escenarios-perfil-seminario).

- **Backfill** de N días de lecturas (con `ts` en el pasado) más **transmisión en vivo** cada pocos segundos.
- Cada escenario inyecta también sus **fixtures de clima**, para que el riego y las alertas respondan al escenario y no al clima real del día.
- En vez de acelerar el reloj del sistema, los jobs diarios se disparan a demanda. Así no hace falta un reloj virtual en el dominio.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Desplegar en un VPS desde el seminario | Costo y operación sin usuarios reales que lo justifiquen |
| Simplificar la arquitectura (sin puertos, todo local "a mano") | Si el proyecto crece habría que reescribir; los puertos ya cuestan poco y separan los perfiles limpiamente |
| Reloj virtual acelerado en todo el sistema | Invasivo: todo el dominio dependería de un `Clock`; backfill más jobs a demanda da el mismo efecto en la demo |
| Supabase local (`supabase start`) como auth del seminario | Levanta muchos contenedores (pesado para un portátil); el emisor local de JWT cubre el caso con el mismo camino de validación |

## Consecuencias

**Positivas**

- Costo del seminario ≈ USD 0 más el LLM (~USD 1–5).
- Demo determinista: los escenarios A–E de [01-requisitos](../01-requisitos.md#escenarios-de-validación) se reproducen igual cada vez.
- Pasar a producción es cambiar adaptadores y configuración, no el dominio.

**Negativas / costos aceptados**

- Hay que mantener dos adaptadores para algunos puertos (auth, notificaciones, objetos).
- Las métricas de impacto ([11-metricas](../11-metricas.md)) se calculan sobre datos simulados: demuestran el cálculo, no el impacto real.
- Lo que solo aparece con hardware real (ruido de sensores baratos, cortes de energía, cobertura) se emula con el simulador, sin validarse en campo.

## Relacionado

[02-estimaciones](../02-estimaciones.md#perfil-seminario), [05-arquitectura](../05-arquitectura.md#perfiles-de-ejecución), [06 §10](../06-diseno-detallado.md#10-simulador-de-escenarios-perfil-seminario), [10-dag](../10-dag.md), [ADR-0018](0018-almacenamiento-de-objetos.md)
