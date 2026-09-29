# ADR-0001 · Arquitectura y stack

- **Estado:** Aceptada (aprobada por Miquel Moreno el 2026-09-29)
- **Fecha:** 2026-09-29

## Contexto

Antifaz es una pasarela HTTP entre las aplicaciones y los proveedores de LLM. Tiene que ser rápida, fácil de auditar y fácil de desplegar por una empresa pequeña.

## Decisión

- Python 3.12, FastAPI, httpx asíncrono y Pydantic v2 con `hide_input_in_errors=True` en los modelos que reciben datos del cliente.
- Una pieza por responsabilidad (Puerta, Detector, Política, Tabla de marcadores, Enmascarador, Guardia de salida, Enrutador, Restaurador, Evidencias), cada una en su paquete de `src/antifaz/`.
- PostgreSQL 16 con asyncpg para claves y evidencias, a partir de la v0.2.
- Valkey si hace falta estado compartido. No Redis 8, por su licencia.

## Consecuencias

- El núcleo (detectar, enmascarar, restaurar) funciona como librería sin base de datos.
- La v0.1 no necesita PostgreSQL: menos piezas que desplegar y que proteger.
- Separar piezas añade algo de código de unión, a cambio de poder probar cada una sola.
