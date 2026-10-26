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
| **Impacto** | Sin medición | KPIs por ciclo (rendimiento, agua por kg, costo por kg, margen, alertas anticipadas) contra la **encuesta de inscripción** de cada parcela, más un **índice de adopción digital** |
| **Usuarios** | Un usuario analizando ubicaciones | **Organizaciones**: cooperativas y técnicos que gestionan muchas fincas, y productores que entran con su teléfono (OTP) |
| **Diseño** | Pantallas hechas una por una | **Design system primero**, pensado para sol directo, dedos grandes y teléfonos de gama baja |
| **Operación** | Compose con un LLM pesado | Un VPS barato, un solo Postgres y backups continuos: operable por un equipo pequeño |

## Estado

**Proyecto de seminario.** Se ejecuta en local con un simulador de nodos IoT y escenarios reproducibles (sequía de El Niño, lluvias con riesgo de hongos, nodo caído, productor sin señal, veranillo en una parcela de secano). La arquitectura queda lista para pasar a producción si el proyecto crece, cambiando adaptadores y no el dominio ([ADR-0021](docs/adr/0021-perfil-seminario-local.md)).

## Correr la demo

El stack del seminario son la base de datos, el broker, el almacén de objetos, la API, el worker, el ingestor y la PWA ([ADR-0021](docs/adr/0021-perfil-seminario-local.md)). Todo se levanta con una receta:

```bash
just stack-rebuild
```

`just stack-rebuild` quita los contenedores del proyecto y los vuelve a levantar con las imágenes de este commit, y espera a que la API conteste `/health`. Detrás hace lo mismo que:

```bash
uvx podman-compose -f infra/compose.yaml --profile seminar down --remove-orphans
uvx podman-compose -f infra/compose.yaml --profile seminar up -d --build
```

Dos cosas que el comando de arriba no hace por ti:

- **`podman-compose` no está instalado** en la máquina de la demo; `uvx podman-compose` corre la misma herramienta desde PyPI. Los docs que digan `docker compose` (docs/05, docs/10) también funcionan si hay Docker.
- **`podman-compose up -d` no recreationa un contenedor sobre una imagen reconstruida**: el contenedor viejo se queda con la imagen vieja y el stack contesta con el código de antes (un `/status` en 500, un ingestor en crash-loop). Por eso la receta borra los contenedores primero; si los levantas a mano, `podman rm -f` antes.

### Los escenarios

Cada escenario se carga con el simulador, que deja la parcela, el ciclo, los nodos y el clima grabado:

```bash
cd server
uv run python -m techcamp.simulator run --scenario dry-spell-rainfed --backfill 21d --live
```

`--live` deja el nodo publicando cada 5 s; sin él, el simulador solo publica el backfill y termina. La lista de escenarios está en [docs/06 §10](docs/06-diseno-detallado.md#10-simulador-de-escenarios-perfil-seminario).

### Entre corridas

El simulador crea una organización por corrida y la parcela se llama igual en todas: una segunda carga del mismo escenario sin limpiar responde "en otra organización". La receta de limpieza:

```bash
just scenario-clean
```

Borra las organizaciones de la base de este worktree y vuelve a sembrar las reglas de alerta de fábrica. **Borra datos**: esa base es la del stack del seminario, no un archivo de alguien.

### Notas operativas

- **El ingestor se mata por PID, no con `pkill -f "techcamp.ingestor"`**: `pkill` deja huérfano el proceso hijo que el ingestor lanza, y ese hijo sigue publicando. Búscalo y mátalo a mano:

  ```bash
  pgrep -af "techcamp.ingestor"     # el PID del padre
  kill <PID>                          # y el hijo que aparezca en la lista
  ```

- **El balance hídrico necesita el clima observado del día anterior**: `POST /dev/jobs/irrigation:run` responde `skipped` y lo dice cuando ese día todavía no está consolidado. Corre `POST /dev/jobs/weather:run` primero.
- **La profundidad de raíz no sale de SoilGrids**: `POST /plots/{id}/soil:autofill` deja `root_depth_cm` vacío y lo avisa. Es la profundidad del cultivo, y el balance no corre sin ella: `PUT /plots/{id}/soil`.

## Empezar por aquí

1. [docs/README.md](docs/README.md): índice del diseño del sistema y orden de lectura.
2. [docs/adr/README.md](docs/adr/README.md): decisiones de arquitectura.
