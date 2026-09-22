# Validación del diseño de TechCamp v2 frente a la realidad de la tecnificación del campo en el Caribe colombiano

- **Fecha de la investigación:** 2026-09-22
- **Alcance:** diseño documental del repositorio `techcamp-v2` (rama `docs/seminar-alignment`); sin código.
- **Estado:** evidencia. Es el *porqué* de los cambios de diseño; cada cambio se decide en un ADR o en el documento afectado, citando el ID de brecha (G01–G28).
- **Validación independiente:** se comprobaron contra el diseño las líneas citadas en G01–G08, G11 y G15; se recalculó el umbral de G02 (0,23 − 0,55 × 0,14 ≈ 15,3 %); se leyó el Boletín 6 del CNA 2014 (33,3 % de UPA con cultivos usa riego, p. 10) y la Res. ANE 000028 del 26-01-2026. El resto conserva las etiquetas de evidencia del investigador.
- **Advertencia:** las referencias `archivo:línea` corresponden al diseño a esta fecha y pueden desplazarse con ediciones posteriores.
- **Lentes:** (a) seminario local con simulador (`docs/adr/0021-perfil-seminario-local.md`); (b) piloto real con productores.
- **Etiquetas de evidencia:** **VERIFICADO** (fuente primaria leída), **VERIFICADO-R** (resumen indexado de una fuente primaria; el texto completo no se pudo abrir), **SUPUESTO** (sin fuente primaria o inferencia propia), **CONTRADICCIÓN** (las fuentes no coinciden), **VACÍO** (no se encontró evidencia).
- **Citas de diseño:** `archivo:línea` relativas a `docs/` salvo `README.md` en la raíz.

---

## 1. Resumen ejecutivo

**Veredicto: el diseño está parcialmente apegado a la realidad.** La ingeniería es sólida y el perfil de seminario es honesto sobre lo que demuestra. Varias decisiones están bien respaldadas: offline-first, calibración de sensores, FAO-56 de coeficiente único, un LLM que no calcula dosis, organizaciones y técnicos como usuarios, y gobierno de modelos. Pero el **modelo agronómico y de impacto** tiene cuatro desajustes estructurales con el pequeño productor del Caribe:

1. **Está centrado en el riego, y la mayoría del campo es de secano.** Solo el 33,3 % de las UPA con cultivos usa algún tipo de riego (CNA 2014), y los departamentos del Caribe no están entre los de mayor uso. La "decisión diaria" del producto supone un sistema de riego con caudal conocido.
2. **La lógica de estrés hídrico es agronómicamente incorrecta.** Usa un umbral volumétrico fijo por cultivo en lugar de `Dr > RAW` por parcela y suelo. Además trata un sensor capacitivo barato a 10 cm como la verdad de toda la zona de raíces. Con los valores de FAO-56, el propio escenario A dispara la alerta antes de que haya estrés.
3. **Las métricas de "tecnificación" miden el uso de la plataforma, no la adopción de prácticas ni el impacto.** La "brecha de rendimiento" contra la media EVA municipal no es un estimador de impacto. Además, al modelo de datos le faltan campos que las métricas necesitan: precio de venta, línea base, jornales y vínculo entre pérdida y alerta.
4. **Falta el marco institucional real.** No hay flujo de extensión según la Ley 1876 (visitas, registro y clasificación de usuarios, PDEA). No se usan las fuentes oficiales de riesgo: ENSO y Mesas Técnicas Agroclimáticas, alertas hidrológicas del IDEAM y zonificación de aptitud de la UPRA. En La Mojana, las inundaciones son fluviales y el modelo M2 solo usa lluvia local.

Ninguno de los cuatro exige cambiar la arquitectura: son cambios de dominio, datos y métricas. Los puntos 2 y 3 **bloquean una implementación correcta del seminario** (alertas, simulador y E11). Los puntos 1 y 4 son imprescindibles para un piloto y conviene reflejarlos ya en el simulador y en los escenarios.

---

## 2. Plan de investigación y método

### 2.1 Preguntas

| ID | Pregunta | Qué podría cambiar en el diseño |
|---|---|---|
| RQ1 | ¿Qué implica tecnificar al pequeño productor del Caribe y cuál es el marco institucional (Ley 1876, ADR, AGROSAVIA, ICA, FINAGRO, UPRA, IDEAM)? | Alcance funcional, usuarios, integraciones |
| RQ2 | ¿Qué dice la evidencia sobre agricultura digital e IoT para pequeños productores (barreras, fallas, qué funciona)? | Canales, rol del técnico, UX de baja alfabetización |
| RQ3 | ¿Se sostienen los supuestos técnicos (FAO-56 y Kc, sensores capacitivos, SHT31, bandas LoRaWAN, calidad de Open-Meteo/ERA5/CHIRPS, etiquetas de riesgo UNGRD, conectividad rural)? | Reglas de alerta, balance hídrico, simulador, hardware |
| RQ4 | ¿Las métricas de impacto, adopción y tecnificación coinciden con marcos establecidos (ODS 2.3.1, 2.3.2, 2.4.1 y 6.4.1; productividad del agua de FAO; clasificación de usuarios de la Ley 1876)? ¿Son medibles? | `11-metricas.md`, modelo de datos, E11 |
| RQ5 | ¿Las estimaciones de `02-estimaciones.md` se apoyan en cifras reales (CNA 2014, ENA)? | Supuestos de escala y costo |
| RQ6 | ¿Qué necesita la tecnificación real que el diseño no tiene o tiene mal? | Brechas de requisitos |

### 2.2 Fuentes previstas por pregunta

- **RQ1:** texto de la Ley 1876 (Función Pública); ADR (registro de usuarios); MADR (Res. 407 de 2018); FAO, Alianza Bioversity-CIAT y AGROSAVIA (Mesas Técnicas Agroclimáticas); UPRA (SIPRA, EVA); FINAGRO (ISA); ICA (BPA).
- **RQ2:** Fabregas, Kremer y Schilbach (Science, 2019); Cole y Fernando (Economic Journal, 2021); revisiones sobre IoT para pequeños productores.
- **RQ3:** FAO-56 (caps. 6 y 8); estudios revisados por pares sobre sensores capacitivos (Sensors, 2025–2026); hoja de datos Sensirion SHT3x; ANE (Res. 105 de 2020 y 28 de 2026); documentación de Open-Meteo; validaciones de ERA5 y CHIRPS en Colombia; IDEAM (alertas hidrológicas); UNGRD (datos abiertos); DANE ENTIC 2024.
- **RQ4:** metadatos ODS de ONU/FAO; FAO-56; Ley 1876 y método de clasificación de usuarios.
- **RQ5:** boletines del CNA 2014 del DANE y ENA 2023.
- **RQ6:** síntesis de RQ1–RQ5 contrastada con el modelo de datos, la API y los requisitos.

### 2.3 Estándar de evidencia y método

- Cada afirmación material se remite a la fuente que la produce: estadística oficial, norma, informe de agencia, artículo revisado por pares o hoja de datos.
- Las noticias y los blogs se usan solo como pista y se marcan como tales.
- Los PDF del DANE se descargaron y se leyeron con `pdftotext`. El texto de la Ley 1876 se descargó del Gestor Normativo de Función Pública.
- **Limitaciones de herramientas:**
  - MDPI, Science, PubMed, OCHA y Función Pública (vía WebFetch) devolvieron 403 o errores de certificado. En esos casos se usó el resumen indexado, marcado **VERIFICADO-R**.
  - La página de la Ley 1581 en `secretariasenado.gov.co` rechazó la conexión.
  - El manual del MADR sobre clasificación de usuarios devolvió HTML en vez del PDF.
  - No se pudieron consultar las normales climatológicas del IDEAM (visor interactivo).
- Los repositorios no se modificaron. Los PDF de apoyo no se versionan; las fuentes están enlazadas en la sección 7.

---

## 3. Hallazgos por pregunta

### RQ1 — Dimensiones de la tecnificación y marco institucional

