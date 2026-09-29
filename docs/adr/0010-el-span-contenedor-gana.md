# ADR-0010 · El span que contiene a otro gana

- **Estado:** Aceptada (aprobada por Miquel Moreno el 2026-09-29)
- **Fecha:** 2026-09-29
- **Modifica:** la regla de solapamientos del ADR-0009

## Contexto

El ADR-0009 decía que, cuando dos detecciones se pisan, gana siempre la de validador (con dígito de control) sobre la de patrón. Las revisiones del PR 2b encontraron el problema: en `juan.garcia.12345678Z@example.com`, el DNI (validador) ganaba al email (patrón) y solo se enmascaraba el DNI. `juan.garcia.` y `@example.com` salían en claro hacia el proveedor: el nombre de la persona y, a menudo, su empresa. Lo mismo con una tarjeta o un IBAN dentro de un email.

## Decisión

- Si una detección **contiene por completo** a otra, gana la que contiene, sea cual sea su capa.
- Si el contenedor pierde frente a un solapamiento parcial, las detecciones que tenía dentro vuelven a competir, para que ningún trozo detectado quede sin cubrir (caso encontrado por un test de propiedad con Hypothesis).
- Para los solapamientos **parciales** sigue la regla del ADR-0009: validador antes que patrón, después el más largo, después el que empieza antes y, en empate exacto, el orden de `EntityType`.
- Se calcula en O(n log n), como el resto de la resolución.

## Consecuencias

- Nunca queda en claro un trozo de un valor que ya se ha detectado entero: enmascarar de más es el fallo seguro.
- El tipo que se ve es el del contenedor (`EMAIL`, no `ES_DNI`): el DNI queda oculto igualmente, dentro del marcador del email.
- Casos que no cambian: un IBAN o un NIR que contienen algo parecido a un teléfono ya ganaban por ser más largos.
