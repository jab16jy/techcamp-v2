# ADR-0002: Monolito modular con arquitectura hexagonal por módulo

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

El equipo es de 1 a 3 personas. La carga estimada es baja (11 escrituras/s y 1 lectura/s en el año 3, ver [02-estimaciones](../02-estimaciones.md)). La v1 no tenía límites entre módulos y todo dependía de todo.

## Decisión

Un **monolito modular** en Python (FastAPI) con un paquete por dominio (`identity`, `farms`, `telemetry`, `weather`, `irrigation`, `alerts`, `notifications`, `logbook`, `risk`, `metrics`, `assistant`). Cada módulo tiene las capas `domain` / `application` / `adapters`. Tres procesos (`api`, `ingestor`, `worker`) con la misma imagen. Las reglas de dependencia entre módulos se verifican en CI con `import-linter`. Los puertos (interfaces) se crean solo cuando hay dos implementaciones o cuando se necesita un doble de prueba para un sistema externo.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Microservicios | Red, despliegues, observabilidad distribuida y consistencia eventual para un equipo pequeño y una carga que cabe en un proceso |
| Monolito por capas (routers/services/models como en la v1) | Esconde el dominio y permite dependencias cruzadas sin control: es el problema de la v1 |
| Serverless / funciones | La ingesta MQTT y SSE necesitan procesos de larga vida; dependencia de un proveedor |

## Consecuencias

**Positivas**

- Un despliegue, una transacción de base de datos por caso de uso y depuración simple.
- Límites explícitos: un módulo se puede extraer como servicio más adelante si la carga lo justifica.
- La estructura de carpetas cuenta qué hace el negocio.

**Negativas / costos aceptados**

- La disciplina de límites depende de `import-linter` y de las revisiones; no la impone la red.
- Un fallo grave en un proceso afecta a todos los módulos de ese proceso.

## Relacionado

[05-arquitectura](../05-arquitectura.md)
