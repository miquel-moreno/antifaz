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

## Solo para desarrollo: los SDK oficiales de los tests de contrato

Los tests de `tests/contract/` usan los SDK oficiales de OpenAI y Anthropic contra Antifaz (issue 5, parte 5d). Están en el grupo `dev` de `pyproject.toml`: **no se distribuyen** (ni en la librería ni en la imagen) y `scripts/check_licenses.py` no los mira porque no son de ejecución. Se comprobaron a mano en sus metadatos el 2026-09-30, con las versiones de `uv.lock`:

| Paquete | Versión | Licencia | Llega por |
|---|---|---|---|
| openai | 3.22.1 | Apache-2.0 | directa (dev) |
| anthropic | 1.9.0 | MIT | directa (dev) |
| httpx2 | 2.13.1 | BSD-3-Clause | los dos SDK (su cliente HTTP) |
| httpcore2 | 2.13.1 | BSD-3-Clause | httpx2 |
| truststore | 0.10.4 | MIT | httpx2 |
| jiter | 0.17.0 | MIT | los dos SDK |
| sniffio | 1.3.1 | MIT o Apache-2.0 | los dos SDK |
| docstring-parser | 0.18.0 | MIT | anthropic |

`httpx2-jsfetch` solo se instala en navegador (emscripten); aquí no se usa.

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