**H1.1 — La extensión agropecuaria es un servicio público con un enfoque de cinco aspectos, y la tecnificación se evalúa sobre esos aspectos.** **VERIFICADO.**

- La Ley 1876 de 2017, art. 25, define el enfoque de la extensión en cinco aspectos:
  1. capacidades humanas integrales (técnico-productivas, administrativas, financieras, informáticas y de comercialización);
  2. capacidades sociales y asociatividad;
  3. acceso a información, tecnologías y TIC;
  4. gestión sostenible de recursos naturales (uso eficiente del agua y el suelo, adaptación al cambio climático);
  5. participación y autogestión.
- El art. 24 hace de la extensión un servicio público prestado por las EPSEA en el marco de un PDEA.
- El art. 31 obliga a inscribir a los productores en un **registro de usuarios**.
- El art. 30 le ordena al MADR definir la **clasificación de usuarios** [S7].
- La ADR administra la "herramienta de registro y clasificación" y califica "cada uno de los componentes y aspectos del enfoque" (procedimiento PR-SPE-002 v1, 22/07/2020) [S8].
- La clasificación va de 1 a 4 por aspecto y se agrega con media geométrica (manual operativo del MADR; Res. 407 de 2018) [S9]. **VERIFICADO-R.**
- La extensión incluye expresamente la poscosecha, la comercialización, el acceso al crédito y la certificación BPA (art. 2, definición) [S7].
- **Implicación para el diseño:** el "índice de tecnificación" de `11-metricas.md:38` no se relaciona con este marco oficial. Además, `01-requisitos.md:14` menciona "registrar visitas", pero no existe una entidad de visita en `03-modelo-datos.md`.

**H1.2 — Sin técnico no hay tecnificación: la cobertura de asistencia técnica y de crédito es baja.** **VERIFICADO.**

- CNA 2014 (UPA del área rural dispersa) [S3]:
  - el 16,5 % recibió asistencia técnica, y en el 86,6 % de esos casos el tema fue BPA;
  - el 10,7 % solicitó crédito (con 88,4 % de aprobación);
  - el 16,4 % tiene maquinaria.
- El técnico está previsto como usuario (`01-requisitos.md:14`), pero el diseño lo reduce a instalar y calibrar nodos y a recibir alertas de nodos.

**H1.3 — La región depende de la información agroclimática estacional (ENSO) y ya existe una institucionalidad que la produce.** **VERIFICADO-R.**

- Las Mesas Técnicas Agroclimáticas (MTA) nacieron en 2014 con CIAT. Hoy son 27 mesas y han publicado más de 300 boletines.
- El convenio MADR–FAO las amplió a 8 mesas, 36 cultivos y 631.000 productores [S20].
- Hay mesas en Córdoba, Sucre y en Magdalena–Cesar–La Guajira–Atlántico [S20].
- El Boletín Agroclimático Nacional es mensual (MADR, FAO e IDEAM) [S20].
- **Implicación:** el diseño no usa estas fuentes, ni como corpus del asistente (RAG) ni como señal de riesgo. El escenario "El Niño" (`01-requisitos.md:87`) es un evento local, no un pronóstico estacional.

**H1.4 — Las inundaciones más dañinas de la región son fluviales y dependen de diques.** **VERIFICADO-R** (el mecanismo es consistente entre fuentes; las cifras vienen de prensa y de OCHA vía resumen).

- En La Mojana, la ruptura del dique de Cara de Gato (Caregato) sobre el río Cauca, el 27 de agosto de 2021, afectó a Sucre, Bolívar, Córdoba y Antioquia. Según la UNGRD citada en prensa, hubo cerca de 300.000 ha afectadas [S22].
- El IDEAM publica un Boletín de Alertas Hidrológicas y pronóstico hidrológico diario para las cuencas Magdalena–Cauca, Sinú y San Jorge [S21].
- **Implicación:** las variables de M2 (`08-ml.md:55`: lluvia acumulada local, humedad de suelo, pendiente y elevación) omiten el motor principal del riesgo en las zonas bajas: el nivel de los ríos aguas arriba y el estado de los diques.

**H1.5 — Existe una zonificación oficial de aptitud.** **VERIFICADO-R.**

- La UPRA publica en SIPRA zonificaciones de aptitud a escala 1:100.000 para maíz tradicional, yuca, plátano, arroz, cacao, fríjol y otros cultivos. Se lanzó en Sucre para la cadena del maíz tradicional (2023) [S24].
- **Implicación:** las "restricciones duras" de `adr/0011-aptitud-de-cultivo.md:12` deberían empezar por la capa UPRA antes de reglas propias.

**H1.6 — Los cultivos del pequeño productor del Caribe incluyen ñame, yuca, maíz, plátano y arroz.** **VERIFICADO-R.**

- Bolívar, Córdoba y Sucre aportan cerca del 38 %, 35 % y 14 % del ñame nacional; unas 30.000 familias dependen de su venta (MADR) [S30].
- El ñame **no aparece en la Tabla 12 de FAO-56** [S2] (**VERIFICADO**).

**H1.7 — Hay seguro agropecuario subsidiado y precios públicos.** **VERIFICADO-R.**

- El Incentivo al Seguro Agropecuario (ISA) subsidia hasta el 85 % de la prima para pequeños productores de bajos ingresos y un 5 % adicional para mujeres, jóvenes y comunidades NARP, entre otros (2025–2026) [S26].
- El SIPSA del DANE publica precios mayoristas diarios, semanales y mensuales por plaza [S31].
- El diseño excluye el marketplace (`01-requisitos.md:79`), lo cual es razonable, pero tampoco usa precios de referencia para el margen.

**H1.8 — Las BPA del ICA exigen registros de aplicación.** **VERIFICADO-R.**

- La Res. ICA 082394 de 2020 regula la certificación BPA de predios con lista de chequeo y criterios [S27].
- Que esa lista exija el producto, su registro ICA, la dosis, el objetivo, el responsable y el periodo de carencia es un **SUPUESTO** basado en la práctica BPA: no se leyó el anexo.
- La bitácora (`03-modelo-datos.md:199`) solo tiene `kind=input` con `quantity` y `unit`. No permitiría ese registro.

### RQ2 — Evidencia sobre agricultura digital e IoT para pequeños productores

**H2.1 — El asesoramiento digital funciona, pero con efectos modestos.** **VERIFICADO-R.**

- En un metaanálisis, la información agrícola por celular aumentó el rendimiento un 4 % y la probabilidad (odds) de adoptar insumos recomendados un 22 % (Fabregas, Kremer y Schilbach, Science 366(6471), 2019) [S18].
- Un servicio de voz con preguntas y respuestas de agrónomos y extensionistas (Avaaj Otalo, India) cambió prácticas y fuentes de información. Rindió unos USD 10 por dólar invertido y el efecto se difundió a los vecinos (Cole y Fernando, EJ 131(633), 2021) [S19].
- **Implicación:**
  - un objetivo de impacto verosímil está en unidades de porcentaje, no en saltos grandes;
  - la voz y la consulta a un humano son canales probados para baja alfabetización. El diseño difiere las notas de voz (`01-requisitos.md:72`) y se apoya en un LLM de texto.

**H2.2 — El IoT de humedad de suelo en pequeños productores falla sobre todo por costo, mantenimiento, calibración y soporte, no por el software.** **VERIFICADO-R.**

- Revisiones en Sensors (PMC9101116) y otras señalan como barreras la durabilidad, la capacitación, el mantenimiento, el costo y la falta de soporte técnico [S32].
- **Implicación:** el costo total de un nodo instalado y mantenido, y el tiempo del técnico, no están en `02-estimaciones.md`. La nota de `02-estimaciones.md:19` (USD 25–30) es el costo de la lista de materiales de una demo.

**H2.3 — Conectividad y teléfono: el offline-first está justificado.** **VERIFICADO.**

ENTIC Hogares 2024 (DANE, 1 de agosto de 2025) [S6]:

