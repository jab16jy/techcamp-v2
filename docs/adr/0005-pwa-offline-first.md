# ADR-0005: PWA offline-first en lugar de app nativa

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

Los usuarios usan Android de gama baja con señal intermitente. Necesitan instalar sin fricción, recibir notificaciones y registrar la bitácora sin conexión. El equipo ya sabe React.

## Decisión

**PWA** con React 19 + Vite + TypeScript, Service Worker (Workbox vía `vite-plugin-pwa`) para el shell, **Dexie (IndexedDB)** como almacenamiento local primario de la bitácora y caché persistida de TanStack Query para el resto. Web Push para notificaciones.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| App nativa (Kotlin) | Otro lenguaje y otro equipo; distribución por tienda |
| React Native / Expo | Mejor acceso al hardware, pero dos objetivos de build y publicación en tiendas; el caso no requiere APIs nativas |
| Web sin offline (como la v1) | Inusable en campo; se pierden registros |

## Consecuencias

**Positivas**

- Se instala desde el navegador y se actualiza sin tiendas.
- Una sola base de código con el equipo actual.

**Negativas / costos aceptados**

- En iOS el push solo funciona con la PWA instalada en la pantalla de inicio (iOS 16.4 o superior) y no hay Background Sync: se compensa con SMS/WhatsApp para alertas críticas y sincronización en primer plano.
- El almacenamiento del navegador puede ser desalojado: se pide `navigator.storage.persist()` y se sincroniza con frecuencia.
- El escaneo de QR usa la cámara desde el navegador (`BarcodeDetector` donde exista; una biblioteca liviana como respaldo).

## Relacionado

[07-frontend-design-system](../07-frontend-design-system.md), [ADR-0013](0013-sincronizacion-offline.md)
