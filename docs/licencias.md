# Licencias

Antifaz se distribuye con licencia **Apache-2.0** (`LICENSE` y `NOTICE`).

## Regla

En lo que se distribuye (la librería y la imagen) **no entra nada no comercial (NC) ni copyleft fuerte** (GPL, AGPL, SSPL…). Se permiten dependencias de **copyleft débil sin modificar** (LGPL, MPL, EPL), y cada una se anota aquí con su motivo.

`scripts/check_licenses.py` lo comprueba en cada PR sobre las dependencias de ejecución bloqueadas en `uv.lock`: falla con cualquier licencia prohibida o desconocida, o con un copyleft débil que no esté en esta página. Las herramientas de desarrollo (tests, lint, zizmor) no se distribuyen y no cuentan.

Las dependencias se declaran sin tope superior (`>=`); las versiones exactas que se prueban y se comprueban aquí están en `uv.lock`.

## Dependencias directas añadidas después del scaffold

| Paquete | Licencia | Para qué |
|---|---|---|
| anyio | MIT | Ya venía con Starlette y httpx; se declara porque el streaming (parte 5c) la usa directamente para cerrar la conexión con el proveedor aunque el cliente se vaya (`CancelScope(shield=True)`). |

## Dependencias de copyleft débil

| Paquete | Licencia | Por qué se acepta |
|---|---|---|
| certifi | MPL-2.0 | Certificados raíz que usa httpx para HTTPS. Se usa sin modificar; MPL solo obliga a publicar cambios en sus propios archivos. |
| python-stdnum | LGPL-2.1-or-later | Valida los identificadores de la UE y sirve de oráculo en los tests de DNI, NIE, CIF, CCC e IBAN (ADR-0005). Se usa sin modificar, como dependencia instalada aparte. |

## Modelos y datos

### Datos del benchmark (no se distribuyen)

| Datos | Licencia | Uso |
|---|---|---|
| MEDDOCAN, partición de test (Zenodo, DOI 10.5281/zenodo.4279323) | CC-BY-4.0 | Se descarga al ejecutar `make bench` a `evals/datasets/.cache/` (ignorado por git), se comprueba su MD5 y no se sube al repositorio. |

Cita: Marimon, M., Gonzalez-Agirre, A., Intxaurrondo, A., Rodríguez, H., Lopez Martin, J. A., Villegas, M., Krallinger, M. (2019). *Automatic De-identification of Medical Texts in Spanish: the MEDDOCAN Track, Corpus, Guidelines, Methods and Evaluation of Results.* IberLEF@SEPLN 2019, pp. 618–638.

### Modelos

Todavía ninguno. Cuando entre el NER (issue 6), se anotarán aquí el modelo, su licencia y la de sus datos de entrenamiento. En el benchmark: MEDDOCAN (CC-BY-4.0, citado) y, solo en el entorno del benchmark y nunca en lo que se distribuye, spaCy `es_core_news_*` (GPL-3.0).