| Indicador | Centros poblados y rural disperso | Total nacional |
|---|---|---|
| Hogares con internet | 41,9 % | 65,6 % |
| Conexión móvil entre los hogares rurales conectados | 74,6 % | — |
| Calificación "mala" del servicio de internet | 45,3 % | 25,5 % |
| Personas de 5 años o más con celular | 65,4 % | 78,1 % |
| Internet por celular entre quienes usan internet | 98,1 % | — |
| Mensajería instantánea entre quienes usan internet | — | 87,3 % |

- El desglose rural de la mensajería instantánea es un **VACÍO**.
- **Implicación:**
  - **respalda** `adr/0005` (PWA offline-first) y `01-requisitos.md:53` (RNF-02);
  - **pone en duda** que cada productor tenga su propio smartphone (`README.md:19`, OTP por teléfono). En un piloto hará falta un modo mediado por el técnico o por un teléfono compartido del hogar.

**H2.4 — Alfabetización y perfil del productor.** **VERIFICADO.**

- CNA 2014, productores residentes [S4]:
  - el 16,8 % de los de 15 años o más no sabe leer ni escribir; La Guajira concentra el 13,3 % de ellos;
  - el 19,2 % no tiene ningún nivel educativo y el 57,4 % solo tiene básica primaria;
  - el 36,4 % de los productores residentes son mujeres.
- **Implicación:** el "lenguaje simple" de `07-frontend-design-system.md:14` no basta. Hacen falta iconos, voz y mediación humana, y conviene desagregar por sexo (la Ley 1876 exige enfoque diferencial) [S7].

### RQ3 — Supuestos técnicos

**H3.1 — FAO-56: el método elegido es correcto, pero la implementación tiene tres desvíos.** **VERIFICADO** [S1][S2].

- **(a) Umbral de estrés.**
  - FAO-56 define el estrés cuando `Dr > RAW`, con `RAW = p·TAW` y `TAW = 1000(θFC − θWP)·Zr` (ecs. 82–84). Recomienda ajustar `p = p_tabla + 0,04(5 − ETc)`.
  - El diseño usa un `crop.stress_threshold_pct` volumétrico fijo por cultivo (`03-modelo-datos.md:108`, `06-diseno-detallado.md:118`), y el ejemplo de `06-diseno-detallado.md:93` usa 20 %.
  - Ejemplo con valores de FAO-56 (Tabla 19): un franco arenoso tiene θFC ≈ 0,23 y θWP ≈ 0,09. Para maíz (p = 0,55), el umbral de estrés es θ ≈ 0,23 − 0,55 × 0,14 ≈ **0,153**.
  - Con la regla de 20 %, la alerta del escenario A (`06-diseno-detallado.md:332-343`, trayectoria de 28 a 14 %) se abre cerca de θ = 0,20, **antes de que haya estrés según FAO-56**.
  - En una arcilla (θFC ≈ 0,37, θWP ≈ 0,27), el 20 % está **por debajo del punto de marchitez**. La regla nunca avisaría a tiempo.
  - **Veredicto: contradicho.**
- **(b) Lluvia efectiva.**
  - FAO-56 usa `(P − RO)` en la ec. 85 y dice que la lluvia diaria menor de ~0,2·ET0 "se evapora completamente y puede ignorarse".
  - La regla `Pe = 0,8·P si P > 5 mm` (`06-diseno-detallado.md:171`) no es de FAO-56: se parece a los métodos mensuales de porcentaje fijo (tipo CROPWAT).
  - El recorte `clamp(…, 0, TAW)` sí equivale a la percolación profunda.
  - **Veredicto: parcial.** Es aceptable si se documenta el origen.
- **(c) Kc para los cultivos locales.**
  - Maíz de grano: 0,3 / 1,20 / 0,60–0,35.
  - Yuca de primer año: 0,3 / 0,80 / 0,30.
  - Banano de primer año, como aproximación para plátano: 0,50 / 1,10 / 1,00.
  - El ñame no está en la tabla.
  - Profundidades de raíz (Tabla 22): maíz 1,0–1,7 m; yuca de primer año 0,5–0,8 m.
  - **Implicación:** el catálogo debe guardar el origen de cada Kc (FAO-56 o local) y un estado "sin Kc validado". Para el ñame no se debería recomendar riego sin validación agronómica. `README.md` (docs) ya reconoce que falta validar los Kc (`docs/README.md:70`).

**H3.2 — Un sensor capacitivo barato no es la verdad de la zona de raíces.** **VERIFICADO.**

- Strypsteen et al. (Sensors, 2026) [S13]:
  - RMSE con calibración de laboratorio por suelo: 0,002–0,022 m³/m³;
  - RMSE al llevar esa calibración al campo: **0,055–0,191 m³/m³**;
  - RMSE con calibración de campo: 0,005–0,036 m³/m³; con calibración de un punto: 0,006–0,078 m³/m³;
  - el sensor pierde desempeño con humedad alta (por encima de ~0,25–0,30) y se recomienda solo para suelos con menos de 35 % de arcilla y no salinos;
  - la variabilidad entre unidades es **baja**.
- Abdelmoneim et al. (Sensors, 2025, sensor DFRobot SEN0193) [S14]:
  - RMSE de 4,5–4,9 % tras calibrar por suelo;
  - coeficiente de variación entre unidades de **6,5–16 %**.
- **CONTRADICCIÓN:** los dos estudios no coinciden sobre la variabilidad entre unidades. Una posible política es calibrar por tipo de suelo, ajustar con un punto por unidad en campo y medir la dispersión en el piloto.
- **Implicaciones:**
  - Reemplazar el `Dr` modelado por el observado (`06-diseno-detallado.md:173,182`; `adr/0009-riego-fao56.md:14`) traslada al balance todo el error del sensor. Además, el escenario mide a 10 cm (`06-diseno-detallado.md:337`), mientras la raíz del maíz llega a 1,0–1,7 m.
  - El objetivo "error de humedad < 5 puntos de % volumétrico" (`11-metricas.md:67`) queda **dentro del error del sensor** si no hay calibración de campo.
  - Los vertisoles arcillosos de los valles del Sinú y de La Mojana y los suelos salinos de La Guajira son justamente las condiciones donde el sensor rinde peor. Es un **SUPUESTO** sobre los suelos: conviene verificarlo con SoilGrids o con los datos de AGROSAVIA que el diseño ya reutiliza (`08-ml.md:28`).

**H3.3 — El SHT31 opera fuera de su rango ideal en el clima del Caribe.** **VERIFICADO-R.**

- Hoja de datos de Sensirion [S15]:
  - precisión de ±2 % HR;
  - rango normal recomendado de 20–80 % HR;
  - la exposición prolongada a más de 80 % HR puede desplazar la señal (**+3 % HR tras 60 h**), con recuperación lenta.
- La regla `fungal_risk` (`06-diseno-detallado.md:121`, HR > 85 % durante 10 h o más) y el escenario B trabajan justo en esa zona.
- **Implicación:**
  - para el piloto: abrigo de radiación, verificación periódica y quizá SHT4x;
  - para el seminario: el simulador debería modelar esa deriva para que la demo de "robustez" (`06-diseno-detallado.md`, reglas del simulador) sea realista.

**H3.4 — LoRaWAN en Colombia: el plan compatible es AU915.** **VERIFICADO / CONTRADICCIÓN parcial.**

- La Res. ANE 105 de 2020 fija las bandas de uso libre en su Anexo 1 [S10]. Las fuentes secundarias de cumplimiento normativo ubican LoRa en 915–928 MHz con un campo de 50 mV/m a 3 m [S12].
- La Res. ANE 28 de 2026 (26 de enero de 2026) adopta un plan de 896–915 / 941–960 MHz para servicios móviles y fijos [S11].
- Hay contradicción sobre si el uso libre en 902–915 MHz sigue vigente: CSA Group y el blog Bixtia dicen que la ventana se reduce a 915–928 MHz; el resumen del normograma no encuentra una derogación expresa.
- AU915-928 cae entero en 915–928 MHz y **cumple con cualquiera de las dos lecturas**; US915 transmite desde 902,3 MHz. Los canales de AU915 vienen de los parámetros regionales de LoRaWAN, que **no se leyeron**: **SUPUESTO**.
- **Implicación:** `02-estimaciones.md:139` y `adr/0004-mqtt-y-lorawan.md:38` pueden resolver ya "AU915, no US915" y citar la Res. 28 de 2026.

