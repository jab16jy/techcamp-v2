# ADR-0020: Protocolo de experimentación de ML con harness fijo, escalera de líneas base y tuning sistemático

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

Los modelos de la v1 fallaron por el método, no por el algoritmo: no había harness de evaluación fijo, había fuga de datos, la frecuencia de positivos era artificial, no había línea base trivial ni búsqueda de hiperparámetros ([08-ml](../08-ml.md#auditoría-de-la-v1)). La v2 va a entrenar modelos con asistencia de agentes de IA, y un agente sin reglas repite esos errores más rápido.

Referencias de las que se toma el método:

| Referencia | Qué se toma |
|---|---|
| [Google Tuning Playbook](https://github.com/google-research/tuning_playbook) | Hiperparámetros científicos, de ruido y fijos; búsqueda cuasialeatoria; cambios incrementales con evidencia |
| [Kaggle Solutions](https://github.com/faridrashidi/kaggle-solutions) | La validación que imita la producción es lo primero; feature engineering antes que modelos más grandes; ensambles al final |
| [AIDE](https://github.com/WecoAI/aideml) y [RD-Agent](https://github.com/microsoft/RD-Agent) | Ciclo hipótesis → experimento → evaluación → registro; separar investigación (qué probar) de desarrollo (cómo implementarlo) |
| [TabPFN](https://github.com/PriorLabs/TabPFN) | Línea base tabular fuerte sin tuning |
| [Context7](https://github.com/upstash/context7) | Documentación vigente de scikit-learn, LightGBM y otras bibliotecas para el agente |

[MLE-bench](https://github.com/openai/mle-bench) **no se usa**: evalúa agentes en competencias de Kaggle, no modelos para las decisiones de TechCamp. Solo tendría sentido si se construyera un agente de ML general.

## Decisión

Todo modelo pasa por estos pasos, en orden. Un paso no se salta.

| # | Paso | Entregable |
|---|---|---|
| 1 | **Model card previa**: decisión que mejora, etiqueta, unidad, frecuencia real de la clase, costo de un falso positivo y de un falso negativo, métrica principal, mejora mínima útil | `ml/models/<nombre>/card.md` |
| 2 | **Dataset reproducible**: script desde fuentes públicas con versión fija, sin pasos manuales; hash y data card | `ml/datasets/<nombre>/` |
| 3 | **Harness fijo** antes de cualquier experimento: partición temporal con brecha ≥ la ventana más larga de las features, grupos espaciales, frecuencia real en val y test, métricas con IC95 bootstrap. El test queda bloqueado. | `ml/harness/` con tests propios |
| 4 | **Escalera de líneas base**: trivial (climatología, prevalencia o persistencia) → regla de dominio → modelo lineal → LightGBM por defecto → TabPFN (si la licencia y el tamaño lo permiten) | Tabla de líneas base en el registro |
| 5 | **Ciclo de experimentos**: una hipótesis y un cambio por experimento, evaluado en validación, registrado aunque falle | `ml/experiments/log.csv` + nota por experimento |
| 6 | **Tuning** según el Tuning Playbook: presupuesto fijo, búsqueda cuasialeatoria sobre los hiperparámetros de ruido, early stopping en validación, nunca sobre test | Configuración y resultados en el registro |
| 7 | **Calibración y umbral** en validación, según el costo de los errores de la model card | Umbral y curva de calibración |
| 8 | **Compuerta en test** (una vez): mejora sobre la mejor línea base con límite inferior del IC95 > 0 y Brier no peor | Reporte de compuerta |
| 9 | **Robustez**: dejar fuera un departamento a la vez, desempeño por temporada y por subgrupo | Reporte de robustez |
| 10 | **Registro** en `model_version` y promoción manual revisada | Fila en `model_version` + artefacto en S3 |

**Reglas para agentes de IA que ejecutan el ciclo:**

- El agente puede proponer y ejecutar experimentos (pasos 5–7).
- El agente **no puede modificar** el harness, el dataset de test ni la compuerta. Esos archivos tienen dueño humano en `CODEOWNERS` y un test que verifica su hash.
- El agente consulta la documentación vigente de las bibliotecas (Context7) antes de usar su API.
- Todo experimento del agente queda en el registro con su hipótesis, aunque no mejore.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Método libre por modelo (como la v1) | Produjo fuga de datos y métricas no confiables |
| Plataforma MLOps completa (MLflow, Kubeflow) | Demasiada infraestructura para 1–3 modelos; un registro en CSV/Markdown y `model_version` alcanzan. Se reevalúa si hay más de 5 modelos activos. |
| Construir un agente AutoML propio (tipo AIDE) | Es un proyecto aparte; se copia el ciclo, no se construye la herramienta |
| AutoML en caja negra (AutoGluon, H2O) como método principal | Útil como línea base adicional, pero sin harness propio sigue permitiendo fuga de datos |

## Consecuencias

**Positivas**

- Métricas comparables entre experimentos y confiables para decidir.
- Los agentes de IA aceleran la iteración sin poder "hacer trampa" con el test.
- Un modelo que no le gana a la regla simple nunca llega al productor.

**Negativas / costos aceptados**

- Más trabajo antes del primer modelo (model card, dataset y harness).
- TabPFN: confirmar licencia (uso comercial) y límites de muestras/features de la versión elegida antes de adoptarlo; si no aplica, la escalera termina en LightGBM.

## Relacionado

[08-ml](../08-ml.md), [ADR-0019](0019-reconstruccion-de-modelos.md), [11-metricas](../11-metricas.md#3-calidad-de-decisión)
