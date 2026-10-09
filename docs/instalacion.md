# Guía de instalación

[Read in English](install.md)

Esta guía es para una empresa que quiere tener Antifaz en su propia máquina o servidor y apuntar a él sus herramientas (los SDK de OpenAI o Anthropic, curl, Claude Code). Cada orden sale del código de la CLI y se ha comprobado con su `--help`.

Recuerda: Antifaz seudonimiza. Solo protege los datos personales que detecta. Pruébalo con tu tipo de textos antes de usarlo con datos reales.

> [!IMPORTANT]
> **Algunos pasos necesitan la v0.2.0, que todavía no está publicada.** Van marcados con **[v0.2.0]**. Hoy (v0.1.0) puedes seguir el camino marcado con **[v0.1.0]**: usa los archivos del repositorio en lugar de los de la release.

> [!NOTE]
> Las capturas de pantalla se añadirán después del prototipo de diseño (#30).

## Índice

1. [Requisitos](#1-requisitos)
2. [Paso a paso](#2-paso-a-paso)
3. [Si Antifaz está caído, y cómo apagarlo](#3-si-antifaz-está-caído-y-cómo-apagarlo)
4. [Tráfico de Claude Code que no pasa por Antifaz](#4-tráfico-de-claude-code-que-no-pasa-por-antifaz)
5. [Lo que Antifaz bloquea: imágenes, PDF y archivos](#5-lo-que-antifaz-bloquea-imágenes-pdf-y-archivos)
6. [Actualizar, limpiar copias y rotar claves](#6-actualizar-limpiar-copias-y-rotar-claves)
7. [Problemas frecuentes](#7-problemas-frecuentes)
8. [Lo que todavía no está probado](#8-lo-que-todavía-no-está-probado)

## 1. Requisitos

Para ejecutar la pasarela:

- **Docker con Compose.** Docker Desktop en Windows o macOS, o Docker Engine con el plugin de Compose en Linux. Compruébalo con `docker compose version`.
- La imagen solo está probada en `linux/amd64` (procesadores Intel o AMD). No está probada en ARM (por ejemplo, Apple Silicon o Raspberry Pi).
- La clave de al menos un proveedor: OpenAI, Anthropic o los dos.

Para ejecutar la CLI en tu ordenador (solo para `setup claude-code`, y opcional para `doctor` y `verify`):

- **Python 3.12** y **[uv](https://docs.astral.sh/uv/)**, y un clon del repositorio:

```bash
git clone https://github.com/miquel-moreno/antifaz
cd antifaz
uv sync
```

`antifaz setup claude-code` y `antifaz doctor` están en la rama `main`, no en la v0.1.0. Hasta que salga la v0.2.0, usa un clon de `main`.

## 2. Paso a paso

Antifaz solo escucha en `127.0.0.1` (esta máquina). Otras máquinas no llegan. Para usarlo desde otras máquinas, mira [Usarlo desde otras máquinas](#usarlo-desde-otras-máquinas).

### 2.1 Descargar el archivo de Compose

**[v0.2.0]** Cada release trae un `docker-compose.yml` que fija la imagen por digest (la imagen exacta que se construyó, se probó y se firmó). En una carpeta vacía:

Linux o macOS (bash):

```bash
mkdir antifaz && cd antifaz
curl -LO https://github.com/miquel-moreno/antifaz/releases/download/v0.2.0/docker-compose.yml
```

Windows (PowerShell). Usa `curl.exe`, no `curl` (en PowerShell 5.1, `curl` es otra orden):

```powershell
mkdir antifaz; cd antifaz
curl.exe -LO https://github.com/miquel-moreno/antifaz/releases/download/v0.2.0/docker-compose.yml
```

**[v0.1.0]** La release v0.1.0 no trae archivo de Compose. Usa el del repositorio: arranca la imagen publicada `ghcr.io/miquel-moreno/antifaz:0.1.0`.

```bash
git clone https://github.com/miquel-moreno/antifaz
cd antifaz
```

### 2.2 Escribir el archivo `.env`

El archivo `.env` guarda las claves. Antifaz lo lee al arrancar el contenedor. No lo subas nunca a git ni lo envíes por correo o chat.

**[v0.2.0] Con `antifaz init`** (en el contenedor, sin clonar). Pide las claves de los proveedores sin mostrarlas, escribe `.env` y crea una `ANTIFAZ_API_KEY` aleatoria nueva. Esa clave la muestra **una sola vez**: cópiala, es la que usan tus clientes. Ninguna clave va en la línea de órdenes.

Linux o macOS (bash):

```bash
docker run --rm -it -v "$PWD:/work" -w /work --user "$(id -u):$(id -g)" --network none \
  ghcr.io/miquel-moreno/antifaz:0.2.0 init
```

Windows (PowerShell), en una línea y sin `--user`:

```powershell
docker run --rm -it -v "${PWD}:/work" -w /work --network none ghcr.io/miquel-moreno/antifaz:0.2.0 init
```

Para qué sirve cada opción:

- `--rm`: borra el contenedor al terminar. Docker guarda la salida de un contenedor (con tu clave nueva) mientras el contenedor existe.
- `-it`: `init` necesita un terminal para pedir las claves. Sin él, `init` se para con "no terminal to ask the questions".
- `--network none`: `init` no usa la red.
- `--user` (solo Linux): el `.env` nuevo es tuyo y no del usuario del contenedor. Sin él, puede que no puedas leer el archivo.
- Para saltarte un proveedor, pulsa Enter cuando `init` pida su clave. Sus rutas responderán 503.
- Si ya existe un `.env`, `init` te pide que escribas `yes` y guarda una copia llamada `.env.bak-AAAAMMDD-HHMMSS`.
- Para scripts y CI está `--non-interactive` con `--openai-key-env VAR` o `--anthropic-key-env VAR` (el **nombre** de una variable de entorno, nunca la clave). Mira `antifaz init --help`.
- `init` también escribe siempre `ANTIFAZ_ADMIN_TOKEN`, un segundo valor aleatorio para el panel del navegador en `http://localhost:8000/panel` (llega en la v0.2). Se enseña una vez, como la clave, y **no** es la clave que usan tus clientes. **Para apagar el panel**, borra la línea `ANTIFAZ_ADMIN_TOKEN` de `.env` y reinicia (`docker compose up -d --force-recreate`).

> En Git Bash de Windows, usa PowerShell para esta orden: Git Bash cambia rutas como `/work` y la orden falla.

**[v0.1.0] A mano.** En la carpeta del clon:

```bash
cp .env.example .env
```

```powershell
Copy-Item .env.example .env
```

Después abre `.env` con un editor de texto y cambia:

- `ANTIFAZ_API_KEY`: un valor aleatorio de 32 caracteres o más. Para crear uno:
  - bash: `openssl rand -hex 32`
  - PowerShell: `$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') })`
- `ANTIFAZ_OPENAI_API_KEY` y/o `ANTIFAZ_ANTHROPIC_API_KEY`: las claves de tus proveedores. Borra la línea del proveedor que no uses (sus rutas responden 503).
- `ANTIFAZ_ADMIN_TOKEN`: el token del panel del navegador (llega en la v0.2). Otro valor aleatorio, hecho igual y distinto de `ANTIFAZ_API_KEY`. Si no quieres el panel, borra la línea.
- `ANTIFAZ_TRUSTED_PROXIES`: déjala vacía salvo que Antifaz esté detrás de un proxy inverso (mira [Usarlo desde otras máquinas](#usarlo-desde-otras-máquinas)).

Reglas para los valores: sin espacios, sin comillas y sin `$` (Compose intentaría sustituirlo). En Windows, guarda el archivo en **UTF-8** (mira [Problemas frecuentes](#7-problemas-frecuentes)).

Antifaz se niega a arrancar mientras una clave tenga el valor de ejemplo (`change-me...`).

### 2.3 Arrancar Antifaz

En la carpeta con `docker-compose.yml` y `.env` (la misma orden en bash y PowerShell):

```bash
docker compose up -d
```

Compruébalo:

```bash
docker compose ps                    # el servicio "antifaz" debe salir "healthy" a los pocos segundos
curl http://127.0.0.1:8000/healthz   # {"status":"ok","version":"..."}
```

En PowerShell, `curl.exe` en lugar de `curl`.

Si el servicio sale como `restarting`, Antifaz ha rechazado la configuración. Mira el motivo con `docker compose logs antifaz`: dice qué variable hay que arreglar, nunca su valor.

### 2.4 Revisarlo con `doctor`

**[v0.2.0]** `doctor` revisa la configuración y la pasarela en marcha. Nombra el ajuste que hay que arreglar, nunca su valor, y da los pasos siguientes.

```bash
docker compose exec antifaz antifaz doctor
```

Para probar también las claves de los proveedores, añade `--providers`. Solo pide a cada proveedor su lista de modelos, que es gratis:

```bash
docker compose exec antifaz antifaz doctor --providers
```

Dentro del contenedor `doctor` dice "no .env in that folder: only environment variables were read". Es normal: en el contenedor la configuración llega por variables de entorno, las mismas que usa la pasarela.

Código de salida: 0 si todo está bien, 1 si algo falla.

### 2.5 Demostrar que no sale ningún dato con `verify`

`verify` usa **tu** configuración, siembra datos personales falsos (DNI, NIE, IBAN, correo, teléfono, tarjeta) en cada parte de una petición y falla si algún valor llega a un proveedor falso. Nunca llama a un proveedor real y no cuesta nada.

```bash
docker compose exec antifaz antifaz verify
```

Funciona también con la v0.1.0. Desde un clon, `uv run antifaz verify` hace lo mismo con el `.env` de la carpeta actual.

Un buen resultado empieza por `antifaz verify: PASS`. Código de salida: 0 bien, 1 una comprobación falla, 2 la configuración no puede arrancar.

### 2.6 Apuntar tus clientes a Antifaz

Tus clientes usan la **clave de Antifaz** (`ANTIFAZ_API_KEY`), no la del proveedor. Las claves de los proveedores se quedan en `.env`.

**SDK de OpenAI (Python):**

```python
import os

from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key=os.environ["ANTIFAZ_API_KEY"])
```

**SDK de Anthropic (Python):** sin `/v1` al final.

```python
import os

from anthropic import Anthropic

client = Anthropic(base_url="http://127.0.0.1:8000", api_key=os.environ["ANTIFAZ_API_KEY"])
```

**curl** (bash), con datos inventados:

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $ANTIFAZ_API_KEY" -H "Content-Type: application/json" \
  -d '{"model": "gpt-4.1-nano", "messages": [{"role": "user", "content": "Mi DNI es 12345678Z"}]}'
```

PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/v1/chat/completions" `
  -Headers @{ "Authorization" = "Bearer $env:ANTIFAZ_API_KEY" } -ContentType "application/json" `
  -Body '{"model": "gpt-4.1-nano", "messages": [{"role": "user", "content": "Mi DNI es 12345678Z"}]}'
```

El proveedor recibe `[[ES_DNI_1]]` y la respuesta vuelve con `12345678Z`.

**Claude Code.** En el ordenador donde usas Claude Code, desde un clon (necesita **[v0.2.0]** o un clon de `main`):

```bash
uv run antifaz setup claude-code            # enseña el cambio; no escribe nada
uv run antifaz setup claude-code --apply    # lo escribe (pide que escribas yes y guarda una copia)
```

- Solo pone `ANTHROPIC_BASE_URL` en tu configuración de usuario de Claude Code: `~/.claude/settings.json` (en Windows, `%USERPROFILE%\.claude\settings.json`). Con `--project` escribe en su lugar `.claude/settings.local.json` de la carpeta actual.
- **Nunca** escribe una clave. Pon tu clave de Antifaz en la variable de entorno `ANTHROPIC_AUTH_TOKEN`:
  - bash o zsh: añade esta línea a `~/.bashrc` o `~/.zshrc` con un editor de texto y abre un terminal nuevo:

    ```bash
    export ANTHROPIC_AUTH_TOKEN=<tu ANTIFAZ_API_KEY>
    ```

  - PowerShell (permanente para tu usuario de Windows; abre un terminal nuevo después):

    ```powershell
    [Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN", "<tu ANTIFAZ_API_KEY>", "User")
    ```

    Una clave escrita en el prompt se queda en el historial de la shell. También puedes ponerla desde Windows: "Editar las variables de entorno de esta cuenta".
- Reinicia Claude Code. `/status` debe mostrar la línea "Anthropic base URL" con la dirección de Antifaz.
- Si Antifaz está en otra dirección, añade `--url`, por ejemplo `--url https://antifaz.ejemplo.internal`.
- Configura la CLI de Claude Code. La extensión de VS Code y la app de escritorio leen su propia configuración ([documentación de Claude Code](https://code.claude.com/docs/en/llm-gateway-connect)); esta orden no las cambia.

### Usarlo desde otras máquinas

Antifaz habla HTTP sin cifrar y solo escucha en `127.0.0.1`. Para usarlo desde otras máquinas:

1. Pon delante un proxy inverso con HTTPS (Caddy, nginx, Traefik). No publiques el puerto en `0.0.0.0`.
2. Añade a `ANTIFAZ_ALLOWED_HOSTS` en `.env` el nombre que usan los clientes (por ejemplo `antifaz.ejemplo.internal`) y ejecuta `docker compose up -d --force-recreate`.
3. El proxy no debe guardar los cuerpos de las peticiones en sus logs: llevan los datos personales antes de enmascararse.
4. Solo para el panel (v0.2): pon en `ANTIFAZ_TRUSTED_PROXIES` la IP o el rango CIDR del proxy, tal como lo ve Antifaz (por ejemplo, la red de Docker del proxy, `172.18.0.0/16`). Solo así el panel se fía de las cabeceras `X-Forwarded-For` y `X-Forwarded-Proto` del proxy. Nunca `0.0.0.0/0`. Sin proxy, déjala vacía.

Más detalles en [TECNICO.md](TECNICO.md#proxy-inverso-https).

## 3. Si Antifaz está caído, y cómo apagarlo

**Si Antifaz está caído**, tu cliente recibe un error de conexión y **no se envía nada** al proveedor. El cliente no se va solo al proveedor directamente. En Claude Code verás un error como "Connection refused" o "Unable to connect to API".

**Para apagarlo en un minuto** (volver a ir directo al proveedor):

1. Claude Code: quita el ajuste, desde el clon:

   ```bash
   uv run antifaz setup claude-code --uninstall --apply
   ```

   Si al instalarlo usaste `--project` o `--url`, añádelos también aquí. Solo quita el valor si nadie lo ha cambiado.
2. Quita la clave de Antifaz de tu entorno:
   - bash o zsh: borra la línea `export ANTHROPIC_AUTH_TOKEN=...` de `~/.bashrc` o `~/.zshrc` y ejecuta `unset ANTHROPIC_AUTH_TOKEN`.
   - PowerShell: `[Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN", $null, "User")` y abre un terminal nuevo.
3. Reinicia Claude Code. En tu código con los SDK, vuelve a poner el `base_url` y la clave de antes.
4. Para la pasarela, en su carpeta:

   ```bash
   docker compose down
   ```

## 4. Tráfico de Claude Code que no pasa por Antifaz

Con `ANTHROPIC_BASE_URL`, Claude Code manda sus **peticiones al modelo** a Antifaz. Otras peticiones van **directamente** a Anthropic o a otros servicios. Antifaz no las ve ni las enmascara. Según la documentación de Claude Code ([protocolo de pasarela](https://code.claude.com/docs/en/llm-gateway-protocol), [conectar a una pasarela](https://code.claude.com/docs/en/llm-gateway-connect)):

- **La telemetría y otro tráfico no esencial** (comprobación de versiones, notas de versión y parecidos) va a Anthropic y a otros servicios como GitHub. Desde Claude Code v2.1.246 no lleva la clave de la pasarela.
- **La comprobación del modo rápido** (fast mode) llama directamente a `api.anthropic.com`.
- **La comprobación de seguridad de dominios de WebFetch** llama directamente a `api.anthropic.com` antes de descargar una página.

Para apagar el tráfico no esencial, pon esta variable junto a `ANTHROPIC_AUTH_TOKEN`:

```bash
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
```

```powershell
[Environment]::SetEnvironmentVariable("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1", "User")
```

Lo que pierdes a cambio:

- **Se paran las actualizaciones automáticas.** Actualiza Claude Code de otra forma (tu gestor de paquetes o la herramienta de software de tu empresa).
- **El modo rápido** (`/fast`) dice que no está disponible.
- **No para la comprobación de WebFetch.** Para pararla también, añade `"skipWebFetchPreflight": true` a tu configuración de Claude Code.

Además, con una clave de pasarela, Remote Control y el dictado por voz de Claude Code no están disponibles (necesitan iniciar sesión con claude.ai).

## 5. Lo que Antifaz bloquea: imágenes, PDF y archivos

Antifaz no puede buscar datos personales dentro de una imagen, un PDF o un archivo. Por eso **bloquea** cualquier petición que lleve uno, en vez de enviarlo sin revisar ("fallo seguro", [ADR-0006](adr/0006-campos-desconocidos.md)). El cliente recibe:

```json
{"error": {"code": "antifaz_blocked", "message": "attachments are not supported; request blocked"}}
```

con el código HTTP 400. Lo mismo pasa con identificadores de archivo, documentos, citas y herramientas del servidor de Anthropic (como la búsqueda web).

Qué significa para quien usa Claude Code:

- **Pegar una captura de pantalla**, o pedir a Claude Code que lea una imagen o un PDF, hace que esa petición falle con este error. No se envía nada al proveedor.
- La imagen se queda en la conversación, así que probablemente los mensajes siguientes también fallen. Empieza una conversación nueva con `/clear`.
- Copia como texto lo que necesites, en lugar de una captura.

Este comportamiento sale de los tests de la pasarela. Todavía no está comprobado con una sesión real de Claude Code (mira la [sección 8](#8-lo-que-todavía-no-está-probado)).

## 6. Actualizar, limpiar copias y rotar claves

### Actualizar

Lee antes el [CHANGELOG](../CHANGELOG.md) de la versión nueva: una línea "Breaking" significa que tienes que cambiar algo.

**[v0.2.0 y siguientes]** Con el archivo de Compose de la release, en su carpeta (cambia `vX.Y.Z` por la versión nueva):

```bash
curl -LO https://github.com/miquel-moreno/antifaz/releases/download/vX.Y.Z/docker-compose.yml
docker compose pull
docker compose up -d
docker compose exec antifaz antifaz doctor
```

En PowerShell, `curl.exe` en lugar de `curl`. Tu `.env` se queda igual. Para volver atrás, descarga el archivo de Compose de la versión anterior y repite las mismas órdenes.

**[v0.1.0]** Con el clon: `git pull`, y después `docker compose pull` y `docker compose up -d`. El archivo de Compose del repositorio usa la última versión publicada. Para elegir una, pon `ANTIFAZ_VERSION` (por ejemplo `ANTIFAZ_VERSION=0.1.0 docker compose up -d`).

De la 0.1.0 a la 0.2.0 la imagen arranca con `antifaz serve` en lugar de `uvicorn ...`. El archivo de Compose y la orden `docker run` del README siguen funcionando. Un `docker run IMAGEN <opciones de uvicorn>` ya no funciona.

### Limpiar copias

Antifaz guarda una copia cada vez que sustituye un archivo. **Las copias llevan tus claves o ajustes antiguos.** Bórralas cuando ya no las necesites:

- `.env.bak-AAAAMMDD-HHMMSS`, junto a `.env` (las hace `init`):
  - bash: `rm .env.bak-*`
  - PowerShell: `Remove-Item .env.bak-*`
- `settings.json.bak-AAAAMMDD-HHMMSS`, junto a `~/.claude/settings.json` (las hace `setup claude-code`).
- Con `--project`: `~/.claude/antifaz-backups/` (en Windows, `%USERPROFILE%\.claude\antifaz-backups\`).

`setup claude-code` imprime la ruta completa de cada copia al escribirla.

### Rotar claves

**Una clave de proveedor** (OpenAI o Anthropic):

1. Crea una clave nueva en la consola del proveedor.
2. Ponla en `.env` (edita la línea, o vuelve a ejecutar `init`: mira la nota de abajo).
3. Reinicia Antifaz para que vuelva a leer `.env`: `docker compose up -d --force-recreate`. (`docker compose restart` **no** vuelve a leer `.env`.)
4. Compruébalo: `docker compose exec antifaz antifaz doctor --providers` **[v0.2.0]**.
5. Revoca la clave antigua en la consola del proveedor.
6. Borra las copias `.env.bak-*` que llevan la clave antigua.

**La clave de Antifaz** (`ANTIFAZ_API_KEY`):

1. Crea un valor aleatorio nuevo (mira [2.2](#22-escribir-el-archivo-env)) y ponlo en `.env`.
2. `docker compose up -d --force-recreate`. A partir de ahora la clave antigua recibe 401.
3. Da la clave nueva a tus clientes: `ANTHROPIC_AUTH_TOKEN` para Claude Code (reinícialo) y la clave de tu código con los SDK.
4. Borra las copias `.env.bak-*`.

Nota: `init` **[v0.2.0]** siempre crea una `ANTIFAZ_API_KEY` nueva y vuelve a pedir las dos claves de proveedor. Ejecutarlo otra vez las cambia todas de una vez.

## 7. Problemas frecuentes

| Qué ves | Por qué | Qué hacer |
|---|---|---|
| `401` `{"error":{"code":"unauthorized",...}}` | El cliente no envió la clave de Antifaz, o envió otra (por ejemplo, una clave real de un proveedor). | Usa la `ANTIFAZ_API_KEY` de `.env` en `Authorization: Bearer <clave>` o `x-api-key: <clave>`, no las dos con valores distintos. `Bearer` con B mayúscula. En Claude Code: `ANTHROPIC_AUTH_TOKEN`, y abre un terminal nuevo después de ponerla. |
| `400` `Invalid host header` | El cliente usó un nombre de host que no está en `ANTIFAZ_ALLOWED_HOSTS`. | Añade el nombre a `ANTIFAZ_ALLOWED_HOSTS` en `.env` (separado por comas) y `docker compose up -d --force-recreate`. |
| `403` `origin_not_allowed` | La petición viene de un navegador (lleva cabecera `Origin`). Los navegadores se rechazan por defecto. | Llama a Antifaz desde un servidor o un script. Solo si de verdad necesitas un navegador, añade su origen exacto a `ANTIFAZ_ALLOWED_ORIGINS`. |
| `415` `unsupported_media_type` | El cuerpo no se envía como JSON. | Envía `Content-Type: application/json`. |
| `400` `antifaz_blocked` | La petición lleva una imagen, un PDF, un archivo o datos en un sitio que no se puede enmascarar. | Mira la [sección 5](#5-lo-que-antifaz-bloquea-imágenes-pdf-y-archivos). Envía solo texto. |
| `503` `not_configured` | Ese proveedor no tiene clave en `.env`. | Añade su clave y `docker compose up -d --force-recreate`. |
| Antifaz no arranca; `docker compose ps` dice `restarting` | La configuración se rechaza, por ejemplo una clave débil o un valor de ejemplo (`change-me...`). | `docker compose logs antifaz` dice la variable y el motivo (nunca el valor), por ejemplo `ANTIFAZ_API_KEY still has the example value from .env.example`. Arregla `.env` y `docker compose up -d`. |
| Antifaz no arranca con `ANTIFAZ_NER_ENABLED=true` | La imagen publicada no trae el NER (el detector de nombres) ni su modelo. | Pon `ANTIFAZ_NER_ENABLED=false`. Hoy el NER solo funciona desde un clon (`make ner-model`, unos 1,16 GB); mira [TECNICO.md](TECNICO.md). |
| Conexión rechazada, o Claude Code dice que no puede conectar | Antifaz no está en marcha, o la dirección o el puerto están mal. | `docker compose ps` y después `docker compose up -d`. Revisa la URL (`http://127.0.0.1:8000` por defecto). |
| `502` o `504` | El proveedor no respondió bien o a tiempo. | Mira el estado del proveedor y tu red. `doctor --providers` **[v0.2.0]** prueba las claves. |
| `init` dice "no terminal to ask the questions" | `docker run` sin `-it`. | Añade `-it`, o usa `--non-interactive`. |
| Linux: "Permission denied" al leer `.env` | `init` se ejecutó sin `--user`, así que el archivo es del usuario del contenedor. | `sudo chown "$(id -u):$(id -g)" .env`, y la próxima vez usa `--user "$(id -u):$(id -g)"`. |
| Windows: `doctor` dice "cannot read .env as UTF-8 text", o Antifaz no arranca aunque `.env` parece bien | `.env` se guardó con otra codificación. En Windows PowerShell 5.1, `>` y `Out-File` escriben UTF-16 y `Set-Content` escribe ANSI. | Abre `.env` con el Bloc de notas, "Guardar como", codificación **UTF-8**. No crees `.env` con `>`, `Out-File` ni `Set-Content`. Usa `init` o `Copy-Item .env.example .env`. |

## 8. Lo que todavía no está probado

- **Falta una sesión real de Claude Code a través de Antifaz.** `setup claude-code` y la ruta de Anthropic están probados con el SDK oficial de Anthropic y con respuestas grabadas, pero todavía no con Claude Code contra la API real de Anthropic. Se hará cuando haya crédito de Anthropic. Hasta entonces, lo que esta guía dice de Claude Code sale de los tests y de la [documentación de Claude Code](https://code.claude.com/docs/en/llm-gateway-connect).
- La ruta de OpenAI se probó una vez contra la API real de OpenAI (2026-09-30).
- Los pasos **[v0.2.0]** usan el código de `main`. Están probados de extremo a extremo con la imagen construida desde el repositorio, pero la v0.2.0 todavía no está publicada.