**H3.5 — Datos climáticos: Open-Meteo sirve, con matices.** **VERIFICADO-R.**

- Open-Meteo ofrece ERA5 a 0,25°, ERA5-Land a 0,1° y el análisis IFS a 9 km. Calcula `et0_fao_evapotranspiration` con Penman-Monteith [S16].
- La API gratuita es para uso no comercial, con un límite de 10.000 llamadas por día y licencia CC-BY 4.0 [S16]. **Respalda** `02-estimaciones.md:69`.
- ERA5-Land estima bien la radiación y la temperatura, pero tiene sesgos sistemáticos en viento y humedad relativa, las variables que más pesan en ET0 [S17b].
- En Colombia, CHIRPS supera a los reanálisis en lluvia, y los productos rinden mejor a escala mensual que diaria [S17].
- **Implicación:** para el balance diario debe mandar el pluviómetro de la parcela sobre la celda; la precedencia no está definida en `06-diseno-detallado.md:171`. Para el piloto, conviene comparar la ET0 contra una estación del IDEAM cercana. En el seminario, los fixtures fijan los datos y no afecta.

**H3.6 — Etiquetas de riesgo.** **VERIFICADO-R.**

- La UNGRD publica "Emergencias UNGRD" en datos.gov.co [S23]. Sirve como etiqueta de municipio y mes, con el sesgo de reporte que el diseño ya reconoce (`08-ml.md:53`).
- **Implicación:** la métrica "anticipación en horas" (`11-metricas.md:65`) no se puede medir con una predicción mensual por municipio (`08-ml.md:52`). Es una **inconsistencia interna**.

**H3.7 — PWA offline.** **Respaldado** por H2.3.

- `adr/0005-pwa-offline-first.md:31` ya documenta los límites de iOS.
- Para Android de gama baja no se encontró evidencia específica: **VACÍO**, bajo riesgo.

### RQ4 — Métricas

**H4.1 — El "índice de tecnificación" mide el uso del sistema, no la tecnificación.** **Análisis sobre el diseño**, contrastado con [S7][S9].

Sus cuatro componentes (`11-metricas.md:38-46`) tienen estos problemas:

- **`monitoring`:** mide que el nodo transmita (`11-metricas.md:43`), no la conducta del productor. Una parcela sin sensor no puede pasar de 75 puntos.
- **`decision`:** no se puede aplicar en secano (H5.1), y supone que el riego se anota en mm (`11-metricas.md:45`), cuando el productor mide en minutos o en turnos.
- **`risk_management`:** mide el reconocimiento de la alerta (el "ack"), no la acción tomada (`11-metricas.md:46`).
- Ningún componente cubre los aspectos 1, 2 y 5 de la Ley 1876, ni las prácticas adoptadas: mulch, preventivo aplicado dentro de la ventana, BPA, asociatividad.

**Recomendación:** renombrarlo "índice de uso de la plataforma" o "adopción digital", y agregar un indicador aparte de **prácticas adoptadas**, alineado con los cinco aspectos. Si el programa lo tiene, registrar el nivel de clasificación de usuario del MADR (1–4) al inicio y al cierre como resultado externo.

**H4.2 — "Brecha de rendimiento" está mal nombrada y no mide impacto.** **VERIFICADO / análisis.**

- En la literatura, la brecha de rendimiento es la distancia entre el rendimiento potencial (o limitado por agua) y el real.
- `11-metricas.md:22` compara con la media EVA municipal de 3 años. Las EVA son datos que consolidan los municipios desde reportes de actores del sector [S25]; no son una muestra comparable.
- Los adoptantes se autoseleccionan, así que la diferencia contra la media no es el efecto de la tecnología.
- **Recomendación:** llamarla "rendimiento relativo a la referencia municipal" y medir impacto con antes y después contra la línea base propia (`11-metricas.md:31`). En un piloto, usar diferencias en diferencias con parcelas de control.

**H4.3 — Las métricas de impacto no se pueden calcular con el modelo de datos actual.** **VERIFICADO** (lectura del diseño).

| Métrica | Qué necesita | Qué hay |
|---|---|---|
| Margen bruto (`11-metricas.md:28`) | "Precio registrado en la cosecha" | `logbook_entry` no tiene precio (`03-modelo-datos.md:194-211`) |
| Pérdidas por evento (`11-metricas.md:29`) | "Observaciones ligadas a alertas" | No hay `alert_id` en la bitácora |
| Línea base (`11-metricas.md:31`) | Una encuesta de inscripción | No existe esa entidad (solo `model_version.baseline_metrics`, `03-modelo-datos.md:265`) |
| Eficiencia de riego "configurable por parcela" (`06-diseno-detallado.md:196`) | Tipo de sistema o eficiencia | `plot` solo tiene `system_flow_lph` (`03-modelo-datos.md:92`) |
| Costo por kg (`11-metricas.md:27`) y ODS 2.3.1 | Jornales, incluida la mano de obra familiar | No hay jornales |

- Sin jornales, el costo por kg subestima el costo real del pequeño productor. El ODS 2.3.1 se define como producción por día trabajado [S29].

**H4.4 — Alineación con marcos establecidos.** **VERIFICADO-R** [S29].

- **ODS 2.3.1:** volumen de producción por día de trabajo. Se puede medir si la bitácora registra jornales.
- **ODS 2.3.2:** ingreso anual, igual a ingresos menos costos operativos y depreciación. El "margen bruto" se le acerca si se agrega el precio.
- **ODS 2.4.1 y 6.4.1:** son indicadores nacionales. El 6.4.1 se mide en USD por m³ y no aplica a la parcela.
- **Productividad del agua (FAO):** "kg por m³" es correcto si se llama "productividad del agua de riego". Conviene agregar la productividad del agua consumida (kg por m³ de ETc), que también sirve en secano.
- **Definición de pequeño productor en los ODS:** el 40 % inferior en tierra y en ingreso. Es útil para segmentar los resultados del piloto.

**H4.5 — Calidad de decisión: métricas sin verdad de referencia definida.**

- "Precisión de alertas: alertas con evento confirmado / alertas" (`11-metricas.md:66`) no dice qué confirma un `water_stress` o un `fungal_risk`.
- En el seminario, el simulador **conoce la verdad**: la trayectoria y el `expected`. Puede emitir etiquetas de verdad y así medir la precisión y el recall de cada regla y el error del balance contra la verdad.
- Esa sería la métrica más fuerte y más honesta que el seminario puede mostrar.

### RQ5 — Estimaciones

**H5.1 — El dimensionamiento técnico es coherente, pero los supuestos de escala no citan fuentes.** **Parcial.**

- Hay pequeños productores de sobra para 5.000 fincas en 3 años: el 63,5 % de los productores residentes tiene UPA de menos de 5 ha y ocupa el 4,2 % del área (CNA 2014) [S4]. La proporción de UPA de menos de 5 ha ronda el 70 % en las fuentes consultadas [S5] (**VERIFICADO-R**).
- No se obtuvo el conteo de UPA por departamento del Caribe: **VACÍO** (los microdatos del CNA están en microdatos.dane.gov.co).
- **SUPUESTO:** DAU del 30 % (`02-estimaciones.md:34`). No hay fuente, pero sobreestimar la carga es conservador.
- **SUPUESTO:** 1,25 nodos por parcela (`02-estimaciones.md:32` frente a la línea 31) y 2 usuarios por finca. Son razonables.
- **Brecha:** el supuesto que más cambia la viabilidad no está en el documento. Es el **costo por nodo instalado** (con panel, gabinete, pluviómetro, radio, SIM o gateway y reposición) y **quién lo paga**. A 10.000 nodos, cada USD 100 por nodo suma USD 1 M.
- **Implicación de H1.2 y del CNA:** el 33,3 % de UPA con riego, concentrado en otras regiones [S3], sugiere que la recomendación de riego diaria aplica a una **minoría** de las parcelas del Caribe. Las estimaciones de push y SMS (`02-estimaciones.md:65-66`) no cambian, pero la propuesta de valor sí.

