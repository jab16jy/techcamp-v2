# Model Card: M2 — Riesgo de Inundación

Ficha del modelo M2 para predicción mensual de riesgo de inundación municipal en el Caribe colombiano, siguiendo el paso 1 del protocolo de experimentación de ML ([ADR-0020](../../../docs/adr/0020-protocolo-de-experimentacion-ml.md)) y las especificaciones de [docs/08-ml.md](../../../docs/08-ml.md).

## 1. Decisión que mejora
¿Me preparo para inundación este mes? (docs/08 §Inventario de modelos, §M2).
Permite al productor y a las organizaciones de productores tomar decisiones preventivas (proteger parcelas, habilitar drenajes, planificar cosecha o mover maquinaria e insumos) frente a la línea base simple de lluvia acumulada.

## 2. Etiqueta / objetivo
1 si hay un evento de inundación reportado en el municipio y el mes en las fuentes oficiales (UNGRD / DesInventar: eventos `INUNDACION`, `CRECIENTE SUBITA` y `AVENIDA TORRENCIAL`). 0 en cualquier otro caso (docs/08 §M2, §Fuentes de datos de M2).

## 3. Unidad
Municipio × mes (docs/08 §M2).
Las features climáticas y topográficas se agregan a nivel de municipio desde el centroide municipal del DANE MGN 2024.

## 4. Región
Los 7 departamentos del Caribe continental colombiano: Atlántico (08), Bolívar (13), Cesar (20), Córdoba (23), La Guajira (44), Magdalena (47) y Sucre (70), que abarcan 195 municipios según el MGN 2024 del DANE (docs/08 §M2 "Región"). El archipiélago de San Andrés, Providencia y Santa Catalina queda excluido por ser insular.

## 5. Horizonte
La predicción para el mes M se emite con datos climáticos e históricos hasta el último día del mes M−1; la etiqueta evalúa la ocurrencia de eventos en el mes M (docs/08 §M2 "Horizonte", docs/06 §8). `horizon_start` corresponde al primer día de M y `horizon_days` a los días del mes M.

## 6. Frecuencia de clase
Se mide en T4 con el dataset (docs/08 §M2 "Negativos"). Todos los municipio-mes sin evento registrado son negativos; no se aplica submuestreo en validación ni en test para preservar la prevalencia real.

## 7. Costo de falsos positivos y falsos negativos
- **Falso positivo (costo cualitativo):** Pérdida de recursos y tiempo al ejecutar medidas preventivas innecesarias, fatiga de alertas y pérdida de confianza en la plataforma. Para mitigar este costo, el uso operativo define la severidad `alto` con un umbral estricto que garantice una precisión mínima ≥ 0,7 en el conjunto de validación (docs/08 §M2 "Uso operativo", §Severidad).
- **Falso negativo (costo cualitativo):** Daño o pérdida total de cultivos, pérdida de insumos o afectación de infraestructura sin preparación previa. Para balancear este costo, el recall operativo obtenido con el umbral de precisión ≥ 0,7 se publica abiertamente, y si ningún modelo supera la compuerta se sirve la mejor línea base explicable (docs/08 §M2, docs/08 §Reglas de gobierno).

## 8. Métrica principal
- **PR-AUC con la frecuencia real:** Área bajo la curva Precision-Recall calculada sobre la prevalencia natural de la clase (docs/08 §M2, docs/11-metricas.md §3).
- **Brier score:** Error cuadrático medio de la probabilidad calibrada respecto al desenlace real (docs/08 §M2, docs/11-metricas.md §3).

## 9. Mejora mínima útil (compuerta de promoción)
Para que un modelo candidato reemplace a la línea base servida en producción, debe superar la compuerta estadística en el conjunto de test bloqueado (ADR-0020 paso 8, docs/08 §Reglas de gobierno):
- Superar a la mejor línea base (climatología o heurística) en PR-AUC con límite inferior del intervalo de confianza del 95 % (IC95 por bootstrap pareado) estrictamente mayor que cero (`CI95_low > 0`).
- El Brier score no puede empeorar respecto al de la mejor línea base.
- Si ningún modelo supera la compuerta, se sirve en producción la mejor línea base de validación registrada (docs/08 §M2 "Línea base servida"). No existe `force_promote`.

## 10. Limitaciones
- **Inundación fluvial y ruptura de diques:** M2 se alimenta exclusivamente de precipitación local acumulada, humedad del suelo y topografía municipal. No modela dinámica hidrológica fluvial ni rupturas de diques o jarillones, que constituyen el principal mecanismo de inundación en zonas bajas de la región (por ejemplo, La Mojana, cuencas de los ríos Cauca, Magdalena, Sinú y San Jorge, como el desastre de Cara de Gato en 2021). En un piloto, las alertas hidrológicas del IDEAM por nivel de río deben operar primero como regla de línea base complementaria (docs/08 §M2 "Limitación", brecha G07).
- **Sesgo de reporte en etiquetas:** Las etiquetas provienen de los consolidados de emergencias de la UNGRD y DesInventar, los cuales exhiben sesgo de notificación: municipios con mayor densidad poblacional, mayor accesibilidad o capacidad institucional tienden a reportar emergencias más consistentemente que municipios rurales dispersos o remotos (docs/08 §M2 "Etiqueta").
