# 01 — Requisitos

TechCamp v2 ayuda a productores y técnicos del Caribe colombiano a **medir** lo que pasa en su parcela, **decidir** con esos datos (regar, proteger, cosechar) y **demostrar** el impacto de tecnificarse.

**Alcance actual:** es un proyecto de seminario. Se ejecuta en local con un simulador de nodos y escenarios ([ADR-0021](adr/0021-perfil-seminario-local.md)). Los requisitos funcionales se cumplen completos en ese perfil. Los no funcionales marcados con *(prod)* aplican solo si el sistema pasa a producción.

La v1 analizaba ubicaciones con datos públicos y modelos entrenados con datos sintéticos. La v2 cambia el centro: el dato principal lo produce el campo (sensores y bitácora); los datos públicos y los modelos lo complementan.

## Usuarios

| Perfil | Contexto real | Qué necesita |
|---|---|---|
| **Productor** | Android de gama baja, señal intermitente, sol directo, poco tiempo; la mayoría sin sistema de riego | Saber qué hacer hoy con el agua de su parcela (regar o, en secano, cómo manejar el déficit), recibir alertas, anotar labores y cosechas sin conexión |
| **Técnico / extensionista** | Visita varias fincas, reporta a una cooperativa o entidad | Ver el estado de muchas parcelas, instalar y calibrar nodos, registrar visitas |
| **Administrador de organización** | Cooperativa, asociación o programa | Gestionar miembros, fincas y nodos; ver indicadores agregados |
| **Investigador** | Universidad o entidad agropecuaria | Exportar datos anonimizados, evaluar modelos |

## Requisitos funcionales

### Núcleo (v2.0)

| ID | Requisito |
|---|---|
| RF-01 | Registro e inicio de sesión con **teléfono (OTP por SMS)** o correo. Roles por organización: `owner`, `technician`, `producer`, `viewer`. |
| RF-02 | Gestión de **fincas y parcelas**: dibujar el polígono en el mapa o caminar el perímetro con GPS; calcular área automáticamente; indicar el sistema de riego o que la parcela es de secano. |
| RF-03 | Perfil de suelo por parcela: autocompletado desde SoilGrids y reemplazable por análisis de laboratorio. |
| RF-04 | **Ciclos de cultivo**: cultivo, fecha de siembra, etapa fenológica estimada y fecha de cosecha esperada. |
| RF-05 | **Nodos IoT**: alta (código QR del nodo), asignación a parcela, **calibración por sensor**, estado (en línea, batería, señal). |
| RF-06 | **Telemetría**: ingesta continua de humedad de suelo, temperatura y humedad relativa, lluvia y batería; lecturas en vivo e históricas. |
| RF-07 | **Alertas** por reglas (estrés hídrico, saturación, calor, riesgo fitosanitario, nodo caído, batería baja) y por modelos (riesgo de inundación o sequía). Ciclo: abierta → reconocida → resuelta. |
| RF-08 | **Notificaciones**: push web; SMS o WhatsApp como respaldo para alertas críticas. |
| RF-09 | **Decisión hídrica diaria** por parcela, basada en ET0 FAO, Kc del cultivo, lluvia y humedad de suelo medida. Con riego: lámina en mm y tiempo de riego según el caudal del sistema. De secano: déficit hídrico, lluvia pronosticada y consejo de manejo, sin lámina ([ADR-0023](adr/0023-parcelas-con-riego-y-secano.md)). |
| RF-10 | **Bitácora de campo offline**: labores, aplicaciones de insumos, riegos, costos, observaciones con foto y cosechas en kg. Sincroniza al recuperar señal. |
| RF-11 | **Clima**: pronóstico a 7–16 días por parcela y acumulados históricos. |
| RF-12 | **Riesgo climático**: probabilidad y severidad de inundación y sequía por municipio o celda, con explicación de los factores. |
| RF-13 | **Tablero** de la parcela y de la organización con los [indicadores de tecnificación](11-metricas.md). |
| RF-14 | **Asistente agronómico**: preguntas en lenguaje natural respondidas con el contexto de la parcela y fuentes citadas. |

### Diferidos (v2.x)

| ID | Requisito | Por qué se difiere |
|---|---|---|
| RF-15 | **Aptitud de cultivo** por parcela ([ADR-0011](adr/0011-aptitud-de-cultivo.md)) | Requiere cosechas reales registradas en la bitácora para validarse |
| RF-16 | **Control de actuadores** (válvulas) desde la app o automático | Primero hay que demostrar que las recomendaciones son confiables; el contrato MQTT ya lo prevé |
| RF-17 | Exportación de datos anonimizados para investigación | Necesita el acuerdo de uso de datos con las organizaciones |
| RF-18 | Mapas base disponibles sin conexión | Alto costo de almacenamiento en el teléfono |

## Requisitos no funcionales