### RQ6 — Requisitos que faltan o están mal

La matriz de la sección 4 los consolida. En resumen:

- **Modo de secano:** ventana de siembra según el pronóstico estacional y ENSO, déficit hídrico acumulado, cosecha de agua y mulch.
- **Flujo del extensionista:** entidad de visita, plan de acompañamiento, registro y clasificación de usuarios, reporte agregado para la EPSEA o el PDEA.
- **Fuentes oficiales:** MTA y ENSO, alertas hidrológicas del IDEAM, UPRA, SIPSA.
- **Bitácora apta para BPA y trazabilidad.**
- **UX para baja alfabetización y teléfono compartido.**
- **Gobierno de datos con las organizaciones:** acuerdo de uso, titularidad, transferencia internacional al proveedor del LLM y de autenticación, y desagregación por sexo y edad con minimización de datos.

---

## 4. Matriz de brechas

| ID | Afirmación del diseño (archivo:línea) | Evidencia | Veredicto | Cambio recomendado | Prioridad | Aplica a |
|---|---|---|---|---|---|---|
| G01 | El estrés hídrico es "humedad < `crop.stress_threshold_pct`", un umbral volumétrico fijo por cultivo (`03-modelo-datos.md:108`, `06-diseno-detallado.md:93`, `06-diseno-detallado.md:118`; métrica en `11-metricas.md:25`) | FAO-56 ecs. 82–84 y Tablas 19 y 22 [S1]: el umbral depende del suelo; 20 % está por encima del umbral de estrés en franco arenoso y por debajo del punto de marchitez en arcilla | contradicho | Derivar el umbral por parcela, `θ_estrés = θFC − p·(θFC − θWP)`, con `p` ajustado por ETc, o evaluar la alerta sobre `Dr > RAW`. Quitar `stress_threshold_pct` de `crop` y definir "días en estrés" como días con Ks < 1 | bloquea implementación | ambos |
| G02 | Escenario A: franco arenoso, humedad de 28 a 14 %, esperado `water_stress` + `irrigate` (`06-diseno-detallado.md:332-343`) | Con θFC = 0,23 y θWP = 0,09, el umbral del maíz es ≈ 15,3 %; con la regla actual la alerta abre cerca de 20 % | contradicho (consistencia interna) | Recalcular las trayectorias y los `expected` de cada escenario con los parámetros FAO-56 del suelo; agregar un test que verifique que la alerta abre cuando `Dr > RAW` | bloquea implementación | seminario |
| G03 | La humedad del sensor reemplaza al `Dr` modelado; sensor a 10 cm (`06-diseno-detallado.md:173,182,337`; `adr/0009-riego-fao56.md:14`) | Raíz del maíz de 1,0–1,7 m [S1]; RMSE en campo de 0,055–0,191 sin calibración de campo y de 0,005–0,036 con ella [S13] | parcial | Asimilación ponderada (mezclar modelo y sensor según la incertidumbre de la calibración) en vez de reemplazo; exigir una profundidad representativa (≥ 2 profundidades o la mitad de Zr); no asimilar sin calibración de campo | bloquea implementación | ambos |
| G04 | Métricas que usan datos inexistentes: precio, pérdidas ligadas a alertas, línea base, eficiencia por parcela, jornales (`11-metricas.md:27-31`; `06-diseno-detallado.md:196`) | Modelo de datos (`03-modelo-datos.md:92,194-211,265`) | contradicho (consistencia interna) | Agregar `sale_price_cop_per_kg` y `sold_kg` en la cosecha, `labor_days` en las labores, `alert_id` opcional en las observaciones, la entidad `plot_baseline` (encuesta de inscripción) y `irrigation_system` y `efficiency` en `plot` | bloquea implementación (E11) | ambos |
| G05 | El índice de tecnificación mide monitoreo, bitácora, decisión y reconocimiento de alertas (`11-metricas.md:38-46`) | Enfoque de 5 aspectos (Ley 1876, art. 25) y clasificación 1–4 del MADR [S7][S9]; reconocer una alerta no es actuar | parcial | Renombrarlo índice de uso o adopción digital; `risk_management` debe contar la acción registrada tras la alerta; agregar un indicador de prácticas adoptadas; `decision` no aplica a parcelas de secano | debería | ambos |
| G06 | La propuesta central es la "decisión diaria de riego" (`README.md:12`; `01-requisitos.md:32`; `07-frontend-design-system.md:135`) | El 33,3 % de las UPA con cultivos usa riego; el Caribe no está entre los departamentos con más uso [S3] | parcial | Agregar un modo de secano: sin sistema de riego, la tarjeta muestra el déficit hídrico, el pronóstico y la recomendación de manejo (siembra, mulch, cosecha de agua). Agregar un escenario de secano al simulador | debería (bloquea el piloto) | ambos |
| G07 | Variables de M2: lluvia local de 1–6 meses, humedad de suelo, pendiente y elevación (`08-ml.md:55`) | La Mojana: inundación fluvial y ruptura de dique [S22]; el IDEAM publica alertas hidrológicas por río [S21] | parcial | Integrar primero las alertas hidrológicas del IDEAM como regla (línea base); agregar nivel del río y distancia al cauce como variables; documentar en la model card que M2 no ve la inundación fluvial | debería | piloto (y la narrativa del seminario) |
| G08 | No usa ENSO ni las MTA; el escenario "El Niño" es local (`01-requisitos.md:87`) | 27 MTA, 631.000 productores, boletines mensuales [S20] | no verificable en el diseño (ausente) | Incluir los boletines agroclimáticos nacionales y regionales en el corpus RAG; agregar un "estado ENSO / pronóstico estacional" como hecho del asistente y como señal para M3 | debería | ambos (RAG: seminario) |
| G09 | "Anticipación de alertas" en horas, mediana ≥ 24 h (`11-metricas.md:65`) con un modelo mensual por municipio (`08-ml.md:52`) | Inconsistencia interna; etiquetas mensuales de la UNGRD [S23] | contradicho | Medir la anticipación en horas solo para las reglas de pronóstico (`heavy_rain_forecast`); para M2 y M3, "aviso emitido antes del mes del evento" | debería | ambos |
| G10 | Objetivo de error del balance < 5 puntos de % volumétrico (`11-metricas.md:67`) | Error del sensor sin calibración de campo de 5,5–19 puntos [S13] | parcial | Reportar el RMSE de calibración junto al error del balance; en el seminario, medir contra la verdad del simulador | debería | ambos |
| G11 | "Brecha de rendimiento" contra la media EVA municipal (`11-metricas.md:22`) | Definición de la literatura; EVA consolidada por los municipios [S25]; sesgo de selección | contradicho (nombre y uso) | Renombrarla "rendimiento relativo a la referencia municipal"; medir el impacto antes y después contra la línea base, y con controles en el piloto | debería | ambos |
| G12 | Plan LoRaWAN "AU915/US915" por confirmar (`02-estimaciones.md:139`; `adr/0004-mqtt-y-lorawan.md:38`; `docs/README.md:67`) | Uso libre en 915–928 MHz y Res. ANE 28 de 2026 [S10][S11][S12] | parcial (resoluble) | Fijar AU915-928 y citar la Res. ANE 105 de 2020 (Anexo 1) y la 28 de 2026; verificar los límites de potencia del Anexo 1 antes de comprar | opcional | piloto |
| G13 | `fungal_risk` con HR > 85 % durante 10 h o más con SHT31 (`06-diseno-detallado.md:121`; `02-estimaciones.md:19`) | Rango normal del SHT3x de 20–80 % HR; +3 % HR tras 60 h por encima de 80 % [S15] | parcial | Abrigo de radiación y verificación periódica en el piloto; modelar la deriva en el simulador; validar el umbral con un agrónomo y con la climatología local | debería | ambos |
| G14 | `heat_stress` > 35 °C durante 3 h (`06-diseno-detallado.md:120`) | No se obtuvieron las normales del IDEAM (VACÍO); en el valle del Cesar y en La Guajira las máximas en época seca podrían superar ese valor de forma rutinaria (SUPUESTO) | no verificable | Calibrar los umbrales contra las normales 1991–2020 del IDEAM y la fenología del cultivo (floración); medir la tasa de alertas por mes en un año histórico antes de fijarlos | debería | ambos |
| G15 | El técnico "registra visitas" (`01-requisitos.md:14`), pero no hay entidad de visita ni flujo de extensión | Ley 1876, arts. 24–25, 30–31; procedimiento ADR [S7][S8] | contradicho (requisito sin modelo) | Agregar `extension_visit` (fecha, finca, temas según los 5 aspectos, recomendaciones y compromisos, fotos), bandeja de trabajo del técnico y exportación de visitas por organización | debería | ambos |
| G16 | UX de texto y push; voz diferida (`01-requisitos.md:72`; `07-frontend-design-system.md:14`) | 16,8 % de productores analfabetos y 19,2 % sin educación [S4]; los servicios de voz tienen efecto documentado [S19] | parcial | Mantener el diseño con iconos primero; adelantar para el piloto la síntesis de voz del texto de la tarjeta y del asistente, y las notas de voz | debería (piloto) / opcional (seminario) | piloto |
| G17 | Cada productor entra con su teléfono (OTP) (`README.md:19`; `01-requisitos.md` RF-01) | 65,4 % de las personas rurales con celular; 41,9 % de hogares rurales con internet; 45,3 % con servicio "malo" [S6] | parcial | Modo "productor sin cuenta" gestionado por el técnico; permitir que un teléfono sirva a varios productores del hogar; SMS como canal primario de avisos críticos para quien no tiene smartphone | debería | piloto |
| G18 | Catálogo de cultivos con Kc de FAO-56 (`03-modelo-datos.md:368`; glosario: maíz, yuca, plátano) | El ñame no está en la Tabla 12; plátano ≈ banano; yuca de 1.er año Kc_mid 0,80 [S2] | parcial | Campo `kc_source` (fao56, local o aproximado) y estado "sin Kc validado", que bloquea la recomendación de lámina; priorizar maíz, yuca y plátano para la demo | debería | ambos |
| G19 | `Pe = 0,8·P` si P > 5 mm (`06-diseno-detallado.md:171`) | FAO-56 ec. 85 `(P − RO)`; ignorar la lluvia < 0,2·ET0 [S1] | parcial | Usar `Pe = P` si P ≥ 0,2·ET0 (si no, 0) y restar la escorrentía con un coeficiente por pendiente y textura; o documentar el origen de la regla 0,8 | opcional | ambos |
| G20 | Lluvia "de la celda o del pluviómetro del nodo" sin precedencia; ET0 de Open-Meteo (`06-diseno-detallado.md:171`; `adr/0009-riego-fao56.md:12`) | Los reanálisis rinden peor a escala diaria; sesgos en HR y viento [S17][S17b] | parcial | Precedencia: pluviómetro válido > celda; en el piloto, comparar la ET0 con una estación del IDEAM y guardar el sesgo por celda | debería (piloto) | piloto |
| G21 | Aptitud con restricciones duras propias (`adr/0011-aptitud-de-cultivo.md:12`) | Zonificación de aptitud oficial de la UPRA a 1:100.000 [S24] | parcial | Usar la capa UPRA como restricción dura y fuente citable; el modelo propio solo sobre el rendimiento relativo | opcional | ambos |
| G22 | La bitácora de insumos tiene cantidad y unidad (`03-modelo-datos.md:199-202`) | BPA del ICA, Res. 082394 de 2020 [S27]; la lista de chequeo con carencia es SUPUESTO; la extensión incluye BPA [S7] | parcial | Campos de aplicación: producto, número de registro ICA, ingrediente activo, dosis, objetivo, responsable, periodo de carencia y fecha de reingreso; alerta "no cosechar antes de X" | debería | piloto (opcional en el seminario) |
| G23 | Sin vínculo con seguro o crédito | ISA con subsidio de hasta 85 % de la prima para pequeños productores [S26]; 10,7 % de UPA solicita crédito [S3] | no verificable (ausente) | Diferido: exportar el historial de la parcela (polígono, ciclos, rendimientos, eventos) como evidencia para el seguro o el crédito, con consentimiento | opcional | piloto |
| G24 | Ley 1581: consentimiento, exportación y borrado (`01-requisitos.md:59`); datos al LLM "sin datos personales" (`adr/0007-llm-por-api.md:39`) | Ley 1581 y Decreto 1377 [S28] (arts. 5 y 26 citados sin haberlos leído: SUPUESTO); el acuerdo de uso con las organizaciones ya está previsto para RF-17 | parcial | Definir la titularidad de los datos (productor frente a organización), el aviso de transferencia internacional (LLM y autenticación en el exterior) y la política de datos sensibles si se registra etnia | debería | piloto |
| G25 | Nodo de USD 25–30 (`02-estimaciones.md:19`); calibración "por sensor" obligatoria (`02-estimaciones.md:137`) | Barreras de costo y mantenimiento [S32]; variabilidad entre unidades en contradicción [S13][S14] | parcial | Agregar un modelo de costo total por nodo instalado y por año y el tiempo del técnico; política de calibración por suelo + un punto por unidad | debería | piloto |
| G26 | DAU del 30 % (`02-estimaciones.md:34`) | Sin fuente | no verificable | Declararlo supuesto conservador para el dimensionamiento; no usarlo como meta de producto | opcional | piloto |
| G27 | Sin desagregación por sexo ni edad en las métricas | Enfoque diferencial (Ley 1876) [S7]; 36,4 % de productoras [S4]; ISA diferencial [S26] | no verificable (ausente) | Pregunta para el dueño (ver sección 6): capturar sexo y rango de edad con consentimiento para desagregar los indicadores | opcional | piloto |
| G28 | Margen sin precio de referencia; marketplace fuera de alcance (`01-requisitos.md:79`) | SIPSA del DANE: precios mayoristas diarios, semanales y mensuales [S31] | parcial | Tomar el precio SIPSA de la plaza más cercana cuando el productor no registra precio; marcarlo como estimado | opcional | ambos |

