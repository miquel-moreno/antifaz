# ADR-0005 · Validadores propios con python-stdnum como oráculo

- **Estado:** Aceptada (aprobada por Miquel Moreno el 2026-09-29)
- **Fecha:** 2026-09-29

## Contexto

Los identificadores españoles (DNI, NIE, CIF/NIF, NSS, CCC) son el diferencial D1 y tienen que estar implementados, entendidos y probados en el repo.

## Decisión

- DNI, NIE, CIF/NIF, NSS y CCC se implementan en el repo.
- python-stdnum (LGPL, dependencia sin modificar) se usa para validar los identificadores de la UE y como oráculo en los tests de DNI, NIE, CIF, CCC e IBAN.
- stdnum no tiene NSS: el NSS se prueba con un conjunto de vectores documentado, con su fuente.

## Consecuencias

- El algoritmo de cada identificador queda explicado y cubierto en el propio repo.
- Miles de casos contrastados con una implementación independiente (invariante 10).
- stdnum se anota en `docs/licencias.md` como copyleft débil.
