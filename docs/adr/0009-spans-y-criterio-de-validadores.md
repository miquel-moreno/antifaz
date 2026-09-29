# ADR-0009 · Spans y criterio de los validadores

- **Estado:** Aceptada (aprobada por Miquel Moreno el 2026-09-29)
- **Fecha:** 2026-09-29

## Contexto

La especificación del proyecto fijaba para el CIF una regla estricta: las entidades P, Q, R, S, N y W llevan letra de control y A, B, E y H llevan dígito. Al buscar la fuente, esa regla solo aparece en Wikipedia, sin norma citada. La norma vigente (Orden EHA/451/2008) dice "letra + 7 dígitos + un carácter de control", sin algoritmo ni reparto por letras, y python-stdnum acepta las dos formas para todas las letras. Un validador más estricto rompería la invariante 10 y, sobre todo, **dejaría escapar CIF reales**: en una pasarela de privacidad, detectar de menos es el fallo peligroso.

Lo mismo con el NIF K/L/M: el RD 1065/2007 habla de "siete caracteres alfanuméricos" y no publica el algoritmo; stdnum usa 7 cifras y la letra del DNI.

El detector también necesitaba un tipo común para decir "aquí hay un dato".

## Decisión

- Los validadores aceptan lo mismo que python-stdnum: CIF con letra o dígito de control para cualquier letra de entidad válida; NIF K/L/M con 7 cifras y la letra del DNI.
- `Span = (start, end, type, layer, confidence)`: inmutable y **sin el valor**. Quien necesite el texto lo recorta del original; así el dato no puede acabar en un `repr` ni en un log.
- Solapamientos: validador antes que patrón; después el más largo; después el que empieza antes; y en un empate exacto, el orden de definición de `EntityType`.

## Consecuencias

- Más recall a cambio de algún falso positivo, que es un fallo seguro (se enmascara de más).
- La especificación del proyecto queda desactualizada en ese punto; este ADR manda.
- El resultado del detector es determinista: no depende del orden en que lleguen los spans.