**Lo que está respaldado (se mantiene):**

- offline-first y bundle pequeño (H2.3);
- calibración con versiones y guardado del valor crudo (H3.2);
- FAO-56 de coeficiente único frente al dual (`adr/0009-riego-fao56.md:24`), razonable por los parámetros que exige;
- el LLM explica y no decide, y remite a la etiqueta ICA (`06-diseno-detallado.md:297`);
- organizaciones y cooperativas como unidad (`03-modelo-datos.md:63`), coherente con el enfoque de asociatividad de la Ley 1876;
- protocolo de ML con frecuencia real y sesgo de reporte documentado (`08-ml.md:53`);
- los términos de Open-Meteo (`02-estimaciones.md:69`).

---

## 5. Métricas recomendadas

"Seminario" indica si la métrica es medible con datos simulados.

| Acción | Métrica | Definición | Fórmula | Fuente de datos | ¿Seminario? |
|---|---|---|---|---|---|
| mantener | Rendimiento | kg cosechados por ha **cosechada** | `Σ yield_kg / área_cosechada_ha` | Bitácora (`harvest`) con conversión de unidades locales (bulto, racimo, carga) a kg | sí (simulado) |
| cambiar | Rendimiento relativo a la referencia municipal (antes "brecha") | Posición frente a la EVA; no es impacto | `rendimiento / mediana_EVA(cultivo, municipio, 3 años)` | Bitácora + EVA [S25] | sí |
| agregar | Cambio de rendimiento frente a la línea base | Impacto antes y después por parcela | `(rend_ciclo − rend_base) / rend_base`; en el piloto, diferencias en diferencias con controles | `plot_baseline` + bitácora | sí (con línea base simulada) |
| cambiar | Productividad del agua de riego | kg por m³ aplicado | `Σ kg / (Σ mm_riego × 10 × área_ha)` | Bitácora (riego en minutos × caudal / área) | sí |
| agregar | Productividad del agua consumida | kg por m³ de ETc; sirve en secano | `Σ kg / (Σ ETc_mm × 10 × área_ha)` | Balance hídrico | sí |
| cambiar | Días en estrés hídrico | Días con `Dr > RAW` (Ks < 1) | `count(días: Dr_i > RAW_i)` | Balance hídrico asimilado | sí |
| cambiar | Costo por kg | Incluye la mano de obra familiar valorada | `(Σ costos + Σ jornales × jornal_ref) / Σ kg` | Bitácora + `labor_days` | sí |
| cambiar | Margen bruto | Ingreso menos costos | `Σ sold_kg × precio − Σ costos` (precio registrado o de SIPSA, marcado) | Bitácora + SIPSA [S31] | sí |
| agregar | Producción por jornal (proxy del ODS 2.3.1) | Productividad del trabajo | `Σ kg / Σ labor_days` | Bitácora | sí |
| mantener (con dato) | Pérdidas por evento | kg o COP perdidos vinculados a una alerta | `Σ loss where alert_id not null` | Bitácora con `alert_id` | sí |
| cambiar | Índice de uso de la plataforma (antes "de tecnificación") | Cuatro componentes; `risk_management` pasa a "acción registrada tras la alerta"; `decision` solo en parcelas con riego | igual que hoy, con esas redefiniciones | Lecturas, bitácora, alertas | sí |
| agregar | Prácticas adoptadas | Proporción de prácticas recomendadas que se aplicaron (mulch, preventivo a tiempo, BPA…) | `prácticas aplicadas / prácticas recomendadas` en el ciclo | Bitácora + recomendaciones | sí |
| agregar | Cobertura de extensión | Visitas por finca y mes, y temas según los 5 aspectos | `count(extension_visit) / fincas` | `extension_visit` | sí |
| agregar (piloto) | Nivel de clasificación del usuario | Nivel 1–4 del MADR por aspecto, al inicio y al cierre | Registro oficial | Registro de usuarios de la ADR [S8][S9] | no |
| cambiar | Anticipación de alertas | Horas solo para alertas de pronóstico; meses para M2 y M3 | `t_evento − t_alerta` | Alertas + eventos (UNGRD o verdad del simulador) | sí |
| agregar | Precisión y recall por regla contra la verdad | Calidad de cada regla | TP/(TP+FP), TP/(TP+FN) | Etiquetas de verdad del simulador; en el piloto, confirmación del técnico | **sí (la más fuerte del seminario)** |
| cambiar | Error del balance hídrico | MAE contra la verdad (seminario) o contra un sensor con calibración de campo (piloto), reportado junto al RMSE del sensor | `MAE(θ_pred, θ_ref)` | Simulador / sensor calibrado | sí |
| agregar | Tasa de alertas por parcela y mes | Control de la fatiga de alertas | `alertas / (parcelas × mes)` por regla | Alertas | sí |
| mantener | Completitud, latencias, sincronización, entrega | SLI operativos (`11-metricas.md` §4) | como hoy | Sistema | parcialmente (sin monitoreo) |
| mantener | Uso offline, utilidad del asistente | Producto (`11-metricas.md:95`) | como hoy | Sistema | sí |
| descartar | "Parcelas monitoreadas" como adopción (`11-metricas.md:54`) | Mide el despliegue de hardware, no la adopción | pasar a métrica operativa | — | — |
| descartar | ODS 6.4.1 como indicador de parcela | Es nacional (USD/m³) | — | — | — |

