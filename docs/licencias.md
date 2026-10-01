# Licencias

Antifaz se distribuye con licencia **Apache-2.0** (`LICENSE` y `NOTICE`).

## Regla

En lo que se distribuye (la librería y la imagen) **no entra nada no comercial (NC) ni copyleft fuerte** (GPL, AGPL, SSPL…). Se permiten dependencias de **copyleft débil sin modificar** (LGPL, MPL, EPL), y cada una se anota aquí con su motivo.

`scripts/check_licenses.py` lo comprueba en cada PR sobre las dependencias de ejecución bloqueadas en `uv.lock`, **también las de los extras opcionales** (`antifaz[ner]`): falla con cualquier licencia prohibida o desconocida, o con un copyleft débil que no esté en esta página. Las herramientas de desarrollo (tests, lint, zizmor) no se distribuyen y no cuentan. El grupo `bench` (Presidio y spaCy, solo para el benchmark) tampoco se distribuye, pero el script lo comprueba con la misma regla (ver más abajo).

La CI no instala el extra `ner` (PyTorch pesa cientos de MB). Para los paquetes que solo trae ese extra, el script usa la licencia que dicen sus metadatos, anotada a mano en `RECORDED_EXTRA` con las versiones de `uv.lock`; un test la compara con los metadatos instalados cuando el extra está (en local).

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

## El extra `ner` (opcional)

`antifaz[ner]` añade el modelo de nombres y direcciones (ADR-0003, ADR-0016). Solo se instala si se pide. PyTorch sale **siempre** del índice de CPU de PyTorch (`https://download.pytorch.org/whl/cpu`, en `[tool.uv.sources]` de `pyproject.toml`), nunca las ruedas con CUDA. Licencias leídas de los metadatos el 2026-09-30, con las versiones de `uv.lock`:

| Paquete | Versión | Licencia | Llega por |
|---|---|---|---|
| gliner | 0.2.29 | Apache-2.0 | directa (extra `ner`) |
| torch | 2.14.1+cpu | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT | directa (extra `ner`) |
| transformers | 5.16.1 | Apache-2.0 | directa (extra `ner`; gliner pide < 5.17) |
| huggingface-hub | 1.33.0 | Apache-2.0 | gliner, transformers |
| hf-xet | 1.6.0 | Apache-2.0 | huggingface-hub |
| tokenizers | 0.23.2 | Apache-2.0 | transformers |
| safetensors | 0.8.0 | Apache-2.0 | gliner, transformers |
| sentencepiece | 0.2.2 | Apache-2.0 | gliner (tokenizador de mDeBERTa) |
| protobuf | 7.36.2 | BSD-3-Clause | directa (extra `ner`): transformers la necesita para leer `spm.model` |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | gliner, transformers |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python | transformers |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | gliner, transformers (ver copyleft débil) |
| sympy, mpmath | 1.14.0, 1.3.0 | BSD | torch |
| networkx | 3.7 | BSD-3-Clause | torch |
| jinja2, markupsafe | 3.1.6, 3.0.3 | BSD | torch |
| fsspec, filelock | 2026.9.0, 4.0.6 | BSD-3-Clause, MIT | torch, huggingface-hub |
| setuptools | 84.0.0 | MIT | torch |
| typer, rich, shellingham, markdown-it-py, mdurl, pygments, colorama | — | MIT, MIT, ISC, MIT, MIT, BSD-2-Clause, BSD | huggingface-hub (su línea de comandos) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | gliner, transformers |

## Solo para el benchmark: el grupo `bench` (Presidio)

La comparación con Presidio de Antifaz-Bench (issue 13, ADR-0011) usa presidio-analyzer, spaCy y un modelo de spaCy. Están en el grupo de dependencias `bench` de `pyproject.toml`: **no se distribuyen** (ni en la librería ni en la imagen), `uv sync` no los instala y la CI tampoco; solo `make bench PRESIDIO=1`. Un test comprueba que `uv export --no-dev` (lo que se distribuye, con todos los extras) no los lista.

Aunque no se distribuyen, `scripts/check_licenses.py` también los comprueba (nada prohibido ni desconocido), con las licencias anotadas a mano en `RECORDED_BENCH` para la CI. Leídas de los metadatos el 2026-10-01, con las versiones de `uv.lock`. Solo los paquetes que trae este grupo y no el resto:

