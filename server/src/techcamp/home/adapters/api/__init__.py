"""Home HTTP adapters: the pydantic view of the `/status` payload.

docs/04-api.md §Estado de la parcela (pantalla principal). The router owns the
HTTP shape only: pydantic out, domain errors mapped to `problem+json`, and the
`application` use case decides.
"""
