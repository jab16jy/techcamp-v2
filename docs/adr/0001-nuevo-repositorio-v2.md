# ADR-0001: TechCamp v2 en un repositorio nuevo, rescatando piezas de la v1

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

La v1 (AgroCaribe AI) se construyó pieza por pieza, sin design system ni límites de módulos. El frontend es la parte peor diseñada. El backend tiene piezas valiosas (pipeline de riesgo climático con compuerta de promoción, clientes de clima y suelo, datos EVA/NDVI), pero también piezas que no sirven: recomendador con 7,08 % de acierto real, riego con ET0 constante, dependencia de Ollama. El producto cambia de foco: de "analizar una ubicación" a "tecnificar una parcela con datos medidos".

## Decisión

Crear `techcamp-v2` como repositorio nuevo, en una carpeta hermana. La v1 queda congelada como referencia de solo lectura. Las piezas que se rescatan se **copian** a la v2 con sus tests y el mensaje de commit indica el archivo de origen. Ver la lista de piezas en [08-ml](../08-ml.md).

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Refactorizar la v1 en sitio | Todo el frontend y la mitad del backend cambian: sería una reescritura con el ruido del código viejo alrededor, y se pierde la v1 como punto de comparación de métricas |
| Rama `v2` en el mismo repositorio | No comparten casi nada; los diffs y merges contra `main` serían puro ruido |
| Reescribir sin mirar la v1 | Se perderían el pipeline de riesgo, los datos EVA/NDVI y las lecciones de la validación sintético-real |

## Consecuencias

**Positivas**

- Diseño limpio desde el primer commit (design system, módulos y contratos).
- Las métricas de la v1 se pueden reproducir en la v1 para comparar con la v2.
- El historial de la v1 queda intacto.

**Negativas / costos aceptados**

- Dos repositorios que mantener mientras la v2 no supere a la v1.
- Copiar código exige volver a leerlo y probarlo; es trabajo, pero es el filtro deseado.

## Relacionado

[05-arquitectura](../05-arquitectura.md), [08-ml](../08-ml.md)
