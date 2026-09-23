# ADR-0014: Autenticación con proveedor gestionado (OTP por teléfono) y autorización propia

- **Estado:** Aceptada
- **Fecha:** 2026-09-22
- **Alcance:** producción futura. En el perfil `seminar` se usa un emisor local de JWT que imprime el código OTP en consola; el backend valida por JWKS igual que en producción ([ADR-0021](0021-perfil-seminario-local.md)).

## Contexto

Muchos productores no usan correo electrónico. La v1 usaba Supabase Auth. Construir la autenticación desde cero es un riesgo de seguridad.

## Decisión

- **Supabase Auth** (gestionado) para identidad: OTP por SMS y correo, emisión de JWT y refresco.
- El backend valida el JWT con JWKS (firma, `iss`, `aud`, `exp`) y usa `sub` como `app_user.id`.
- **Roles y membresías viven en la base propia**, no en el proveedor. El proveedor queda reemplazable.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Auth propia (contraseñas + JWT) | Riesgo de seguridad y trabajo que no diferencia al producto |
| Keycloak autoalojado | Pesado de operar para el piloto |
| Auth0 / Clerk | Costo por usuario activo mayor para el volumen previsto |

## Consecuencias

**Positivas**

- Inicio de sesión por teléfono sin construir OTP ni enviar SMS propios.
- La autorización está en el dominio y es auditable.

**Negativas / costos aceptados**

- Dependencia de un tercero para el login (con sesión activa se sigue usando la app offline).
- Costo de SMS de OTP según el proveedor configurado (solo en producción).

## Relacionado

[04-api](../04-api.md#convenciones-http), [09 seguridad](../09-cuellos-de-botella.md#seguridad)
