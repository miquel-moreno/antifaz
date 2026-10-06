<p align="center"><img src="docs/images/logo.svg" alt="Logo de Antifaz" width="120"></p>

<h1 align="center">Antifaz</h1>

<p align="center"><b>Usa la IA con datos de clientes sin enviarle sus datos.</b><br><a href="README.md">Read in English</a></p>

<p align="center">
  <a href="https://github.com/miquel-moreno/antifaz/releases"><img src="https://img.shields.io/github/v/release/miquel-moreno/antifaz?include_prereleases&amp;label=versi%C3%B3n" alt="Versión"></a>
  <a href="https://github.com/miquel-moreno/antifaz/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/miquel-moreno/antifaz/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/miquel-moreno/antifaz?label=licencia" alt="Licencia"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&amp;logoColor=white" alt="Python 3.12"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&amp;logoColor=white" alt="FastAPI"></a>
  <a href="https://github.com/miquel-moreno/antifaz/pkgs/container/antifaz"><img src="https://img.shields.io/badge/ghcr.io-antifaz-2496ED?logo=docker&amp;logoColor=white" alt="Imagen Docker en ghcr.io"></a>
  <a href="#puesta-en-marcha"><img src="https://img.shields.io/badge/compatible%20con-OpenAI%20%7C%20Anthropic-412991" alt="Compatible con OpenAI y Anthropic"></a>
  <a href="https://github.com/miquel-moreno/antifaz/releases/tag/v0.1.0"><img src="https://img.shields.io/badge/estado-beta-orange" alt="Estado: beta"></a>
  <a href="https://github.com/miquel-moreno/antifaz/stargazers"><img src="https://img.shields.io/github/stars/miquel-moreno/antifaz?style=flat&amp;logo=github&amp;label=estrellas" alt="Estrellas en GitHub"></a>
</p>

**EN** · Use any LLM with your customers' data, without sending it.

Las empresas quieren usar ChatGPT o Claude con emails y documentos de clientes, pero enviarles datos personales es un riesgo para la privacidad. Antifaz se pone en medio y cambia esos datos por marcadores antes de que salga el texto.

![Demo en terminal con datos inventados: Antifaz encuentra un DNI, un email y un IBAN, el proveedor solo recibe marcadores y la respuesta vuelve con los valores originales.](docs/images/demo.gif)

## Qué hace
- Encuentra los datos personales del texto, con foco en los identificadores españoles y europeos (DNI, NIE, número de la Seguridad Social, IBAN…), y los cambia por marcadores como `[[ES_DNI_1]]`.
- Envía a OpenAI o Anthropic solo los marcadores y vuelve a poner los valores reales en la respuesta.
- Si algo falla al revisar una petición, la bloquea en vez de dejarla pasar.

## Resultado
- En un benchmark público en español, los nombres que quedan a la vista bajan de 100 a 2,5 de cada 100 con el detector de nombres opcional (también tapa algo de texto que no es personal: detalles abajo).
- Revisa un documento en unos 3 milisegundos sin el detector de nombres, y pasa 960 tests de ataque.

## Tecnologías
Python · FastAPI · APIs de LLM (OpenAI, Anthropic) · NLP (GLiNER) · Privacidad (RGPD) · Docker · GitHub Actions · pytest

## Mi papel
Lo he diseñado y desarrollado de principio a fin. Desarrollo asistido por IA bajo mi especificación y revisión.

