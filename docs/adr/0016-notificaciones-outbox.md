# ADR-0016: Notificaciones con outbox transaccional, reintentos y canal alterno

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

Una alerta crítica que no llega puede costar una cosecha. Los proveedores de push y SMS fallan. En iOS, el push solo funciona con la PWA instalada.

## Decisión

- La alerta y sus filas de `notification` se escriben en la misma transacción (**outbox**).
- El `worker` las envía con backoff exponencial (máx. 5 intentos) y un circuit breaker por proveedor.
- Canales por severidad: info solo dentro de la app; warning por push; critical por push y escalamiento a SMS/WhatsApp si no se reconoce en 2 h.
- Horas de silencio y agrupación para lo no crítico.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Enviar dentro de la petición o de la ingesta | Se pierde si el proveedor falla o el proceso cae |
| Servicio de notificaciones de terceros (OneSignal, etc.) | Otra dependencia y datos de usuarios hacia un tercero; el volumen es bajo |
| Solo push | No alcanza para críticas en iOS ni con la PWA desinstalada |

## Consecuencias

**Positivas**

- Ninguna alerta se queda sin aviso, y los reintentos son seguros.
- Se evita la fatiga de alertas con agrupación y horas de silencio.

**Negativas / costos aceptados**

- Costo por SMS/WhatsApp; se reserva a críticas escaladas.
- Integrar WhatsApp Business requiere plantillas aprobadas por Meta.

## Relacionado

[06 §4](../06-diseno-detallado.md#4-notificaciones-outbox)