---

## 6. Preguntas abiertas para el dueño o un agrónomo

1. **Producto:** ¿el piloto objetivo es de parcelas **con** riego (hortalizas, plátano, arroz) o de pequeños productores de **secano** (maíz, yuca, ñame)? La respuesta cambia la pantalla de inicio y el valor de G06.
2. **Producto:** ¿el índice de tecnificación debe alinearse con la clasificación oficial de usuarios del MADR (5 aspectos, niveles 1–4) o basta con renombrarlo como índice de uso?
3. **Agrónomo:** los valores de `p`, Zr y Kc por cultivo y variedad local, y los umbrales de `heat_stress` y `fungal_risk` para el Caribe (G13, G14, G18). ¿Qué hacer con el ñame, que no tiene Kc en FAO-56?
4. **Agrónomo:** ¿a qué profundidades instalar los sensores por cultivo y qué protocolo de calibración de campo seguir (un punto frente a varios)?
5. **Producto:** ¿quién paga y mantiene los nodos en el piloto (organización, programa o productor) y con qué presupuesto por nodo al año?
6. **Producto y legal:** ¿de quién son los datos de la parcela (productor u organización)? ¿Se permite enviarlos a proveedores extranjeros (LLM, autenticación)?
7. **Producto:** ¿se capturan sexo y rango de edad para desagregar los indicadores (enfoque diferencial) aunque aumente el tratamiento de datos personales?
8. **Producto:** ¿el alcance del riesgo de inundación incluye la inundación fluvial (La Mojana, Sinú, San Jorge)? Si es así, ¿se acepta depender de las alertas del IDEAM como línea base antes que de M2?

---

## 7. Fuentes

Todas consultadas el 2026-09-22. "R" indica que solo se accedió al resumen indexado o a un resumen de terceros.

- **[S1]** FAO. *Crop evapotranspiration*, Irrigation and Drainage Paper 56, cap. 8 (ecs. 82–87, Tablas 19 y 22). 1998. https://www.fao.org/4/x0490e/x0490e0e.htm
- **[S2]** FAO. FAO-56, cap. 6, Tabla 12 (Kc de coeficiente único). 1998. https://www.fao.org/4/x0490e/x0490e0b.htm
- **[S3]** DANE. *3er Censo Nacional Agropecuario 2014, Boletín 6: maquinaria, construcciones, riego, asistencia técnica y financiamiento*. Resultados de 2015 (PDF de 2016). https://www.dane.gov.co/files/CensoAgropecuario/entrega-definitiva/Boletin-6-Infraestructura/6-Boletin.pdf
- **[S4]** DANE. *CNA 2014, Boletín 2: caracterización de productores residentes*. 2016. https://www.dane.gov.co/files/CensoAgropecuario/entrega-definitiva/Boletin-2-Productores-residentes/2-Boletin.pdf
- **[S5]** DANE. *CNA 2014, avance de resultados* (presentación, agosto de 2015). https://www.dane.gov.co/files/CensoAgropecuario/avanceCNA/CNA_agosto_2015_new_present.pdf (R para la cifra de ~70 %)
- **[S6]** DANE. *ENTIC Hogares 2024, boletín técnico* (1 de agosto de 2025). https://www.dane.gov.co/files/operaciones/ENTIC/bol-ENTICHogares-2024.pdf
- **[S7]** Congreso de Colombia. *Ley 1876 de 2017* (SNIA). Gestor Normativo de Función Pública. https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=261916
- **[S8]** ADR. *PR-SPE-002, Administración del registro de usuarios del servicio público de extensión agropecuaria*, v1, 22/07/2020. https://www.adr.gov.co/wp-content/uploads/2022/07/Administracion-del-Registro-de-Usuarios-del-Servicio-Publico-de-Extension-Agropecuaria.pdf
- **[S9]** MADR. *Manual operativo de registro y clasificación de usuarios* y *Res. 407 de 2018* (R). https://www.minagricultura.gov.co/ministerio/direcciones/Documents/Doc%20Orlando/Manual_Operativo_Registro_y_Clasificaci%C3%B3n_de_Usuarios.pdf
- **[S10]** ANE. *Res. 105 de 2020* (bandas de uso libre), compilación MinTIC. https://normograma.mintic.gov.co/mintic/compilacion/docs/resolucion_ane_0105_2020.htm
- **[S11]** ANE. *Res. 000028 del 26-01-2026* (banda de 900 MHz). https://normograma.mintic.gov.co/mintic/compilacion/docs/resolucion_ane_0028_2026.htm y https://ane.gov.co/Sliders/Publicaciones/RESOLUCIONNUMERO000028DEL26012026.pdf
- **[S12]** Resúmenes secundarios de cumplimiento normativo (R):
  - CSA Group: https://www.csagroup.org/global-certification-regulatory-update/colombias-ane-publishes-resolution-000028-of-2026-updating-the-900-mhz-band-imt-allocations/
  - TÜV Rheinland: https://www.tuv.com/regulations-and-standards/en/colombia-resolution-no-000028-of-2026-amendment-of-the-resolution-105-of-2020-and-update-of-the-national-frequency-allocation-table-cnabf.html
  - Bixtia (blog, 5 de agosto de 2026): https://www.bixtia.com/433-mhz-o-915-mhz-la-pregunta-que-todo-proyecto-lora-en-colombia-deberia-resolver-antes-de-disenar-el-hardware/