| ID | Atributo | Requisito |
|---|---|---|
| RNF-01 | **Offline** | Consultar el último estado de las parcelas y usar la bitácora completa sin conexión. Nada de lo registrado offline se pierde. |
| RNF-02 | **Dispositivos** | Usable en Android de gama baja (2 GB de RAM) en 3G. Bundle inicial ≤ 200 KB gzip. LCP ≤ 2,5 s en 3G rápido. |
| RNF-03 | **Accesibilidad en campo** | Contraste WCAG AA o mejor (uso a pleno sol), áreas táctiles ≥ 48 px, texto base ≥ 16 px, iconos con etiqueta. |
| RNF-04 | **Disponibilidad** *(prod)* | API: 99,5 % mensual. Ingesta: el broker acepta lecturas 99,5 % del tiempo; los nodos guardan ≥ 72 h de lecturas si no hay conexión. |
| RNF-05 | **Latencia** | API p95 < 300 ms en lecturas. Alerta crítica: p95 < 2 min desde la lectura hasta el envío de la notificación (en el seminario, hasta la bandeja `/dev/outbox` o el push). |
| RNF-06 | **Integridad de datos** | Ingesta idempotente: una lectura repetida no se duplica. Se guarda el valor crudo y el calibrado. |
| RNF-07 | **Seguridad** | TLS en todo el tráfico. Credenciales únicas por nodo. Autorización por organización en cada consulta. |
| RNF-08 | **Privacidad** *(prod; en el seminario solo hay datos simulados)* | Cumplimiento de la Ley 1581 de 2012 (habeas data): consentimiento explícito y exportación y borrado de los datos personales. |
| RNF-09 | **Costo** | Seminario: USD 0 de infraestructura y ≤ USD 5 de LLM. Producción: un solo VPS y LLM con tope mensual ([02-estimaciones](02-estimaciones.md)). |
| RNF-10 | **Mantenibilidad** | Monolito modular con límites explícitos ([ADR-0002](adr/0002-monolito-modular.md)). Design system único ([ADR-0006](adr/0006-design-system.md)). |
| RNF-11 | **Trazabilidad de modelos** | Toda predicción guarda la versión del modelo. Ningún modelo pasa a producción sin superar su línea base ([ADR-0019](adr/0019-reconstruccion-de-modelos.md), [ADR-0020](adr/0020-protocolo-de-experimentacion-ml.md)). |
| RNF-12 | **Idioma** | Interfaz en español. Código e identificadores en inglés. |
| RNF-13 | **Ejecución local** | El perfil seminario levanta con `docker compose --profile seminar up` en un portátil de 8 GB, sin cuentas pagas. |
| RNF-14 | **Demo reproducible** | Cada escenario (A–E) produce siempre las mismas alertas y recomendaciones, y funciona sin internet salvo el asistente. |

## Requisitos extendidos

- Métricas técnicas y de producto con tablero interno (Grafana) *(prod)*.
- Seguimiento de errores del cliente y del servidor.
- Modo técnico: registrar varias fincas en una sola visita sin conexión.
- Notas de voz en la bitácora (transcripción posterior).

## Fuera de alcance

- LLM local (Ollama): se elimina ([ADR-0007](adr/0007-llm-por-api.md)).
- App nativa: la PWA cubre el caso ([ADR-0005](adr/0005-pwa-offline-first.md)).
- Imágenes de dron y procesamiento satelital propio. Se usan índices ya procesados, como en la v1.
- Marketplace o comercialización de cosechas.

## Escenarios de validación

Vienen de los requisitos de la v1 y ahora se validan con datos medidos:

| Escenario | Condición | Comportamiento esperado |
|---|---|---|
| **A. Estrés hídrico (El Niño)** | Temperatura > 35 °C, humedad relativa < 50 %, lluvia < 10 mm en 30 días, agotamiento de la parcela mayor que RAW (humedad bajo el θ_estrés que se deriva del suelo y la etapa del cultivo, [ADR-0022](adr/0022-estres-hidrico-y-asimilacion.md)) | Alerta de estrés hídrico, recomendación de riego con lámina y tiempo, sugerencia de cobertura (mulch) |
| **B. Riesgo fitosanitario (lluvias)** | Humedad relativa > 85 % sostenida, lluvia continua, suelo saturado | Alerta de riesgo de hongos con la ventana de aplicación preventiva según el pronóstico |
| **C. Nodo caído** | Sin lecturas por 3 intervalos esperados | Alerta de nodo al técnico asignado, no al productor |
| **D. Sin conexión** | El productor registra una cosecha en modo avión | Queda guardada localmente y se sincroniza sin duplicarse al volver la señal |
| **E. Veranillo en secano** | Maíz de secano en floración, sin sensor, 21 días sin lluvia efectiva y sin lluvia en el pronóstico de 7 días; agotamiento mayor que RAW desde el día 16 | Alerta de estrés hídrico abierta por el balance diario, recomendación de secano sin lámina con consejo de conservar la humedad ([06 §10](06-diseno-detallado.md#10-simulador-de-escenarios-perfil-seminario)) |
