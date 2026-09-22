# 02 — Estimaciones y restricciones

**Conclusión:** el sistema escribe mucho más de lo que lee. La telemetría genera unas 11 escrituras por segundo sostenidas en el año 3, frente a ~1 lectura por segundo de la API. Un solo PostgreSQL con TimescaleDB alcanza con amplio margen: no hace falta sharding, colas externas ni microservicios. Lo que más crece son las **fotos de la bitácora**, por eso van a almacenamiento de objetos y no a la base de datos.

Todas las cifras son órdenes de magnitud para dimensionar. Se recalculan con datos reales al terminar el piloto.

## Supuestos de escala

| Supuesto | Piloto (año 1) | Objetivo (año 3) |
|---|---|---|
| Organizaciones | 3 | 50 |
| Fincas | 200 | 5.000 |
| Parcelas | 300 | 8.000 |
| Nodos IoT | 300 | 10.000 |
| Usuarios | 400 | 10.000 |
| Usuarios activos diarios (DAU) | 120 (30 %) | 3.000 (30 %) |
| Variables por nodo | 6 | 6 |
| Intervalo de envío | 15 min | 15 min |

## Tráfico

### Ingesta IoT (escritura)

| Cálculo | Año 3 |
|---|---|
| Mensajes/día = nodos × 96 | 10.000 × 96 = **960.000** |
| Mensajes/s promedio | 960.000 / 86.400 ≈ **11** |
| Pico (envíos alineados al cuarto de hora ×10) | ≈ **110 msg/s** |
| Filas/día = mensajes × 6 variables | **5,76 M** (≈ 67 filas/s) |
| Ráfaga de reconexión: gateway con 200 nodos caído 72 h | 200 × 288 = 57.600 mensajes; a 500 msg/s en lote se vacía en ~2 min |

### API (lectura)

| Cálculo | Año 3 |
|---|---|
| Solicitudes/día = 3.000 DAU × 2 sesiones × 15 solicitudes | **90.000** |
| Promedio | ≈ **1 rps** |
| Pico matutino (5–8 a. m., antes de salir al campo ×10) | ≈ **10 rps** |
| Conexiones SSE concurrentes en pico | ~500 |
| Proporción lectura:escritura de la API | ~5:1 |

### Servicios externos

| Servicio | Cálculo | Año 3 |
|---|---|---|
| Clima (Open-Meteo) | ~200 celdas activas de 0,1° × 8 actualizaciones/día | **1.600 llamadas/día** |
| Push | 10 % de parcelas con alerta/día × 2 destinatarios | ~1.600/día |
| SMS/WhatsApp | ~10 % de las alertas son críticas | ~160/día |
| LLM | 3.000 DAU × 20 % usa el asistente × 3 preguntas | **1.800 preguntas/día** |

> Open-Meteo gratuito es solo para uso **no comercial** y tiene límite diario. Si TechCamp se comercializa hay que pasar a un plan de pago o autoalojar Open-Meteo (es de código abierto). Verificar los términos vigentes antes del lanzamiento.

### Costo del LLM

| Concepto | Por pregunta | Año 3 por día |
|---|---|---|
| Tokens de entrada (sistema + contexto de parcela + fragmentos RAG + historial) | ~3.500 | 6,3 M |
| Tokens de salida | ~400 | 0,72 M |

`costo_diario = 6,3 M × precio_entrada + 0,72 M × precio_salida`. Los precios por millón de tokens se toman de la página del proveedor al implementar. El caché de prompts reduce el costo del contexto fijo. Controles obligatorios: límite por usuario (10 preguntas/día) y tope de presupuesto mensual con degradación a respuestas sin LLM ([ADR-0007](adr/0007-llm-por-api.md)).

## Almacenamiento

| Dato | Cálculo | Piloto/año | Año 3/año |
|---|---|---|---|
| Lecturas crudas | 5,76 M filas/día × ~100 B (fila + índice) | 6 GB | **210 GB** |
| Lecturas comprimidas (TimescaleDB, ~90 %) | 10 % de lo anterior | < 1 GB | **~20 GB** |
| Agregado horario | nodos × 6 × 24 × ~80 B | 0,2 GB | ~40 GB crudo, ~4 GB comprimido |
| Agregado diario | nodos × 6 × ~80 B | despreciable | ~2 GB |
| Bitácora (sin fotos) | 3 entradas/semana/parcela × 1 KB | 50 MB | 1,3 GB |
| **Fotos de bitácora** | 20 % de entradas × 200 KB (comprimidas en el cliente) | 2 GB | **50 GB** → almacenamiento de objetos |
| Base de conocimiento RAG | 10.000 fragmentos × (1 KB de texto + 4 KB de vector) | 50 MB | 50 MB |
| Datos de referencia de la v1 (municipios, NDVI, EVA) | ya existentes | ~1 GB | ~1 GB |

**Retención:**

| Dato | Retención |
|---|---|
| Lecturas crudas | Sin comprimir 7 días; comprimidas 2 años |
| Agregado horario | 5 años |
| Agregado diario, bitácora y cosechas | Indefinida (son la evidencia de impacto) |

## Ancho de banda

| Flujo | Cálculo | Año 3 |
|---|---|---|
| Ingesta MQTT (JSON ~200 B + ~100 B de TLS/MQTT) | 960.000 × 300 B | ~290 MB/día (~27 kbps) |
| API | 90.000 × ~5 KB | ~450 MB/día |
| Fotos (subida) | 700/día × 200 KB | ~140 MB/día |
| PWA (instalación, cacheada después) | ≤ 1 MB por instalación | despreciable |

Por LoRaWAN el payload es binario y mide una decena de bytes, porque el tamaño máximo depende del data rate. El decodificador corre en ChirpStack y el backend recibe el mismo JSON por MQTT.

## Cómputo

| Etapa | Servidor | Justificación |
|---|---|---|
| Piloto | 1 VPS: 4 vCPU, 8 GB de RAM, 160 GB SSD | Datos calientes < 1 GB |
| Año 3 | 1 servidor: 8 vCPU, 32 GB de RAM, 1 TB SSD, más una réplica de lectura opcional | Los chunks sin comprimir de 7 días (~4 GB) caben en `shared_buffers` |

## Resumen

| Métrica | Piloto | Año 3 |
|---|---|---|
| Escrituras de telemetría | 0,3 msg/s | 11 msg/s (pico 110) |
| Lecturas de la API | < 0,1 rps | 1 rps (pico 10) |
| Crecimiento de la base de datos | ~1 GB/año | ~30 GB/año |
| Crecimiento de objetos (fotos) | 2 GB/año | 50 GB/año |
| Preguntas al LLM | ~70/día | 1.800/día |

## Restricciones

| Restricción | Efecto en el diseño |
|---|---|
| Conectividad rural intermitente o inexistente | PWA offline-first; nodos con buffer local; LoRaWAN donde no hay celular |
| Energía en campo | Nodos con panel solar y batería; envío cada 15 min con sueño profundo entre envíos |
| Equipo pequeño (1–3 desarrolladores) | Monolito modular, un solo motor de base de datos, despliegue con Docker Compose |
| Presupuesto de piloto | Un VPS; APIs gratuitas o baratas; sin Kubernetes |
| Sensores baratos (capacitivos) | Imprecisos de fábrica: calibración por sensor obligatoria y versionada |
| Regulación de datos (Ley 1581 de 2012) | Consentimiento, minimización, exportación y borrado |
| Bandas de radio LoRaWAN en Colombia | Confirmar el plan de frecuencias (AU915/US915) con la regulación vigente antes de comprar gateways |