- **[S13]** Strypsteen, G. et al. *Field Performance and Calibration Strategies for Low-Cost Capacitive Soil Moisture Sensors*. Sensors, 2026. https://pmc.ncbi.nlm.nih.gov/articles/PMC13259304/
- **[S14]** Abdelmoneim, A. A. et al. *Calibration of Low-Cost Capacitive Soil Moisture Sensors for Irrigation Management Applications*. Sensors, enero de 2025. https://pmc.ncbi.nlm.nih.gov/articles/PMC11768944/
- **[S15]** Sensirion. *Datasheet SHT3x-DIS*, v7, diciembre de 2022 (R). https://sensirion.com/media/documents/213E6A3B/63A5A569/Datasheet_SHT3x_DIS.pdf
- **[S16]** Open-Meteo. *Historical Weather API* y *Terms*. https://open-meteo.com/en/docs/historical-weather-api · https://open-meteo.com/en/terms · https://open-meteo.com/en/pricing
- **[S17]** *Evaluation of Areal Monthly Average Precipitation Estimates from MERRA2 and ERA5 Reanalysis in a Colombian Caribbean Basin*. Atmosphere 12(11):1430, 2021 (R). https://doi.org/10.3390/atmos12111430
  - Comparación de productos de precipitación en Colombia (R): https://www.tandfonline.com/doi/full/10.1080/02626667.2025.2600094
- **[S17b]** Evaluación de ET0 con ERA5-Land (Sicilia, 2024) (R). https://www.sciencedirect.com/science/article/pii/S0378377424000672
- **[S18]** Fabregas, R., Kremer, M. y Schilbach, F. *Realizing the potential of digital development: The case of agricultural advice*. Science 366(6471), 2019 (R). https://www.science.org/doi/10.1126/science.aay3038
- **[S19]** Cole, S. y Fernando, A. N. *'Mobile'izing Agricultural Advice*. Economic Journal 131(633):192–219, 2021 (R). https://academic.oup.com/ej/article-abstract/131/633/192/5867759
- **[S20]** Mesas Técnicas Agroclimáticas (R):
  - Alianza Bioversity-CIAT: https://alliancebioversityciat.org/tools-innovations/local-technical-agroclimatic-committees-ltac
  - CCAFS: https://ccafs.cgiar.org/es/boletin-agroclimatico-local
  - AGROSAVIA (10 años): https://www.agrosavia.co/noticias/diez-a%C3%B1os-de-las-mesas-t%C3%A9cnicas-agroclim%C3%A1ticas-un-modelo-clave-para-la-agricultura-resiliente-en-colombia
  - FAO: https://openknowledge.fao.org/server/api/core/bitstreams/b0fc5b6e-0b26-47a5-b1f3-1ba3719a56d0/content/src/html/mesas-tecnicas-agroclimaticas-en-colombia.html
- **[S21]** IDEAM. *Boletín de Alertas Hidrológicas* y pronóstico hidrológico (R). https://www.ideam.gov.co/sala-de-prensa/boletines/Bolet%C3%ADn-de-Alertas-Hidrol%C3%B3gicas-(BAH) · http://www.ideam.gov.co/en/web/agua/pronostico-hidrologico
- **[S22]** La Mojana (R):
  - OCHA, SITREP n.° 01, 25 de agosto de 2023: https://www.unocha.org/publications/report/colombia/colombia-inundacion-gran-escala-en-la-mojana-reporte-de-situacion-sitrep-no-01-del-25-de-agosto-de-2023
  - Pesquisa Javeriana: https://www.javeriana.edu.co/pesquisa/caregato-la-mojana-territorio-agua-2/
  - El Tiempo: https://www.eltiempo.com/colombia/otras-ciudades/la-mojana-ruptura-de-dique-deja-hasta-ahora-3000-familias-afectadas-3341167
- **[S23]** UNGRD. *Emergencias UNGRD* (datos abiertos). https://www.datos.gov.co/Ambiente-y-Desarrollo-Sostenible/Emergencias-UNGRD-/wwkg-r6te
- **[S24]** UPRA. SIPRA, zonificaciones de aptitud (R). https://upra.gov.co/en/node/579 · https://upra.gov.co/en/node/1044
- **[S25]** UPRA. *Evaluaciones Agropecuarias Municipales* (R). https://upra.gov.co/en/eva · https://www.datos.gov.co/Agricultura-y-Desarrollo-Rural/Evaluaciones-Agropecuarias-Municipales-EVA-2019-20/uejq-wxrr
- **[S26]** FINAGRO. *Incentivo al Seguro Agropecuario, Circular 12 de 2026* y comunicado de 2025 (R). https://www.finagro.com.co/sites/default/files/notifications/2026-02/Circular%20No.%2012%20de%202026.pdf · https://www.presidencia.gov.co/prensa/Paginas/Finagro-destina-128000-millones-para-que-campesinos-aseguren-sus-cultivos-ante-fenomenos-climaticos-250516.aspx
- **[S27]** ICA. *Res. 082394 de 2020* (certificación BPA) (R). https://www.ica.gov.co/getattachment/446ac25a-0fd7-4fd8-ae9f-2e50f0047c8b/2020R82394.aspx · https://normograma.invima.gov.co/compilacion/docs/resolucion_ica_82394_2020.htm
- **[S28]** *Ley 1581 de 2012* y *Decreto 1377 de 2013* (no se pudo abrir el texto; los artículos citados son SUPUESTO). https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=49981 · https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=53646
- **[S29]** ONU/FAO. Metadatos de los ODS 2.3.1 y 2.3.2 (R). https://unstats.un.org/sdgs/metadata/files/Metadata-02-03-01.pdf · https://unstats.un.org/sdgs/metadata/files/Metadata-02-03-02.pdf · https://unstats.un.org/sdgs/metadata/files/Metadata-02-04-01proxy.pdf
- **[S30]** MADR. Lanzamiento de la cadena del ñame (R). https://www.minagricultura.gov.co/noticias/Paginas/MinAgricultura-lanz%C3%B3-la-cadena-de-%C3%B1ame-en-Bol%C3%ADvar-para-lograr-rentabilidad-en-las-30-000-familias-que-dependen-del-sector.aspx
  - AGROSAVIA, contexto de la cadena del ñame: https://repository.agrosavia.co/server/api/core/bitstreams/061d54ff-6a5b-487f-b8fd-d5e7f1fc8985/content
- **[S31]** DANE. *SIPSA, precios mayoristas* (R). https://www.dane.gov.co/index.php/estadisticas-por-tema/agropecuario/sistema-de-informacion-de-precios-sipsa
- **[S32]** *Utilization of Internet of Things and Wireless Sensor Networks for Sustainable Smallholder Agriculture*. Sensors, 2022 (R). https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9101116/
- **Contexto secundario, no usado como evidencia:** documentación de la v1 en `docs/01-business/vision.md` del repositorio de la v1 (alcance geográfico de los 7 departamentos).
