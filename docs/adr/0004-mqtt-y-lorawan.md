# ADR-0004: MQTT como protocolo de ingesta y LoRaWAN (ChirpStack) como transporte rural

- **Estado:** Aceptada
- **Fecha:** 2026-09-22
- **Alcance:** el broker MQTT y el contrato se usan en ambos perfiles. LoRaWAN/ChirpStack y los nodos físicos son de producción futura; en el perfil `seminar` las lecturas las publica el simulador de escenarios por MQTT con el contrato real ([ADR-0021](0021-perfil-seminario-local.md)).

## Contexto

Los nodos IoT envían lecturas cada 15 min con energía solar y, a veces, sin cobertura celular. En la v1 las "lecturas de sensores" se cargaban con un `POST` HTTP manual.

## Decisión

- **MQTT** (Mosquitto 2) con TLS, credenciales por nodo (plugin Dynamic Security) y ACL `tc/v1/%u/#`. QoS 1 más deduplicación en la base.
- Nodos con Wi-Fi o celular publican directo al broker.
- Donde no hay cobertura se usa **LoRaWAN** con **ChirpStack** v4 (perfil opcional de Compose). ChirpStack decodifica el payload y lo publica en MQTT, y el ingestor lo traduce al mismo mensaje interno.
- Contrato en [04-api](../04-api.md#contrato-mqtt).

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| HTTP POST desde el nodo | Más energía por mensaje (TLS + HTTP por envío), sin Last Will ni canal de bajada; no funciona para LoRa |
| CoAP | Menos herramientas y soporte en brokers y bibliotecas |
| Plataformas IoT en la nube (AWS IoT, TTN comercial) | Costo por mensaje y dependencia; TTN comunitario no garantiza nada para producción |
| Solo LoRaWAN | Obliga a comprar gateways incluso donde hay Wi-Fi o celular |

## Consecuencias

**Positivas**

- Un protocolo para todos los transportes; el backend no sabe si el nodo es Wi-Fi o LoRa.
- Last Will da el estado en línea/fuera de línea sin sondeo.
- El canal `down` deja listo el control de actuadores (RF-16).

**Negativas / costos aceptados**

- Operar un broker y, con LoRa, ChirpStack (que requiere Redis).
- Las bandas LoRaWAN en Colombia deben confirmarse antes de comprar hardware (solo en producción).
- La calibración de sensores baratos es obligatoria y consume tiempo del técnico.

## Relacionado

[06 §1-2](../06-diseno-detallado.md), [09](../09-cuellos-de-botella.md)
