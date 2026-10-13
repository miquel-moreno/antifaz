# Guía de instalación

[Read in English](install.md)

Esta guía es para una empresa que quiere tener Antifaz en su propia máquina o servidor y apuntar a él sus herramientas (los SDK de OpenAI o Anthropic, curl, Claude Code).

Recuerda: Antifaz seudonimiza. Solo protege los datos personales que detecta. Pruébalo con tu tipo de textos antes de usarlo con datos reales.

> [!IMPORTANT]
> **Hay dos caminos. No los mezcles.**
>
> - **[Sección 2: Instalar la v0.1.1 publicada (hoy)](#2-instalar-la-v011-publicada-hoy).** Todas sus órdenes funcionan con la imagen publicada `ghcr.io/miquel-moreno/antifaz:0.1.1`.
> - **[Sección 3: Llega con la v0.2.0 (hoy solo en `main`)](#3-llega-con-la-v020-hoy-solo-en-main).** `init`, `doctor`, `setup claude-code`, el panel y el archivo de Compose de la release. La v0.2.0 todavía no está publicada. La imagen 0.1.1 no tiene esas órdenes: responde "invalid arguments".

> [!NOTE]
> Las capturas de pantalla se añadirán después del prototipo de diseño (#30).

## Índice

1. [Requisitos](#1-requisitos)
2. [Instalar la v0.1.1 publicada (hoy)](#2-instalar-la-v011-publicada-hoy)
3. [Llega con la v0.2.0 (hoy solo en `main`)](#3-llega-con-la-v020-hoy-solo-en-main)
4. [Si Antifaz está caído, y cómo apagarlo](#4-si-antifaz-está-caído-y-cómo-apagarlo)
5. [Tráfico de Claude Code que no pasa por Antifaz](#5-tráfico-de-claude-code-que-no-pasa-por-antifaz)
6. [Lo que Antifaz bloquea: imágenes, PDF y archivos](#6-lo-que-antifaz-bloquea-imágenes-pdf-y-archivos)
7. [Actualizar, limpiar copias y rotar claves](#7-actualizar-limpiar-copias-y-rotar-claves)
8. [Problemas frecuentes](#8-problemas-frecuentes)
9. [Lo que todavía no está probado](#9-lo-que-todavía-no-está-probado)

## 1. Requisitos

Para ejecutar la pasarela (sección 2):

- **Docker con Compose.** Docker Desktop en Windows o macOS, o Docker Engine con el plugin de Compose en Linux. Compruébalo con `docker compose version`.
- **git**, para bajar el archivo de Compose y el archivo de ajustes de ejemplo.
- La imagen solo está probada en `linux/amd64` (procesadores Intel o AMD). No está probada en ARM (por ejemplo, Apple Silicon o Raspberry Pi).
- La clave de al menos un proveedor: OpenAI, Anthropic o los dos.

Solo para la sección 3 (lo de la v0.2.0, desde un clon de `main`): **Python 3.12** y **[uv](https://docs.astral.sh/uv/)**.

## 2. Instalar la v0.1.1 publicada (hoy)

Todo lo de esta sección funciona con la imagen publicada `ghcr.io/miquel-moreno/antifaz:0.1.1`. Nada de aquí necesita la sección 3.

Antifaz solo escucha en `127.0.0.1` (esta máquina). Otras máquinas no llegan. Para usarlo desde otras máquinas, mira [2.7](#27-usarlo-desde-otras-máquinas).

### 2.1 Bajar los archivos

La release v0.1.1 no trae archivo de Compose. Usa el del repositorio: arranca la imagen publicada `ghcr.io/miquel-moreno/antifaz:0.1.1`. El clon trae también el archivo de ajustes de ejemplo, `.env.example`.

Linux o macOS (bash):

```bash
git clone https://github.com/miquel-moreno/antifaz
cd antifaz
```

Windows (PowerShell):

```powershell
git clone https://github.com/miquel-moreno/antifaz
cd antifaz
```

### 2.2 Escribir el archivo `.env`

El archivo `.env` guarda las claves. Antifaz lo lee al arrancar el contenedor. No lo subas nunca a git ni lo envíes por correo o chat.

Copia el archivo de ejemplo. bash:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Después abre `.env` con un editor de texto y cambia:

- `ANTIFAZ_API_KEY`: un valor aleatorio de 32 caracteres o más. Es la clave que usan tus clientes. Para crear uno:
  - bash: `openssl rand -hex 32`
  - PowerShell: `$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') })`
- `ANTIFAZ_OPENAI_API_KEY` y/o `ANTIFAZ_ANTHROPIC_API_KEY`: las claves de tus proveedores. Borra la línea del proveedor que no uses (sus rutas responden 503).
- `ANTIFAZ_ADMIN_TOKEN`: **genéralo o borra la línea.** La 0.1.1 ignora esta línea. Es para el panel del navegador, que llega con la v0.2.0. Pero la v0.2.0 **no arranca** mientras tenga el valor de ejemplo (`change-me...`). Así que haz ahora una de estas dos cosas:
  - crea otro valor aleatorio, igual que el de arriba y **distinto de** `ANTIFAZ_API_KEY`; o
  - borra la línea. Así el panel se queda apagado en la v0.2.0.
- `ANTIFAZ_TRUSTED_PROXIES`: déjala vacía. La 0.1.1 la ignora (es para el panel de la v0.2.0 detrás de un proxy inverso).

Reglas para los valores: sin espacios, sin comillas y sin `$` (Compose intentaría sustituirlo). En Windows, guarda el archivo en **UTF-8** (mira [Problemas frecuentes](#8-problemas-frecuentes)).

Antifaz se niega a arrancar mientras `ANTIFAZ_API_KEY` o una clave de proveedor tenga el valor de ejemplo (`change-me...`).

### 2.3 Arrancar Antifaz

En la carpeta del clon (la misma orden en bash y PowerShell):

```bash
docker compose up -d
```

Descarga `ghcr.io/miquel-moreno/antifaz:0.1.1`. Compruébalo:

```bash
docker compose ps                    # el servicio "antifaz" debe salir "healthy" a los pocos segundos
curl http://127.0.0.1:8000/healthz   # {"status":"ok","version":"0.1.1"}
```

En PowerShell, `curl.exe` en lugar de `curl`:

```powershell
docker compose ps
curl.exe http://127.0.0.1:8000/healthz
```

Si el servicio sale como `restarting`, Antifaz ha rechazado la configuración. Mira el motivo:

```bash
docker compose logs antifaz
```

Dice qué variable hay que arreglar, nunca su valor. Arregla `.env` y vuelve a lanzar `docker compose up -d`.

### 2.4 Sin Compose: un solo `docker run`

El mismo endurecimiento que el archivo de Compose, en una sola orden (bash, en la carpeta con `.env`):

```bash
docker run -d --name antifaz --env-file .env -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp:size=16m,mode=1777,noexec,nosuid,nodev \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 512m --cpus 1 --pids-limit 128 --restart unless-stopped \
  ghcr.io/miquel-moreno/antifaz:0.1.1
```

En PowerShell, escríbela en una sola línea, o termina cada línea con una comilla invertida `` ` `` en lugar de `\`. Usa esto **o** `docker compose up -d`, no los dos: usan el mismo puerto. El resto de la guía usa Compose.

### 2.5 Demostrar que no sale ningún dato con `verify`

`verify` usa **tu** configuración, siembra datos personales falsos (DNI, NIE, IBAN, correo, teléfono, tarjeta) en cada parte de una petición y falla si algún valor llega a un proveedor falso. Nunca llama a un proveedor real y no cuesta nada.

```bash
docker compose exec antifaz antifaz verify
```

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

Para Claude Code, mira [3.4](#34-claude-code-setup-claude-code).

### 2.7 Usarlo desde otras máquinas

Antifaz habla HTTP sin cifrar y solo escucha en `127.0.0.1`. Para usarlo desde otras máquinas:

1. Pon delante un proxy inverso con HTTPS (Caddy, nginx, Traefik). No publiques el puerto en `0.0.0.0`.
2. Añade a `ANTIFAZ_ALLOWED_HOSTS` en `.env` el nombre que usan los clientes (por ejemplo `antifaz.ejemplo.internal`) y ejecuta `docker compose up -d --force-recreate`.
3. El proxy no debe guardar los cuerpos de las peticiones en sus logs: llevan los datos personales antes de enmascararse.

Para el panel detrás de un proxy (v0.2.0), mira [3.5](#35-el-panel).

Más detalles en [TECNICO.md](TECNICO.md#proxy-inverso-https).

## 3. Llega con la v0.2.0 (hoy solo en `main`)

> [!WARNING]
> **Todavía no está publicada.** Nada de esta sección funciona con la imagen 0.1.1: `docker compose exec antifaz antifaz doctor` responde "invalid arguments", y los enlaces de descarga de la release dan "Not Found" hasta que salga la v0.2.0. Los pasos están probados de extremo a extremo con una imagen construida desde `main`. Para hoy, usa la [sección 2](#2-instalar-la-v011-publicada-hoy).

### 3.1 El archivo de Compose de la release

Cada release desde la v0.2.0 trae un `docker-compose.yml` que fija la imagen por digest (la imagen exacta que se construyó, se probó y se firmó). No hace falta clonar. En una carpeta vacía:

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

### 3.2 Escribir `.env` con `antifaz init`

En el contenedor, sin clonar. Pide las claves de los proveedores sin mostrarlas, escribe `.env` y crea una `ANTIFAZ_API_KEY` aleatoria nueva. Esa clave la muestra **una sola vez**: cópiala, es la que usan tus clientes. Ninguna clave va en la línea de órdenes.

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
- `init` también escribe siempre `ANTIFAZ_ADMIN_TOKEN`, un segundo valor aleatorio para el panel (mira [3.5](#35-el-panel)). Se enseña una vez, como la clave, y **no** es la clave que usan tus clientes.

> En Git Bash de Windows, usa PowerShell para esta orden: Git Bash cambia rutas como `/work` y la orden falla.

Después arráncalo con `docker compose up -d`, como en [2.3](#23-arrancar-antifaz).

### 3.3 Revisarlo con `doctor`

`doctor` revisa la configuración y la pasarela en marcha. Nombra el ajuste que hay que arreglar, nunca su valor, y da los pasos siguientes.

```bash
docker compose exec antifaz antifaz doctor
```

Para probar también las claves de los proveedores, añade `--providers`. Solo pide a cada proveedor su lista de modelos, que es gratis:

```bash
docker compose exec antifaz antifaz doctor --providers
```

Dentro del contenedor `doctor` dice "no .env in that folder: only environment variables were read". Es normal: en el contenedor la configuración llega por variables de entorno, las mismas que usa la pasarela.

Código de salida: 0 si todo está bien, 1 si algo falla.

### 3.4 Claude Code: `setup claude-code`

En el ordenador donde usas Claude Code, desde un clon de `main` (con [uv](https://docs.astral.sh/uv/)) hasta que salga la v0.2.0. Esta orden se ejecuta en tu ordenador, no en el contenedor. Solo escribe un ajuste de Claude Code, así que la pasarela puede ser la 0.1.1.

```bash
git clone https://github.com/miquel-moreno/antifaz
cd antifaz
uv sync
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

### 3.5 El panel

Un panel en el navegador, en `http://localhost:8000/panel`, está en desarrollo (issue #53). Se abre con `ANTIFAZ_ADMIN_TOKEN`, no con la clave que usan tus clientes.

- **Para apagar el panel**, borra la línea `ANTIFAZ_ADMIN_TOKEN` de `.env` y reinicia (`docker compose up -d --force-recreate`).
- Detrás de un proxy inverso, pon en `ANTIFAZ_TRUSTED_PROXIES` la **IP exacta del proxy**, tal como la ve Antifaz (una sola dirección, por ejemplo `172.20.0.10`). En Compose, dale al contenedor del proxy una dirección fija (`ipv4_address` en su red) para que no cambie. Solo así el panel se fía de las cabeceras `X-Forwarded-For` y `X-Forwarded-Proto` del proxy.
  - **Nunca** pongas la puerta de enlace de Docker (`172.x.0.1`) ni la red de Docker entera: las peticiones que entran por el puerto publicado llegan desde esa puerta de enlace, así que cualquier cliente podría falsear esas cabeceras. Nunca `0.0.0.0/0`. `antifaz doctor` avisa de los rangos más anchos que /24.
  - El proxy tiene que añadir `X-Forwarded-For`. Si no lo añade, todos los clientes parecen el proxy y comparten un único límite de intentos de login (5 fallos por minuto).
  - Sin proxy, déjala vacía.

## 4. Si Antifaz está caído, y cómo apagarlo

**Si Antifaz está caído**, tu cliente recibe un error de conexión y **no se envía nada** al proveedor. El cliente no se va solo al proveedor directamente. En Claude Code verás un error como "Connection refused" o "Unable to connect to API".

**Para apagarlo en un minuto** (volver a ir directo al proveedor):

1. Claude Code: quita el ajuste, desde el clon (mira [3.4](#34-claude-code-setup-claude-code)):

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

## 5. Tráfico de Claude Code que no pasa por Antifaz

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

## 6. Lo que Antifaz bloquea: imágenes, PDF y archivos

Antifaz no puede buscar datos personales dentro de una imagen, un PDF o un archivo. Por eso **bloquea** cualquier petición que lleve uno, en vez de enviarlo sin revisar ("fallo seguro", [ADR-0006](adr/0006-campos-desconocidos.md)). El cliente recibe:

```json
{"error": {"code": "antifaz_blocked", "message": "attachments are not supported; request blocked"}}
```

con el código HTTP 400. Lo mismo pasa con identificadores de archivo, documentos, citas y herramientas del servidor de Anthropic (como la búsqueda web).

Qué significa para quien usa Claude Code:

- **Pegar una captura de pantalla**, o pedir a Claude Code que lea una imagen o un PDF, hace que esa petición falle con este error. No se envía nada al proveedor.
- La imagen se queda en la conversación, así que probablemente los mensajes siguientes también fallen. Empieza una conversación nueva con `/clear`.
- Copia como texto lo que necesites, en lugar de una captura.

Este comportamiento sale de los tests de la pasarela. Todavía no está comprobado con una sesión real de Claude Code (mira la [sección 9](#9-lo-que-todavía-no-está-probado)).

## 7. Actualizar, limpiar copias y rotar claves

### Actualizar

Lee antes el [CHANGELOG](../CHANGELOG.md) de la versión nueva: una línea "Breaking" significa que tienes que cambiar algo.

**Con la 0.1.1 (el clon, sección 2)**: `git pull`, y después `docker compose pull` y `docker compose up -d`. El archivo de Compose del repositorio usa la última versión publicada. Para elegir una, pon `ANTIFAZ_VERSION` (por ejemplo `ANTIFAZ_VERSION=0.1.1 docker compose up -d` en bash).

**[v0.2.0 y siguientes]** Con el archivo de Compose de la release, en su carpeta (cambia `vX.Y.Z` por la versión nueva):

```bash
curl -LO https://github.com/miquel-moreno/antifaz/releases/download/vX.Y.Z/docker-compose.yml
docker compose pull
docker compose up -d
docker compose exec antifaz antifaz doctor
```

En PowerShell, `curl.exe` en lugar de `curl`. Tu `.env` se queda igual. Para volver atrás, descarga el archivo de Compose de la versión anterior y repite las mismas órdenes.

De la 0.1.x a la 0.2.0 la imagen arranca con `antifaz serve` en lugar de `uvicorn ...`. El archivo de Compose y la orden `docker run` de [2.4](#24-sin-compose-un-solo-docker-run) siguen funcionando. Un `docker run IMAGEN <opciones de uvicorn>` ya no funciona. Antes de pasar a la 0.2.0, revisa `ANTIFAZ_ADMIN_TOKEN` en `.env` (mira [2.2](#22-escribir-el-archivo-env)): con el valor de ejemplo, la 0.2.0 no arranca.

### Limpiar copias

Antifaz guarda una copia cada vez que sustituye un archivo. **Las copias llevan tus claves o ajustes antiguos.** Bórralas cuando ya no las necesites:

- `.env.bak-AAAAMMDD-HHMMSS`, junto a `.env` (las hace `init`, v0.2.0):
  - bash: `rm .env.bak-*`
  - PowerShell: `Remove-Item .env.bak-*`
- `settings.json.bak-AAAAMMDD-HHMMSS`, junto a `~/.claude/settings.json` (las hace `setup claude-code`).
- Con `--project`: `~/.claude/antifaz-backups/` (en Windows, `%USERPROFILE%\.claude\antifaz-backups\`).

`setup claude-code` imprime la ruta completa de cada copia al escribirla.

### Rotar claves

**Una clave de proveedor** (OpenAI o Anthropic):

1. Crea una clave nueva en la consola del proveedor.
2. Ponla en `.env` (edita la línea; con la v0.2.0 también puedes volver a ejecutar `init`: mira la nota de abajo).
3. Reinicia Antifaz para que vuelva a leer `.env`: `docker compose up -d --force-recreate`. (`docker compose restart` **no** vuelve a leer `.env`.)
4. Compruébalo. Con la 0.1.1 no hay ninguna orden que pruebe una clave de proveedor: envía una petición pequeña desde tu cliente ([2.6](#26-apuntar-tus-clientes-a-antifaz)). Con la v0.2.0: `docker compose exec antifaz antifaz doctor --providers`.
5. Revoca la clave antigua en la consola del proveedor.
6. Borra las copias `.env.bak-*` que llevan la clave antigua, si tienes.

**La clave de Antifaz** (`ANTIFAZ_API_KEY`):

1. Crea un valor aleatorio nuevo (mira [2.2](#22-escribir-el-archivo-env)) y ponlo en `.env`.
2. `docker compose up -d --force-recreate`. A partir de ahora la clave antigua recibe 401.
3. Da la clave nueva a tus clientes: `ANTHROPIC_AUTH_TOKEN` para Claude Code (reinícialo) y la clave de tu código con los SDK.
4. Borra las copias `.env.bak-*`, si tienes.

Nota: `init` **[v0.2.0]** siempre crea una `ANTIFAZ_API_KEY` nueva y vuelve a pedir las dos claves de proveedor. Ejecutarlo otra vez las cambia todas de una vez.

## 8. Problemas frecuentes

Con la 0.1.1 tus dos herramientas son `docker compose logs antifaz` (por qué no arranca) y `docker compose exec antifaz antifaz verify` (que no sale ningún dato). `antifaz doctor` llega con la v0.2.0.

| Qué ves | Por qué | Qué hacer |
|---|---|---|
| `401` `{"error":{"code":"unauthorized",...}}` | El cliente no envió la clave de Antifaz, o envió otra (por ejemplo, una clave real de un proveedor). | Usa la `ANTIFAZ_API_KEY` de `.env` en `Authorization: Bearer <clave>` o `x-api-key: <clave>`, no las dos con valores distintos. `Bearer` con B mayúscula. En Claude Code: `ANTHROPIC_AUTH_TOKEN`, y abre un terminal nuevo después de ponerla. |
| `400` `Invalid host header` | El cliente usó un nombre de host que no está en `ANTIFAZ_ALLOWED_HOSTS`. | Añade el nombre a `ANTIFAZ_ALLOWED_HOSTS` en `.env` (separado por comas) y `docker compose up -d --force-recreate`. |
| `403` `origin_not_allowed` | La petición viene de un navegador (lleva cabecera `Origin`). Los navegadores se rechazan por defecto. | Llama a Antifaz desde un servidor o un script. Solo si de verdad necesitas un navegador, añade su origen exacto a `ANTIFAZ_ALLOWED_ORIGINS`. |
| `415` `unsupported_media_type` | El cuerpo no se envía como JSON. | Envía `Content-Type: application/json`. |
| `400` `antifaz_blocked` | La petición lleva una imagen, un PDF, un archivo o datos en un sitio que no se puede enmascarar. | Mira la [sección 6](#6-lo-que-antifaz-bloquea-imágenes-pdf-y-archivos). Envía solo texto. |
| `503` `not_configured` | Ese proveedor no tiene clave en `.env`. | Añade su clave y `docker compose up -d --force-recreate`. |
| Antifaz no arranca; `docker compose ps` dice `restarting` | La configuración se rechaza, por ejemplo una clave débil o un valor de ejemplo (`change-me...`). | `docker compose logs antifaz` dice la variable y el motivo (nunca el valor), por ejemplo `ANTIFAZ_API_KEY still has the example value from .env.example`. Arregla `.env` y `docker compose up -d`. |
| Antifaz no arranca con `ANTIFAZ_NER_ENABLED=true` | La imagen publicada no trae el NER (el detector de nombres) ni su modelo. | Pon `ANTIFAZ_NER_ENABLED=false`. Hoy el NER solo funciona desde un clon (`make ner-model`, unos 1,16 GB); mira [TECNICO.md](TECNICO.md). |
| Conexión rechazada, o Claude Code dice que no puede conectar | Antifaz no está en marcha, o la dirección o el puerto están mal. | `docker compose ps` y después `docker compose up -d`. Revisa la URL (`http://127.0.0.1:8000` por defecto). |
| `502` o `504` | El proveedor no respondió bien o a tiempo. | Mira el estado del proveedor y tu red. Con la v0.2.0, `doctor --providers` prueba las claves. |
| `docker compose exec antifaz antifaz doctor` dice "invalid arguments" | `doctor` no existe en la 0.1.1. | Usa `docker compose logs antifaz` y `docker compose exec antifaz antifaz verify`. `doctor` llega con la v0.2.0. |
| `init` dice "no terminal to ask the questions" (v0.2.0) | `docker run` sin `-it`. | Añade `-it`, o usa `--non-interactive`. |
| Linux: "Permission denied" al leer `.env` (v0.2.0) | `init` se ejecutó sin `--user`, así que el archivo es del usuario del contenedor. | `sudo chown "$(id -u):$(id -g)" .env`, y la próxima vez usa `--user "$(id -u):$(id -g)"`. |
| Windows: Antifaz no arranca aunque `.env` parece bien (con la v0.2.0, `doctor` dice "cannot read .env as UTF-8 text") | `.env` se guardó con otra codificación. En Windows PowerShell 5.1, `>` y `Out-File` escriben UTF-16 y `Set-Content` escribe ANSI. | Abre `.env` con el Bloc de notas, "Guardar como", codificación **UTF-8**. No crees `.env` con `>`, `Out-File` ni `Set-Content`. Usa `Copy-Item .env.example .env` (o `init` con la v0.2.0). |

## 9. Lo que todavía no está probado

- **Falta una sesión real de Claude Code a través de Antifaz.** `setup claude-code` y la ruta de Anthropic están probados con el SDK oficial de Anthropic y con respuestas grabadas, pero todavía no con Claude Code contra la API real de Anthropic. Se hará cuando haya crédito de Anthropic. Hasta entonces, lo que esta guía dice de Claude Code sale de los tests y de la [documentación de Claude Code](https://code.claude.com/docs/en/llm-gateway-connect).
- La ruta de OpenAI se probó una vez contra la API real de OpenAI (2026-09-30).
- Los pasos de la [sección 3](#3-llega-con-la-v020-hoy-solo-en-main) usan el código de `main`. Están probados de extremo a extremo con la imagen construida desde el repositorio, pero la v0.2.0 todavía no está publicada.
