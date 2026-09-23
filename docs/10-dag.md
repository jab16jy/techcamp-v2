# 10 — DAGs

Tres grafos acíclicos dirigidos: el orden de **construcción**, el flujo de **datos** en ejecución y la **orquestación** de jobs. El DAG de entrenamiento de modelos está en [08-ml](08-ml.md#pipeline-de-entrenamiento).

## 1. DAG de implementación (épicas)

Una flecha `A → B` significa que B necesita A terminada. En rojo, la **ruta crítica** a la demo del seminario. En gris punteado, lo que queda para **producción futura** ([ADR-0021](adr/0021-perfil-seminario-local.md)).

```mermaid
flowchart LR
  E0[E0 Fundaciones<br/>repo, CI, Compose, esqueletos]
  E1[E1 Design system<br/>+ shell PWA]
  E2[E2 Identidad<br/>auth, orgs, roles]
  E3[E3 Fincas y parcelas<br/>suelo, cultivos, ciclos]
  E4[E4 Telemetría<br/>broker, ingestor, alta QR,<br/>calibración, simulador]
  E5[E5 Clima<br/>celdas + Open-Meteo]
  E6[E6 Riego FAO-56]
  E7[E7 Alertas +<br/>notificaciones]
  E8[E8 Bitácora offline]
  E9[E9 Pantalla de inicio<br/>estado de parcela]
  E10[E10 Riesgo climático<br/>reconstrucción con protocolo]
  E11[E11 Métricas e<br/>índice de tecnificación]
  E12[E12 Asistente]
  E13[E13 LoRaWAN<br/>ChirpStack]
  E14[E14 Operación<br/>backups, monitoreo, DR]
  E16[E16 Simulador de escenarios<br/>A–E + fixtures de clima]
  E15((Demo del<br/>seminario))

  E0 --> E1
  E0 --> E2 --> E3 --> E4 --> E6 --> E9 --> E15
  E0 -.-> E14
  E3 --> E5
  E5 --> E6
  E4 --> E7
  E5 --> E7
  E7 --> E9
  E1 --> E8
  E3 --> E8
  E1 --> E9
  E5 --> E10
  E10 -.reglas de riesgo.-> E15
  E4 --> E16
  E5 --> E16
  E7 --> E16 --> E15
  E8 --> E15
  E6 --> E11
  E7 --> E11
  E8 --> E11 --> E15
  E6 --> E12
  E7 --> E12
  E10 -.-> E12
  E12 --> E15
  E4 -.-> E13

  classDef critical fill:#fde2e1,stroke:#c0392b,stroke-width:2px,color:#000
  class E0,E2,E3,E4,E6,E9,E15 critical
  classDef future fill:#eeeeee,stroke:#999999,stroke-dasharray:4 3,color:#555
  class E13,E14 future
```

| Épica | Resultado verificable | Depende de |
|---|---|---|
| E0 | `docker compose --profile seminar up` levanta Postgres (con sus 3 extensiones), Mosquitto, MinIO, api y web vacíos; CI verde con lint, tipos, tests e import-linter | — |
| E1 | Tokens, primitivos y el catálogo `/dev/ui`; PWA instalable que abre offline | E0 |
| E2 | Ingreso por OTP con el emisor local de JWT (código en consola), `GET /me`, membresías y roles, test de aislamiento entre organizaciones | E0 |
| E3 | Crear finca y parcela dibujando el polígono; autocompletado de suelo; catálogo de cultivos con Kc | E2 |
| E4 | Un **simulador de nodo básico** publica y las lecturas aparecen calibradas en la base y en vivo (SSE) | E3 |
| E5 | ET0, lluvia y pronóstico por celda con degradación `stale` | E3 |
| E6 | Recomendación diaria con `rationale`, con lámina en parcelas con riego y recomendación de secano en las demás; test con los ejemplos numéricos de FAO-56 | E4, E5 |
| E7 | El escenario A dispara una alerta y llega un push (y el SMS simulado a `/dev/outbox`); reintentos y escalamiento probados. Las reglas `flood_risk`/`drought_risk` se activan cuando termina E10 | E4, E5 |
| E8 | El escenario D (cosecha en modo avión) sincroniza sin duplicar | E1, E3 |
| E9 | Pantalla de inicio completa con datos reales del simulador | E1, E6, E7 |
| E10 | Model card, dataset reproducible, harness y escalera de líneas base de M2 (inundación); en producción queda el modelo que pase la compuerta o, si ninguno pasa, la línea base ([ADR-0020](adr/0020-protocolo-de-experimentacion-ml.md)) | E5 |
| E11 | Índice de tecnificación mensual y resumen por ciclo | E6, E7, E8 |
| E12 | Asistente con fuentes citadas, límites y degradación | E6, E7, E10 |
| E16 | Los escenarios A–E corren con un comando, con fixtures de clima grabados, y su bloque `expected` pasa como prueba end-to-end en CI ([06 §10](06-diseno-detallado.md#10-simulador-de-escenarios-perfil-seminario)) | E4, E5, E7 |
| E13 *(producción futura)* | Un nodo LoRa real llega por ChirpStack con el mismo mensaje interno | E4 |
| E14 *(producción futura)* | Backup y restauración ensayados; tableros de SLO | E0 |

**Paralelismo:** después de E3 se pueden trabajar en paralelo E4, E5 y E8. E1 corre en paralelo a E2 y E3 desde el inicio. E10 (reconstrucción del modelo) no bloquea la ruta crítica: la demo puede mostrar alertas de umbral y pronóstico, y sumar las de riesgo si el modelo pasa la compuerta a tiempo. E12 usa las predicciones de riesgo si existen, pero no depende de ellas.

## 2. DAG de datos en ejecución

```mermaid
flowchart LR
  subgraph fuentes [Fuentes]
    s1[Nodos IoT]
    s2[Open-Meteo]
    s3[SoilGrids / laboratorio]
    s4[Bitácora del productor]
    s5[Eventos UNGRD / HDX]
  end
  subgraph almacenados [Datos base]
    r[(reading)]
    w[(weather_daily)]
    sp[(soil_profile)]
    lb[(logbook_entry)]
    cc[(crop_cycle)]
  end
  subgraph derivados [Derivados]
    rh[reading_hourly / daily]
    wb[water_balance_daily]
    rp[risk_prediction]
    cs[crop_cycle_summary]
    pm[plot_metric_monthly]
  end
  subgraph decisiones [Decisiones]
    ir[irrigation_recommendation]
    al[alert]
  end
  subgraph entrega [Entrega]
    ui[PWA / SSE]
    nt[Push / SMS]
    as[Asistente]
  end

  s1 --> r --> rh
  s2 --> w
  s3 --> sp
  s4 --> lb
  s4 --> cc
  rh --> wb
  w --> wb
  sp --> wb
  cc --> wb
  lb --> wb
  w --> rp
  s5 -. entrenamiento .-> rp
  wb --> ir
  r --> al
  w --> al
  rp --> al
  lb --> cs
  rh --> cs
  cs --> pm
  al --> pm
  ir --> pm
  ir --> ui
  al --> ui
  al --> nt
  pm --> ui
  ir --> as
  al --> as
  rp --> as
```

La bitácora alimenta el balance hídrico (riegos registrados) y el resumen del ciclo (cosechas y costos). Sin bitácora no hay métricas de impacto.

## 3. DAG de orquestación diaria (worker)

Horas en `America/Bogota`. Cada job arranca cuando termina el anterior y además tiene hora mínima de inicio; si una dependencia falla, los siguientes no corren y se alerta al equipo.

```mermaid
flowchart LR
  a[03:00 consolidar clima<br/>del día anterior] --> b[04:00 refrescar agregados<br/>diarios de lecturas]
  b --> c[04:30 balance hídrico<br/>por parcela]
  c --> d[recomendaciones de riego]
  d --> e[05:00 push matutino<br/>agrupado por usuario]
  a --> f[06:00 features de riesgo<br/>por celda]
  f --> g[inferencia de riesgo]
  g --> h[evaluar reglas<br/>flood / drought]
  h --> i[outbox de notificaciones]

  subgraph continuos [Continuos]
    k[cada 3 h: pronóstico de celdas activas] --> l[reglas de pronóstico]
    m[cada 5 min: salud de nodos]
    n[cada 5 s: outbox de notificaciones]
  end

  subgraph mensual [Mensual: día 1, 02:00]
    o[resumen de ciclos cerrados] --> p[índice de tecnificación]
  end
```
