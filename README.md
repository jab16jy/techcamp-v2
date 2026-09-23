# TechCamp v2

Plataforma para tecnificar el campo en el Caribe colombiano. Mide la parcela con sensores IoT y una bitácora que funciona sin señal, convierte esos datos en decisiones diarias (regar, prevenir, cosechar) y demuestra con indicadores que la producción mejoró.

Sucesor de TechCamp v1 (AgroCaribe AI). Ver [ADR-0001](docs/adr/0001-nuevo-repositorio-v2.md).

## Qué diferencia a la v2

| | v1 (AgroCaribe AI) | v2 |
|---|---|---|
| **De dónde salen los datos** | Datos públicos y datasets sintéticos sobre una ubicación | **Datos del propio campo**: sensores de humedad y clima, más la bitácora del productor. Los datos públicos complementan. |
| **Qué recibe el productor** | Un análisis y un "top de cultivos" | **Una decisión diaria sobre el agua**, con el porqué: "hoy riegue 12 mm, unos 40 minutos" en una parcela con riego; en una de secano, cuánta agua le falta al cultivo, la lluvia esperada y qué hacer |
| **Riego** | ET0 constante por cultivo | Balance hídrico **FAO-56** con ET0 diaria real, corregido con la humedad medida; también en parcelas de **secano**, la mayoría en el campo |
| **Conectividad** | Requiere internet | **Offline-first**: la bitácora y el último estado funcionan sin señal; **LoRaWAN** donde no hay cobertura celular |
| **Alertas** | Paneles que hay que ir a mirar | **Avisos que llegan**: push, con escalamiento a SMS/WhatsApp si una crítica no se atiende, sin saturar |
| **Modelos de ML** | Métricas no reproducibles, con fuga de datos; un recomendador con 7 % de acierto real | **Solo se publica un modelo que le gana a la regla simple**, con un protocolo reproducible. El campo tecnificado genera los datos que mejoran los modelos. |
| **Asistente** | Ollama local (4 GB de RAM), respuestas débiles | LLM barato por API que **explica** los datos de la parcela, cita fuentes y nunca inventa dosis |
| **Impacto** | Sin medición | **Índice de tecnificación** y KPIs por ciclo: rendimiento, agua por kg, costo por kg, alertas anticipadas, contra una línea base |
| **Usuarios** | Un usuario analizando ubicaciones | **Organizaciones**: cooperativas y técnicos que gestionan muchas fincas, y productores que entran con su teléfono (OTP) |
| **Diseño** | Pantallas hechas una por una | **Design system primero**, pensado para sol directo, dedos grandes y teléfonos de gama baja |
| **Operación** | Compose con un LLM pesado | Un VPS barato, un solo Postgres y backups continuos: operable por un equipo pequeño |

## Estado

**Proyecto de seminario.** Se ejecuta en local (`docker compose --profile seminar up`) con un simulador de nodos IoT y escenarios reproducibles (sequía de El Niño, lluvias con riesgo de hongos, nodo caído, productor sin señal, veranillo en una parcela de secano). La arquitectura queda lista para pasar a producción si el proyecto crece, cambiando adaptadores y no el dominio ([ADR-0021](docs/adr/0021-perfil-seminario-local.md)).

Fase actual: diseño. Todavía no hay código.

## Empezar por aquí

1. [docs/README.md](docs/README.md): índice del diseño del sistema y orden de lectura.
2. [docs/adr/README.md](docs/adr/README.md): decisiones de arquitectura.
