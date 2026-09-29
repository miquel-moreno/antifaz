# ADR-0012 · Escape de `[[` y qué es "el mismo valor"

- **Estado:** Propuesta
- **Fecha:** 2026-09-30
- **Concreta:** el escape del ADR-0002 y la numeración del ADR-0004

## Contexto

El ADR-0002 dice que los `[[…]]` que ya escribe el usuario se escapan, pero no dice cómo. El escape tiene que cumplir dos cosas a la vez:

- `restore(mask(x)) == x` para cualquier texto (invariante 1), también si el usuario escribe `[[`, `[[[`, `[[!` o un marcador falso como `[[ES_DNI_1]]`.
- Un marcador que no salió de esta petición nunca se convierte en un dato (invariante 6).

El ADR-0004 dice que el mismo dato recibe el mismo marcador, pero no define "el mismo".

## Decisión

**Escape (al enmascarar).** En el texto del usuario que queda en claro (lo que hay entre detecciones y las detecciones que la política deja pasar), después de cada tramo máximo de dos o más `[` seguidos se inserta un `!`:

- `[[` → `[[!`, `[[[` → `[[[!`, `[[!` → `[[!!`.
- Un `[` suelto no se toca.

**Restauración (una sola pasada).** Se recorre la respuesta con una única expresión con dos alternativas, probadas en este orden en cada posición:

1. `ESC = \[\[+!` → se quita el `!` y se deja el tramo de `[`.
2. `PH = \[\[[ \t]*([A-Za-z]+(?:_[A-Za-z]+)*_[0-9]+)[ \t]*\]\]`, sin distinguir mayúsculas → si el token en mayúsculas está en la tabla **de esta petición**, se pone su valor; si no, se deja el texto tal cual.

Los valores restaurados no se vuelven a analizar.

**Por qué funciona `restore(mask(x)) == x`.** En la salida del enmascarador, todo tramo de 2+ `[` que viene del usuario va seguido de `!`, así que ESC lo consume entero y lo devuelve tal cual; nunca empieza en él un PH, porque ESC se prueba antes. Los únicos `[[` sin `!` detrás son los de nuestros marcadores. Si el usuario deja un `[` pegado a un marcador (`[` + `[[ES_DNI_1]]` = `[[[ES_DNI_1]]`), en la primera posición no encaja ni ESC (falta el `!`) ni PH (tras `[[` viene otro `[`), y en la siguiente encaja el marcador: el `[` del usuario se conserva. Un `!` del usuario justo después de un marcador tampoco se confunde, porque el marcador acaba en `]]`, no en `[`.

**"El mismo valor".** Es el par (tipo, texto exacto tal como aparece). `12345678Z` y `12345678-Z` son dos formas distintas y reciben dos marcadores distintos. La numeración es por tipo y por orden de primera aparición en todos los textos de la petición, sin huecos (`ES_DNI_1`, `ES_DNI_2`…). La guardia de salida (issue 4b) compara valores normalizados, así que una forma distinta del mismo dato no se le escapa.

## Alternativas descartadas

- **Caracteres invisibles** (espacio de anchura cero) como escape: los modelos y los tokenizadores los pierden o los cambian, y el usuario no ve qué ha pasado.
- **Claves normalizadas** para "el mismo valor": obligaría a restaurar una sola forma y el texto del usuario dejaría de volver idéntico.

## Consecuencias

- La ida y vuelta es exacta para cualquier texto, y lo comprueban tests de propiedades con Hypothesis.
- Riesgo aceptado: si el modelo se come el `!` de un escape y deja `[[ES_DNI_1]]`, se restaura como marcador. Solo puede salir un valor de **esta misma petición**, que el usuario ya envió; nunca uno de otra petición.
- El modelo ve `[[!` donde el usuario escribió `[[`: es un cambio mínimo y visible.
