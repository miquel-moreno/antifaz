# ADR-0014 · Normalización previa del detector

- **Estado:** Propuesta
- **Fecha:** 2026-09-30
- **Modifica:** los límites alfanuméricos de los patrones de identificadores (PR 2a) y la limitación conocida de Unicode de `TECNICO.md`

## Contexto

La ronda 1 del red team (`tests/redteam/`) encontró 11 formas de pasar un DNI, NIE o IBAN en claro al proveedor sin que el detector lo viera: espacios entre grupos (`12 345 678 Z`), el dato partido en dos líneas, pegado a la etiqueta (`DNI12345678Z`, `NIEX1234567L`) o dentro de una palabra, un guion blando invisible en medio, y letras cirílicas o griegas idénticas a las latinas (`З`, `Ζ`, `Х`, `Е`). La guardia de salida solo para los valores que el detector ya encontró, así que no servía de red. Además, un JSON anidado 1.000 niveles provocaba un `RecursionError` (500) en el recorrido de adjuntos.

## Decisión

- **Vista normalizada con mapa de posiciones.** Antes de los patrones se construye una vista del texto: sin caracteres Unicode Cf (invisibles), con NFKC solo cuando da **una** letra o cifra ASCII (ancho completo), con los espacios raros (tabulador, NBSP…) como espacio normal y con una tabla corta y documentada de homoglifos cirílicos y griegos pasados a latín. Cada carácter se sustituye por uno o desaparece, así que las posiciones se devuelven al original con un mapa monótono y el marcador cubre los caracteres originales, invisibles incluidos. El texto no se modifica y `restore(mask(x)) == x` se mantiene (test de propiedad).
- **Separadores:** DNI, NIE y NIF K/L/M aceptan un espacio entre grupos y antes de la letra, además de punto y guion, y un único salto de línea en cualquier punto; el IBAN, un salto de línea entre grupos. Cada rama tiene longitud fija: no hay retroceso (ADR-0008).
- **Límites relajados para los tipos con control fuerte** (DNI, NIE, NIF K/L/M, IBAN): pueden tocar letras, nunca cifras. Con separador antes de la letra de control, esa letra no puede ir seguida de otra letra. CIF, NSS, CCC, codice fiscale y NIF-IVA mantienen los límites estrictos.
- **Profundidad máxima del JSON:** 100 niveles, comprobados de forma iterativa antes de cualquier recorrido recursivo; más profundo se bloquea con 400 `antifaz_blocked`.

## Consecuencias

- Las 11 fugas y los 4 límites Unicode conocidos (espacio de ancho cero, espacio duro, tabuladores, cifras de ancho completo) pasan a estar cubiertos por tests normales.
- Más falsos positivos posibles: un número de 8 cifras pegado a una letra que resulte ser su letra de control (1 de cada 23) ahora se enmascara. Es el fallo seguro.
- Tres expectativas antiguas cambian a propósito (`A12345678ZB`, `12345678ZZ`, `X2482300WA`, `cafés12345678Z` ahora se detectan).
- La tabla de homoglifos es corta a propósito: alfabetos no incluidos y cifras de otros sistemas de numeración siguen siendo una limitación documentada.
- Coste: un texto no ASCII se copia una vez con `str.translate`; si tiene caracteres invisibles, se guarda la lista de sus posiciones.
