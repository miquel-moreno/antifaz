# ADR-0007 · Licencia Apache-2.0

- **Estado:** Propuesta (pendiente de aprobar por Miquel Moreno)
- **Fecha:** 2026-09-29

## Contexto

El proyecto busca adopción por empresas y contribuciones externas.

## Decisión

- Licencia Apache-2.0, con `NOTICE`.
- Regla de dependencias: nada no comercial ni copyleft fuerte en lo que se distribuye; copyleft débil sin modificar, anotado en `docs/licencias.md` y comprobado en CI (`scripts/check_licenses.py`).

## Consecuencias

- Apache-2.0 incluye concesión de patentes, algo que valoran las empresas frente a MIT.
- Es la misma licencia que el rival principal (PasteGuard) y que los modelos GLiNER elegidos.