[Detalles técnicos →](docs/TECNICO.md) · [LinkedIn](https://www.linkedin.com/in/miquel-moreno-martinez)

---

> [!WARNING]
> **Beta: [ya está publicada la v0.1.0](https://github.com/miquel-moreno/antifaz/releases/tag/v0.1.0).** Antifaz **no garantiza encontrar todos los datos personales**: solo protege lo que detecta, y el [benchmark](docs/benchmark.md) dice cuánto se escapa. Pruébalo con tu tipo de textos antes de usarlo con datos reales.

## Puesta en marcha

Necesitas Docker con Compose.

**Desde la v0.2.0 (todavía no publicada): un minuto, sin clonar.** En una carpeta vacía:

```bash
curl -LO https://github.com/miquel-moreno/antifaz/releases/download/v0.2.0/docker-compose.yml
docker run --rm -it -v "$PWD:/work" -w /work --user "$(id -u):$(id -g)" --network none \
  ghcr.io/miquel-moreno/antifaz:0.2.0 init
docker compose up -d
curl http://127.0.0.1:8000/healthz   # {"status":"ok","version":"0.2.0"}
```

`init` te pide las claves de los proveedores sin mostrarlas, escribe `.env` y crea una `ANTIFAZ_API_KEY` aleatoria nueva, que enseña una sola vez: es la que usan tus clientes. Ninguna clave pasa por la línea de órdenes. `--rm` borra el contenedor y su log (que guarda esa clave); `--user` hace que `.env` sea tuyo. En Windows, quita `--user` (en PowerShell, usa `-v "${PWD}:/work"`). El `docker-compose.yml` descargado fija la imagen por digest. Ejecutar `init` dentro del contenedor necesita la v0.2.0 o posterior: la imagen 0.1.0 solo sirve.

**Con la v0.1.0 (hoy), a mano:**

```bash
git clone https://github.com/miquel-moreno/antifaz && cd antifaz
cp .env.example .env
# Edita .env:
#   ANTIFAZ_API_KEY: un valor aleatorio, por ejemplo la salida de `openssl rand -hex 32`
#   ANTIFAZ_OPENAI_API_KEY y/o ANTIFAZ_ANTHROPIC_API_KEY: las claves de tus proveedores
#   (borra la línea del proveedor que no uses: su ruta responde 503)
docker compose up -d                 # descarga ghcr.io/miquel-moreno/antifaz:0.1.0
curl http://127.0.0.1:8000/healthz   # {"status":"ok","version":"0.1.0"}
```

Antifaz se niega a arrancar mientras una clave tenga su valor de ejemplo (`change-me...`); `docker compose logs antifaz` dice qué variable hay que arreglar, nunca su valor. Solo escucha en `127.0.0.1`: para llegar desde otras máquinas, pon delante un proxy inverso con HTTPS ([detalles](docs/TECNICO.md)).

**Sin Compose**, el mismo endurecimiento en un solo `docker run` (también vale con la 0.1.0):

```bash
docker run -d --name antifaz --env-file .env -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp:size=16m,mode=1777,noexec,nosuid,nodev \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 512m --cpus 1 --pids-limit 128 --restart unless-stopped \
  ghcr.io/miquel-moreno/antifaz:0.1.0
```

Después, apunta tu cliente a Antifaz en vez de al proveedor, con tu `ANTIFAZ_API_KEY` como clave (las claves de los proveedores se quedan en `.env`):

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="<tu ANTIFAZ_API_KEY>")
```

```python
from anthropic import Anthropic

client = Anthropic(base_url="http://localhost:8000", api_key="<tu ANTIFAZ_API_KEY>")
```

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $ANTIFAZ_API_KEY" -H "Content-Type: application/json" \
  -d '{"model": "gpt-4.1-nano", "messages": [{"role": "user", "content": "Mi DNI es 12345678Z"}]}'
```

**Claude Code**: pon `ANTHROPIC_BASE_URL=http://localhost:8000` y en `ANTHROPIC_API_KEY` tu `ANTIFAZ_API_KEY`. Todavía no se ha probado con la API real de Anthropic (solo con el SDK oficial y respuestas grabadas).

Qué funciona en la v0.1: OpenAI Chat Completions y Anthropic Messages (con `count_tokens`), con y sin streaming, llamadas a herramientas y bloques de razonamiento; `GET /v1/models`; y `POST /antifaz/scan`, que dice dónde están los datos personales (tipos y posiciones, nunca los valores). Probado con los SDK oficiales de OpenAI y Anthropic para Python, y una vez con la API real de OpenAI.

Para comprobar tu configuración sin llamar a ningún proveedor: `uv run antifaz verify` (desde un clon, con [uv](https://docs.astral.sh/uv/)). Planta valores falsos en cada parte de una petición y falla si alguno llega a un proveedor falso.

## Qué detecta

Solo los tipos que tienen tests. La política por defecto los enmascara todos menos el CIF de empresa.

| Dato | Marcador | Cómo se encuentra |
|---|---|---|
| DNI, NIE, NIF K/L/M | `ES_DNI`, `ES_NIE`, `ES_NIF` | Dígito de control |
| CIF de empresa | `ES_CIF` | Dígito de control (no se enmascara por defecto) |
| Número de la Seguridad Social (NSS) | `ES_NSS` | Dígito de control |
| Cuenta bancaria (CCC) e IBAN | `ES_CCC`, `IBAN` | Dígito de control |
| Tarjeta de pago | `CREDIT_CARD` | Comprobación de Luhn y prefijo conocido |
| Codice fiscale italiano, NIF-IVA europeo | `IT_CODICE_FISCALE`, `EU_VAT` | Dígito de control (python-stdnum) |
| Email | `EMAIL` | Patrón |
| Teléfono español | `PHONE` | Patrón |
| Dirección IPv4 | `IP` | Patrón |
| Dirección postal al estilo español, catalán o gallego | `ADDRESS` | Patrón |
| Pasaporte, matrícula, fecha de nacimiento | `ES_PASSPORT`, `ES_PLATE`, `DATE_OF_BIRTH` | Patrón con una palabra clave delante |
| Identificadores de Portugal, Francia y Alemania | `PT_NIF`, `FR_NIR`, `DE_IDNR` | Dígito de control con una palabra clave delante |
| Nombres de persona | `PERSON` | Modelo NER opcional, **apagado por defecto** |

La detección no usa IA generativa y se hace en local. Sin el NER opcional, **los nombres de persona no se detectan**.

## Benchmark

Medido en la partición de test de [MEDDOCAN](docs/benchmark.md) (250 casos clínicos sintéticos en español, escritos por terceros) con el mismo script y las mismas métricas para cada herramienta. Cuanto más bajo, mejor: datos personales que quedan a la vista de cada 100.

| Datos a la vista (de cada 100) | Antifaz | Antifaz + NER | Presidio |
|---|---|---|---|
| Todos los datos personales anotados | 86,9 | 61,6 | 47,5 |
| Nombres de persona | 100 | 2,5 | 7,8 |
| Calles | 60,3 | 12,3 | 97,6 |
| Emails | 0,8 | 0,4 | 0,8 |
| Tiempo por documento (mediana) | 2,87 ms | 1947 ms (caché fría) | 27,53 ms |

- **Presidio deja menos datos a la vista en total** que Antifaz, con NER o sin él: también tapa lugares, organizaciones y fechas, que Antifaz todavía no busca. MEDDOCAN es texto clínico (edades, hospitales, fechas…) y no tiene DNI, IBAN ni tarjetas.
- **El NER no cumple el suelo de precisión fijado de antemano (85 %)**: cerca del 25 % de sus detecciones de nombres tapan texto que no es un dato personal (precisión del 75,2 %). Por eso es opcional y viene apagado.
- En un conjunto sintético de 600 textos con identificadores españoles, Antifaz deja 0 datos a la vista de cada 100 y Presidio 29,5. Ese conjunto lo escribió el mismo equipo que el detector, así que favorece a Antifaz.

Tablas completas, configuración y límites: [docs/benchmark.md](docs/benchmark.md). Lo puedes repetir con `make bench`.

## Seguridad

- **Cierra por defecto**: una petición se bloquea si el detector falla o si lleva una imagen, un PDF, un archivo u otro campo que no se puede enmascarar. El texto de un campo desconocido también se enmascara, no sale tal cual.
- **Guardia de salida**: una segunda comprobación sobre los bytes exactos que van a salir; si queda un valor oculto, la petición se bloquea.
- **Puerta cerrada**: todas las rutas piden la clave de Antifaz, se rechazan los navegadores, las URL y claves de los proveedores solo se leen de `.env`, y Antifaz no arranca con una clave débil o de ejemplo.
- **Probado atacándolo**: pasan 960 tests de red team; otros 13 son fallos conocidos documentados, escritos como fallos esperados. Un test de extremo a extremo arranca la imagen real de Docker contra un proveedor falso y comprueba que solo recibe marcadores.

¿Has encontrado un problema? Lee [SECURITY.md](SECURITY.md): las vulnerabilidades se avisan en privado. Un dato que Antifaz no detecta es una issue pública con **datos inventados** ([I found a leak](https://github.com/miquel-moreno/antifaz/issues/new?template=leak.yml)).

## Lo que Antifaz no es

- No anonimiza: **seudonimiza**. Solo protege lo que detecta, y ningún detector es perfecto.
- No evita que la IA deduzca cosas por el contexto (forma de escribir, detalles que quedan en el texto).
- No procesa imágenes ni archivos: los bloquea.
- No es un DLP de navegador, e instalarlo no convierte a nadie en "cumplidor del RGPD". Ayuda a minimizar los datos personales que se envían a los proveedores de IA; no es asesoría legal.

## Limitaciones conocidas

- Los nombres de persona solo se detectan con el NER opcional, que viene apagado.
- Muchos datos clínicos (edades, fechas, hospitales, números de historia) todavía no se detectan.
- No decodifica texto codificado (base64, hexadecimal).
- Todavía no hay límites de peticiones ni registro de evidencias (previstos para la v0.2).
- La imagen de Docker solo se ha probado en `linux/amd64`.

La lista completa está en [docs/TECNICO.md](docs/TECNICO.md#limitaciones).

## Telemetría

Antifaz no envía nada a ningún sitio salvo al proveedor que configures. No abre ninguna otra conexión de salida, no tiene analítica y no "llama a casa". El NER opcional funciona sin red, con un modelo que descargas una vez con `make ner-model`.

## Licencia

El código tiene licencia [Apache-2.0](LICENSE). El nombre "Antifaz" y el logo no entran en ella: ver [LICENSE-ASSETS.md](LICENSE-ASSETS.md).
