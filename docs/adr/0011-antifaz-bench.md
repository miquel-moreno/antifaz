# ADR-0011 · Antifaz-Bench: cómo se cuentan aciertos y fugas

- **Estado:** Aceptada (aprobada por Miquel Moreno el 2026-09-29)
- **Fecha:** 2026-09-29

## Contexto

Las cifras del README tienen que salir de un benchmark, ser comparables y no favorecer a Antifaz. La evaluación oficial de MEDDOCAN exige principio y fin exactos por tipo, pero para la privacidad lo que importa es otra cosa: si queda **algún carácter** de un dato personal sin tapar.

## Decisión

1. **Acierto por tipo:** una detección es un verdadero positivo si se solapa en al menos un carácter con un dato anotado del mismo tipo (ya traducido a los tipos de Antifaz), emparejando uno a uno por orden de posición (un emparejamiento voraz solo puede dar menos aciertos que el óptimo, nunca más: no favorece al detector). También se publica el F1 **estricto** (principio y fin exactos), comparable con MEDDOCAN.
2. **Fuga:** un dato anotado se fuga si alguna de sus letras o cifras no queda cubierta por ninguna detección, **del tipo que sea**. La cifra principal es **fugas por cada 100 datos**, por tipo y global, **incluidos los tipos que Antifaz aún no cubre**.
3. **MEDDOCAN** (Zenodo 10.5281/zenodo.4279323, CC-BY-4.0) se descarga al ejecutar, se comprueba su MD5 y **no se redistribuye**. Si una posición anotada no cuadra con el texto, la carga falla en vez de ignorarla.
4. Los resultados guardan solo tipos, recuentos y métricas: **nunca** texto ni valores de los documentos (hay un test).
5. Las detecciones de tipos que el dataset no anota se publican aparte (cuántas hay y cuántas tocan un dato anotado), para que sus falsos positivos no queden ocultos. Una etiqueta del dataset que no esté en la tabla de correspondencia es un error, no un tipo "no cubierto" silencioso.
6. Cada resultado anota el commit; si había cambios sin guardar, lleva `-dirty`.
7. (Segunda parte del issue 3) La verdad del generador sintético sale de cómo se construye cada texto, nunca del detector; Presidio y spaCy solo irán en un grupo de dependencias `bench` que no se distribuye.

## Consecuencias

- La cifra global sobre MEDDOCAN será mala hasta que llegue el NER, y se publica así, con los tipos no cubiertos a la vista.
- Las cifras por solapamiento son algo más generosas que las estrictas; por eso se publican las dos.
- El benchmark es reproducible: mismos datos (tamaño, MD5 y SHA-256), mismo código y el entorno anotado en cada resultado.
- Un span discontinuo cuenta como un dato por fragmento (el test de MEDDOCAN no tiene ninguno).
