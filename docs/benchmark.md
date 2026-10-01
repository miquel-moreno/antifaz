# Antifaz-Bench

Cuánto detecta Antifaz, y cuánto se le escapa, medido con datos que no hemos escrito nosotros. Las reglas de medida están en el [ADR-0011](adr/0011-antifaz-bench.md).

## Cómo leer las cifras

- **Fugas por cada 100 datos:** de cada 100 datos personales anotados, cuántos saldrían con al menos una letra o cifra sin tapar. Es la cifra que importa para la privacidad. Si un dato se tapa con una etiqueta equivocada (una calle marcada como otra cosa), **no** cuenta como fuga: el dato no sale.
- **Recall (por solapamiento):** de los datos de un tipo, cuántos quedan emparejados con una detección de ese mismo tipo que los toca (cada detección se empareja con un solo dato).
- **F1 estricto:** igual, pero exigiendo el mismo principio y fin exactos, como en la evaluación oficial de MEDDOCAN.
- **Detecciones sin equivalente en el dataset:** tipos que Antifaz detecta y el dataset no anota con una etiqueta propia; se muestran para que sus falsos positivos no queden ocultos.
- **Tipos aún no cubiertos:** datos que Antifaz todavía no busca (nombres, lugares, hospitales, fechas de consulta…). El NER (nombres y direcciones) existe, es opcional y viene apagado: la tabla principal es sin NER y la sección "Resultados con NER" lo mide encendido. Se publican igual, y cuentan en la cifra global.

## Datos y entorno

