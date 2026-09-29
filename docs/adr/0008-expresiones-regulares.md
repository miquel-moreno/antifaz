# ADR-0008 · Expresiones regulares y diccionarios

- **Estado:** Aceptada (aprobada por Miquel Moreno el 2026-09-29)
- **Fecha:** 2026-09-29

## Contexto

Una expresión regular mal escrita puede tardar un tiempo exponencial (ReDoS) y tumbar la pasarela con un solo texto.

## Decisión

- Los patrones internos se escriben para no retroceder y se prueban con fuzzing.
- Las expresiones regulares que pone el cliente en su política se compilan con `google-re2`, que garantiza tiempo lineal.
- Los diccionarios del cliente usan Aho-Corasick, con límite de tamaño.

## Consecuencias

- Ningún texto ni política de cliente puede provocar un tiempo de proceso descontrolado.
- re2 no admite algunas construcciones (por ejemplo, referencias hacia atrás); se documenta en la guía de políticas.