| Paquete | Versión | Licencia | Llega por |
|---|---|---|---|
| presidio-analyzer | 2.2.364 | MIT | directa (grupo `bench`) |
| spacy | 3.8.16 | MIT | directa (grupo `bench`) |
| xx-ent-wiki-sm | 3.8.0 | MIT | directa (grupo `bench`): el modelo multilingüe de spaCy, descargado de su release de GitHub y fijado por SHA-256 en `uv.lock`. Entrenado con [WikiNER](https://figshare.com/articles/Learning_multilingual_named_entity_recognition_from_Wikipedia/5462500) ([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); Nothman, Ringland, Radford, Murphy y Curran, 2013); fuente: [release del modelo](https://github.com/explosion/spacy-models/releases/tag/xx_ent_wiki_sm-3.8.0) |
| phonenumbers | 9.0.40 | Apache-2.0 | presidio-analyzer |
| tldextract | 5.3.2 | BSD-3-Clause | presidio-analyzer |
| requests-file | 3.0.1 | Apache-2.0 | tldextract |
| requests | 2.34.2 | Apache-2.0 | tldextract, spacy |
| urllib3 | 2.8.0 | MIT | requests |
| charset-normalizer | 3.5.2 | MIT | requests |
| thinc | 8.3.13 | MIT | spacy |
| blis | 1.3.3 | BSD | thinc |
| cymem | 2.0.13 | MIT | spacy, thinc |
| preshed | 3.0.13 | MIT | spacy, thinc |
| murmurhash | 1.0.15 | MIT | spacy, thinc |
| srsly | 2.5.4 | MIT | spacy, thinc |
| catalogue | 2.0.10 | MIT | spacy, thinc |
| confection | 1.3.3 | MIT | spacy, thinc |
| wasabi | 1.1.3 | MIT | spacy, thinc |
| spacy-legacy | 3.0.12 | MIT | spacy |
| spacy-loggers | 1.0.5 | MIT | spacy |
| weasel | 1.0.0 | MIT | spacy |
| cloudpathlib | 0.25.0 | MIT | weasel |
| smart-open | 8.0.2 | MIT | weasel |
| wrapt | 2.5.0 | BSD-2-Clause | smart-open |

presidio-analyzer pide `numpy < 2.5` y el extra `ner` fija una versión más nueva: en `pyproject.toml` el grupo `bench` y el extra `ner` se declaran incompatibles (`[tool.uv] conflicts`), así uv los resuelve por separado y el grupo `bench` no cambia nada de lo que se distribuye con el extra. Por eso no se pueden instalar a la vez: la comparación lee los resultados del NER ya guardados en `evals/results/`.

**Por qué este modelo y no uno en español.** Los modelos de spaCy en español (`es_core_news_sm/md/lg`) son **GPL-3.0** (ADR-0003): no se usan ni en el benchmark. `xx_ent_wiki_sm` es MIT, multilingüe (incluye español) y pequeño (11 MB).

## Dependencias de copyleft débil

| Paquete | Licencia | Por qué se acepta |
|---|---|---|
| certifi | MPL-2.0 | Certificados raíz que usa httpx para HTTPS. Se usa sin modificar; MPL solo obliga a publicar cambios en sus propios archivos. |
| tqdm | MPL-2.0 AND MIT | Barras de progreso que usan gliner y transformers (solo con el extra `ner`). Se usa sin modificar; MPL solo obliga a publicar cambios en sus propios archivos. |
| python-stdnum | LGPL-2.1-or-later | Valida los identificadores de la UE y sirve de oráculo en los tests de DNI, NIE, CIF, CCC e IBAN (ADR-0005). Se usa sin modificar, como dependencia instalada aparte. |

## La imagen de Docker (issue 7, parte 7a)

La imagen lleva Antifaz y sus dependencias de ejecución (las mismas que comprueba `scripts/check_licenses.py`, sin el extra `ner`) sobre la imagen oficial `python:3.12-slim` (Debian 13 "trixie"). Esa base trae los paquetes del sistema de Debian, y algunos son **GPL** (por ejemplo bash, coreutils o apt). Van **sin modificar**, como programas separados que Antifaz no enlaza ni incluye en su código: es la "agregación" que permite la GPL, igual que en cualquier imagen basada en Debian. Su código fuente está en los repositorios de Debian, y el SBOM que genera la CI (CycloneDX, Syft) lista cada paquete con su licencia.

**Pendiente de decidir por Miquel**: si esta excepción de la regla (que habla de "la librería y la imagen") basta así anotada, o si se prefiere una base sin paquetes GPL (por ejemplo una distroless), con más trabajo de mantenimiento.

## Modelos y datos

### Datos del benchmark (no se distribuyen)

| Datos | Licencia | Uso |
|---|---|---|
| MEDDOCAN, partición de test (Zenodo, DOI 10.5281/zenodo.4279323) | CC-BY-4.0 | Se descarga al ejecutar `make bench` a `evals/datasets/.cache/` (ignorado por git), se comprueba su MD5 y no se sube al repositorio. |

Cita: Marimon, M., Gonzalez-Agirre, A., Intxaurrondo, A., Rodríguez, H., Lopez Martin, J. A., Villegas, M., Krallinger, M. (2019). *Automatic De-identification of Medical Texts in Spanish: the MEDDOCAN Track, Corpus, Guidelines, Methods and Evaluation of Results.* IberLEF@SEPLN 2019, pp. 618–638.

La partición de desarrollo (`dev`) del mismo zip se usa solo para elegir el umbral del NER; la de test se mide una vez con el umbral elegido ([benchmark](benchmark.md)).

### Modelos

El modelo **no se distribuye** con Antifaz ni está en el repositorio: `make ner-model` lo descarga de Hugging Face, fijado por commit, y comprueba el tamaño y el SHA-256 de cada archivo contra `src/antifaz/detect/ner/manifest.json`. Cadena de licencias, del modelo a sus datos (revisada el 2026-09-30):

| Pieza | Licencia | Qué es | Fuente |
|---|---|---|---|
| `urchade/gliner_multi_pii-v1` (commit `1fcf13e8…`) | Apache-2.0 | El modelo que usa Antifaz: GLiNER multilingüe afinado para datos personales | https://huggingface.co/urchade/gliner_multi_pii-v1 |
| `urchade/synthetic-pii-ner-mistral-v1` | Apache-2.0 | Datos de ese afinado: textos sintéticos generados con Mistral | https://huggingface.co/datasets/urchade/synthetic-pii-ner-mistral-v1 |
| `urchade/gliner_multi-v2.1` | Apache-2.0 | Modelo base del afinado (según la ficha del modelo) | https://huggingface.co/urchade/gliner_multi-v2.1 |
| `urchade/pile-mistral-v0.1` | Apache-2.0 | Datos del modelo base: anotaciones hechas con Mistral sobre textos de The Pile. **El origen y la licencia de cada texto de partida no están documentados** en la ficha | https://huggingface.co/datasets/urchade/pile-mistral-v0.1 |
| `microsoft/mdeberta-v3-base` (commit `a0484667…`) | MIT | Codificador de GLiNER. Antifaz solo descarga su tokenizador (`spm.model`, `config.json`, `tokenizer_config.json`); los pesos van dentro de `pytorch_model.bin` de GLiNER. **Sus datos de entrenamiento (CC100) no tienen una licencia documentada** en la ficha | https://huggingface.co/microsoft/mdeberta-v3-base |

**Por qué `gliner_multi_pii-v1` y no otro GLiNER.** Las primeras versiones (`urchade/gliner_base`, `gliner_multi` v0/v1) se publicaron con **CC-BY-NC-4.0** (no comercial) porque se entrenaron con datos de licencia no comercial: la regla las prohíbe. Se fija la familia v2 (`gliner_multi-v2.1`, Apache-2.0) y el afinado para datos personales que sale de ella.

**Riesgo declarado.** Los datos de partida del modelo base y de mDeBERTa no tienen licencia documentada. Es lo habitual en modelos publicados así y la licencia del modelo (Apache-2.0 y MIT) es la que rige su uso, pero se anota aquí para que quien lo despliegue lo sepa.

En el benchmark: MEDDOCAN (CC-BY-4.0, citado) y, solo en el grupo `bench` y nunca en lo que se distribuye, el modelo de spaCy `xx_ent_wiki_sm` 3.8.0 (MIT; ver la sección del grupo `bench`). Los `es_core_news_*` (GPL-3.0) no se usan.