- **Datos:** partición de test de MEDDOCAN (250 casos clínicos **sintéticos** en español, escritos por terceros), descargada de Zenodo (DOI [10.5281/zenodo.4279323](https://doi.org/10.5281/zenodo.4279323), licencia CC-BY-4.0) y comprobada por tamaño, MD5 y SHA-256. No se redistribuye. Cita: Marimon et al., *Automatic De-identification of Medical Texts in Spanish: the MEDDOCAN Track*, IberLEF@SEPLN 2019 ([licencias](licencias.md)).
- **Sesgos declarados:** MEDDOCAN es texto clínico: pocos emails, teléfonos o direcciones, y ningún DNI, IBAN o tarjeta. Por eso este benchmark mide sobre todo lo que **todavía falta**; el generador sintético (issue 3, segunda parte) medirá el resto, con su propio sesgo (lo escribe el mismo equipo que el detector).
- **Qué se mide:** `antifaz.detect.scan()` en CPU, sin NER y sin llamadas a ningún proveedor.

## Resultados en MEDDOCAN

Generados por `make bench`; el detalle está en `evals/results/<fecha>-<versión>.json`.

<!-- bench:start -->
## Tipos cubiertos

| Tipo en el dataset | Tipo Antifaz | Datos | Recall (solape) | Fugas por cada 100 |
|---|---|---|---|---|
| CALLE | ADDRESS | 413 | 81.8 % | 60.3 |
| CORREO_ELECTRONICO | EMAIL | 249 | 99.2 % | 0.8 |
| ID_ASEGURAMIENTO | ES_NSS | 198 | 1.0 % | 98.5 |
| NUMERO_FAX | PHONE | 7 | 85.7 % | 14.3 |
| NUMERO_TELEFONO | PHONE | 26 | 92.3 % | 7.7 |

| Tipo Antifaz | Precisión (mismo tipo) | Precisión estricta | Precisión (cualquier dato personal) | Tapan texto no personal | Recall | F1 | F1 estricto |
|---|---|---|---|---|---|---|---|
| ADDRESS | 93.4 % | 32.9 % | 93.6 % | 23 | 81.8 % | 87.2 % | 30.7 % |
| EMAIL | 99.2 % | 99.2 % | 99.2 % | 2 | 99.2 % | 99.2 % | 99.2 % |
| ES_NSS | 100.0 % | 100.0 % | 100.0 % | 0 | 1.0 % | 2.0 % | 2.0 % |
| PHONE | 76.9 % | 76.9 % | 100.0 % | 0 | 90.9 % | 83.3 % | 83.3 % |

Precisión del mismo tipo: la detección se solapa con un dato anotado de su tipo. Contra cualquier dato personal: se solapa con un dato anotado de cualquier tipo (también los que Antifaz no cubre); solo es un error si tapa texto que no es un dato personal.

## Tipos aún no cubiertos

Antifaz no busca estos datos con esta configuración (el NER existe, es opcional y
viene apagado). Se miden igual: sus fugas cuentan en la cifra global.

| Tipo en el dataset | Datos | Fugas por cada 100 |
|---|---|---|
| CENTRO_SALUD | 6 | 100.0 |
| EDAD_SUJETO_ASISTENCIA | 518 | 100.0 |
| FAMILIARES_SUJETO_ASISTENCIA | 81 | 100.0 |
| FECHAS | 611 | 59.2 |
| HOSPITAL | 130 | 100.0 |
| ID_CONTACTO_ASISTENCIAL | 39 | 94.9 |
| ID_SUJETO_ASISTENCIA | 283 | 99.6 |
| ID_TITULACION_PERSONAL_SANITARIO | 234 | 100.0 |
| INSTITUCION | 67 | 100.0 |
| NOMBRE_PERSONAL_SANITARIO | 501 | 100.0 |
| NOMBRE_SUJETO_ASISTENCIA | 502 | 100.0 |
| OTROS_SUJETO_ASISTENCIA | 7 | 100.0 |
| PAIS | 363 | 100.0 |
| PROFESION | 9 | 100.0 |
| SEXO_SUJETO_ASISTENCIA | 461 | 100.0 |
| TERRITORIO | 956 | 95.2 |

Una detección de cualquier tipo tapa el dato: por eso algunos tipos no cubiertos no
llegan a 100 fugas (por ejemplo, fechas tapadas por DATE_OF_BIRTH).

## Detecciones de tipos sin equivalente en el dataset

Tipos que Antifaz detecta pero que el dataset no anota con una etiqueta propia. Si no
tocan ningún dato anotado, son falsos positivos.

| Tipo Antifaz | Detecciones | Tocan un dato anotado |
|---|---|---|
| DATE_OF_BIRTH | 250 | 250 |

## Global

| Medida | Valor |
|---|---|
| Documentos | 250 |
| Datos personales anotados | 5661 |
| Fugas por cada 100 (todos los tipos) | 86.9 |
| Fugas por cada 100 (tipos cubiertos) | 50.3 |
| Latencia p50 / p95 por documento | 2.88 ms / 5.70 ms |
<!-- bench:end -->

## Resultados en el conjunto sintético

600 textos generados por `evals/generate.py` (semilla fija, en el repositorio): emails, tickets, nóminas, contratos, chats, CSV pegados, código, catalán y trampas. Cubre lo que MEDDOCAN no tiene (DNI, NIE, CIF, NSS con dígito válido, IBAN, tarjetas, pasaportes, matrículas…). **Sesgo declarado:** el mismo equipo escribió el generador y el detector, así que estas cifras son probablemente mejores que en texto real. Los nombres de persona no se anotan todavía (no hay tipo PERSON hasta el NER). En los textos en catalán, el pasaporte y la matrícula llevan además la palabra clave en castellano, porque Antifaz solo reconoce palabras clave en castellano: su 100 % no mide el catalán. Un 100 % aquí **no** significa que Antifaz lo detecte todo.

<!-- bench-synthetic:start -->
## Tipos cubiertos

| Tipo en el dataset | Tipo Antifaz | Datos | Recall (solape) | Fugas por cada 100 |
|---|---|---|---|---|
| ADDRESS | ADDRESS | 240 | 100.0 % | 0.0 |
| CREDIT_CARD | CREDIT_CARD | 80 | 100.0 % | 0.0 |
| DATE_OF_BIRTH | DATE_OF_BIRTH | 60 | 100.0 % | 0.0 |
| EMAIL | EMAIL | 420 | 100.0 % | 0.0 |
| ES_CCC | ES_CCC | 60 | 100.0 % | 0.0 |
| ES_CIF | ES_CIF | 60 | 100.0 % | 0.0 |
| ES_DNI | ES_DNI | 400 | 100.0 % | 0.0 |
| ES_NIE | ES_NIE | 60 | 100.0 % | 0.0 |
| ES_NIF | ES_NIF | 40 | 100.0 % | 0.0 |
| ES_NSS | ES_NSS | 60 | 100.0 % | 0.0 |
| ES_PASSPORT | ES_PASSPORT | 60 | 100.0 % | 0.0 |
| ES_PLATE | ES_PLATE | 60 | 100.0 % | 0.0 |
| IBAN | IBAN | 120 | 100.0 % | 0.0 |
| IP | IP | 60 | 100.0 % | 0.0 |
| PHONE | PHONE | 440 | 100.0 % | 0.0 |

| Tipo Antifaz | Precisión (mismo tipo) | Precisión estricta | Precisión (cualquier dato personal) | Tapan texto no personal | Recall | F1 | F1 estricto |
|---|---|---|---|---|---|---|---|
| ADDRESS | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| CREDIT_CARD | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| DATE_OF_BIRTH | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| EMAIL | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| ES_CCC | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| ES_CIF | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| ES_DNI | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| ES_NIE | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| ES_NIF | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| ES_NSS | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| ES_PASSPORT | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| ES_PLATE | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| IBAN | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| IP | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |
| PHONE | 100.0 % | 100.0 % | 100.0 % | 0 | 100.0 % | 100.0 % | 100.0 % |

Precisión del mismo tipo: la detección se solapa con un dato anotado de su tipo. Contra cualquier dato personal: se solapa con un dato anotado de cualquier tipo (también los que Antifaz no cubre); solo es un error si tapa texto que no es un dato personal.

## Tipos aún no cubiertos

Antifaz no busca estos datos con esta configuración (el NER existe, es opcional y
viene apagado). Se miden igual: sus fugas cuentan en la cifra global.

| Tipo en el dataset | Datos | Fugas por cada 100 |
|---|---|---|

Una detección de cualquier tipo tapa el dato: por eso algunos tipos no cubiertos no
llegan a 100 fugas (por ejemplo, fechas tapadas por DATE_OF_BIRTH).

## Detecciones de tipos sin equivalente en el dataset

Tipos que Antifaz detecta pero que el dataset no anota con una etiqueta propia. Si no
tocan ningún dato anotado, son falsos positivos.

| Tipo Antifaz | Detecciones | Tocan un dato anotado |
|---|---|---|
| — | 0 | 0 |

## Global

| Medida | Valor |
|---|---|
| Documentos | 600 |
| Datos personales anotados | 2220 |
| Fugas por cada 100 (todos los tipos) | 0.0 |
| Fugas por cada 100 (tipos cubiertos) | 0.0 |
| Latencia p50 / p95 por documento | 0.33 ms / 0.49 ms |
<!-- bench-synthetic:end -->

## Resultados con NER (MEDDOCAN)

Generados por `make bench NER=1` (antes, `make ner-model`); el detalle está en `evals/results/<fecha>-<versión>-ner.json`. El umbral se elige en la partición dev con un suelo de precisión del 85 % en PERSON y ADDRESS, medido contra cualquier dato personal anotado (ADR-0011, enmienda del 2026-10-01). Se publican siempre las dos precisiones (contra cualquier dato personal y del mismo tipo) y la estricta. Si ningún umbral cumple el suelo, se usa el más preciso, el resultado lo dice (`floor_met: false`) y el NER queda como opcional y apagado por defecto.

<!-- bench-ner:start -->
## Con NER: umbral elegido en dev

Umbrales probados en la partición **dev** de MEDDOCAN. Se elige el de menos fugas (tipos cubiertos) con precisión de PERSON y ADDRESS **contra cualquier dato personal anotado** de al menos 85 %; en empate, el más alto. Una detección solo cuenta como error si tapa texto que no es un dato personal. También se publican la precisión del mismo tipo y la estricta.

Esta forma de medir el suelo se decidió el 2026-10-01, **después** de ver la primera ejecución en dev, donde la precisión del mismo tipo fue del 63–69 % (PERSON) y del 56–58 % (ADDRESS) (ADR-0011, enmienda). El 85 % no ha cambiado.

| Umbral | Fugas por cada 100 (todos) | Fugas (cubiertos) | PERSON: precisión (cualquier dato personal) | PERSON: precisión (mismo tipo) | PERSON: precisión estricta | PERSON: recall | PERSON: tapan texto no personal | ADDRESS: precisión (cualquier dato personal) | ADDRESS: precisión (mismo tipo) | ADDRESS: precisión estricta | ADDRESS: recall | ADDRESS: tapan texto no personal | Suelo PERSON / ADDRESS |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.3 | 59.0 | 12.3 | 67.5 % | 63.6 % | 51.5 % | 99.9 % | 510 | 95.9 % | 56.5 % | 18.8 % | 95.6 % | 30 | no / sí |
| 0.4 | 59.4 | 12.5 | 68.7 % | 65.1 % | 52.7 % | 99.7 % | 479 | 95.9 % | 56.7 % | 18.8 % | 95.2 % | 30 | no / sí |
| 0.5 | 60.0 | 13.0 | 69.5 % | 66.5 % | 53.8 % | 99.0 % | 454 | 96.2 % | 57.3 % | 19.1 % | 94.5 % | 27 | no / sí |
| 0.6 | 60.8 | 13.8 | 70.9 % | 68.6 % | 55.3 % | 98.0 % | 416 | 96.3 % | 57.6 % | 19.4 % | 93.5 % | 26 | no / sí |

**El NER no cumple el suelo de precisión del 85 % en nombres; se publica como opcional y desactivado por defecto.**

Ningún umbral llega al suelo: se usa el más preciso, **0.6** (el de mayor precisión contra cualquier dato personal en el tipo más débil; en empate, el más alto). Con él se mide la partición de test una sola vez. Activa el NER solo si te vale que tape de más texto que no es personal.

## Con NER: antes y después (test)

| Medida | Sin NER | Con NER |
|---|---|---|
| Fugas por cada 100 (todos los tipos) | 86.9 | 61.6 |
| Fugas por cada 100 en nombres (paciente y personal sanitario) | 100.0 | 2.5 |
| Fugas por cada 100 en CALLE | 60.3 | 12.3 |
| Precisión de PERSON: cualquier dato personal / mismo tipo / estricta | — | 75.2 % / 73.2 % / 60.0 % |
| Detecciones de PERSON que tapan texto no personal | — | 335 |
| Latencia p50 / p95 por documento | 2.88 ms / 5.70 ms | 1947 ms / 3822 ms (caché fría) |
| Latencia p50 / p95 con la caché caliente | — | 2.86 ms / 5.56 ms |
| Memoria del proceso del NER (RSS) | — | 743 MB |
| Arranque del pool (SHA-256 del modelo y carga) | — | 51 s |

La caché fría no es fría del todo en un documento: antes de medir, la evaluación pasa el primero una vez sin cronometrar (calentamiento), así que 1 de las 250 medidas ya sale de la caché.

Modelo `urchade/gliner_multi_pii-v1` en el commit `1fcf13e85f4e`, manifiesto SHA-256 `cbdd812389ca`.

### Con NER · Tipos cubiertos

| Tipo en el dataset | Tipo Antifaz | Datos | Recall (solape) | Fugas por cada 100 |
|---|---|---|---|---|
| CALLE | ADDRESS | 413 | 94.2 % | 12.3 |
| CORREO_ELECTRONICO | EMAIL | 249 | 99.2 % | 0.4 |
| ID_ASEGURAMIENTO | ES_NSS | 198 | 1.0 % | 98.5 |
| NOMBRE_PERSONAL_SANITARIO | PERSON | 501 | 99.4 % | 2.8 |
| NOMBRE_SUJETO_ASISTENCIA | PERSON | 502 | 97.8 % | 2.2 |
| NUMERO_FAX | PHONE | 7 | 85.7 % | 14.3 |
| NUMERO_TELEFONO | PHONE | 26 | 92.3 % | 7.7 |

| Tipo Antifaz | Precisión (mismo tipo) | Precisión estricta | Precisión (cualquier dato personal) | Tapan texto no personal | Recall | F1 | F1 estricto |
|---|---|---|---|---|---|---|---|
| ADDRESS | 59.4 % | 21.8 % | 95.1 % | 32 | 94.2 % | 72.8 % | 26.8 % |
| EMAIL | 99.2 % | 99.2 % | 99.2 % | 2 | 99.2 % | 99.2 % | 99.2 % |
| ES_NSS | 100.0 % | 100.0 % | 100.0 % | 0 | 1.0 % | 2.0 % | 2.0 % |
| PERSON | 73.2 % | 60.0 % | 75.2 % | 335 | 98.6 % | 84.0 % | 68.8 % |
| PHONE | 76.9 % | 76.9 % | 100.0 % | 0 | 90.9 % | 83.3 % | 83.3 % |

Precisión del mismo tipo: la detección se solapa con un dato anotado de su tipo. Contra cualquier dato personal: se solapa con un dato anotado de cualquier tipo (también los que Antifaz no cubre); solo es un error si tapa texto que no es un dato personal.

### Con NER · Tipos aún no cubiertos

Antifaz no busca estos datos con esta configuración (el NER existe, es opcional y
viene apagado). Se miden igual: sus fugas cuentan en la cifra global.

| Tipo en el dataset | Datos | Fugas por cada 100 |
|---|---|---|
| CENTRO_SALUD | 6 | 100.0 |
| EDAD_SUJETO_ASISTENCIA | 518 | 100.0 |
| FAMILIARES_SUJETO_ASISTENCIA | 81 | 79.0 |
| FECHAS | 611 | 59.2 |
| HOSPITAL | 130 | 98.5 |
| ID_CONTACTO_ASISTENCIAL | 39 | 94.9 |
| ID_SUJETO_ASISTENCIA | 283 | 99.6 |
| ID_TITULACION_PERSONAL_SANITARIO | 234 | 100.0 |
| INSTITUCION | 67 | 94.0 |
| OTROS_SUJETO_ASISTENCIA | 7 | 100.0 |
| PAIS | 363 | 90.1 |
| PROFESION | 9 | 100.0 |
| SEXO_SUJETO_ASISTENCIA | 461 | 98.7 |
| TERRITORIO | 956 | 75.4 |

Una detección de cualquier tipo tapa el dato: por eso algunos tipos no cubiertos no
llegan a 100 fugas (por ejemplo, fechas tapadas por DATE_OF_BIRTH).

### Con NER · Detecciones de tipos sin equivalente en el dataset

Tipos que Antifaz detecta pero que el dataset no anota con una etiqueta propia. Si no
tocan ningún dato anotado, son falsos positivos.

| Tipo Antifaz | Detecciones | Tocan un dato anotado |
|---|---|---|
| DATE_OF_BIRTH | 250 | 250 |

### Con NER · Global

| Medida | Valor |
|---|---|
| Documentos | 250 |
| Datos personales anotados | 5661 |
| Fugas por cada 100 (todos los tipos) | 61.6 |
| Fugas por cada 100 (tipos cubiertos) | 14.5 |
| Latencia p50 / p95 por documento | 1947.30 ms / 3822.07 ms |
<!-- bench-ner:end -->

<!-- bench-presidio:start -->
## Comparación con Presidio

Se genera con `make bench PRESIDIO=1` (grupo de dependencias `bench`, que no se distribuye); el detalle irá en `evals/results/<fecha>-<versión>-presidio.json`. Todavía no se ha ejecutado.
<!-- bench-presidio:end -->
