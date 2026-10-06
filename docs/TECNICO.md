# Detalles técnicos

> Antifaz está en desarrollo (v0.1). Esta página crece con cada issue.

## Ponerlo en marcha (desarrollo)

```bash
make install   # dependencias + hooks de pre-commit
make check     # lint + tipos + tests (también los de contrato) + gitleaks + zizmor
make contract  # solo los tests de contrato: SDK oficiales contra Antifaz, sin red
make audit     # vulnerabilidades conocidas en las dependencias (uv audit, experimental)
make licenses  # licencias de lo que se distribuye
make openapi   # regenera docs/openapi.json desde la app (un test comprueba que está al día)
make dev       # API en http://localhost:8000 (uvicorn --factory; necesita .env, ver abajo)
make e2e       # extremo a extremo con Docker: imagen real + init + Compose + proveedor falso
make up        # construye la imagen desde el clon y la arranca con Compose (necesita .env)
```

## Docker (issue 7, parte 7a)

### Ponerlo en marcha desde cero

Desde la v0.2.0, sin clonar (issue 42, ver «Una sola orden en la imagen»):

```bash
curl -LO https://github.com/miquel-moreno/antifaz/releases/download/v0.2.0/docker-compose.yml
docker run --rm -it -v "$PWD:/work" -w /work --user "$(id -u):$(id -g)" --network none \
  ghcr.io/miquel-moreno/antifaz:0.2.0 init
docker compose up -d
```

A mano, desde un clon (vale también con la 0.1.0):

```bash
git clone https://github.com/miquel-moreno/antifaz && cd antifaz
cp .env.example .env
# En .env: ANTIFAZ_API_KEY con un valor aleatorio (openssl rand -hex 32) y la clave de cada
# proveedor que uses; borra la línea del proveedor que no uses (sin clave, su ruta da 503).
docker compose up -d                 # la imagen publicada; `make up` la construye desde el clon
curl http://127.0.0.1:8000/healthz   # {"status":"ok","version":"0.1.0"}
```

El flujo a mano con `docker compose up -d --build` se comprobó el 2026-10-01 desde un clon limpio (Docker 29.8, Compose 5.5); desde el issue 42 la construcción local va en `compose.build.yml` (`make up`). Con los valores de ejemplo (`change-me...`) Antifaz se niega a arrancar: es a propósito (ADR-0015). Como Compose usa `restart: unless-stopped`, el contenedor se reinicia una y otra vez, cada vez con más espera entre intentos, y `docker compose ps` lo muestra como `restarting`. El motivo está en `docker compose logs antifaz` (un mensaje sin ninguna clave); se arregla `.env` y se vuelve a lanzar `docker compose up -d`. En `.env` no uses `$` en los valores: Compose intentaría sustituirlo.

### Cómo está hecha la imagen

| Decisión | Por qué |
|---|---|
| Dos etapas: uv construye un virtualenv con `uv sync --frozen --no-dev --no-editable` y la imagen final solo copia ese virtualenv | En la imagen no hay uv, ni código fuente, ni herramientas de desarrollo, ni compiladores. Las versiones son exactamente las de `uv.lock` |
| Imágenes base fijadas por digest: `python:3.12-slim` (3.12.14-slim-trixie, `sha256:f77ac9e4…84e51f`) y `ghcr.io/astral-sh/uv:0.12.20` (`sha256:100047e7…cd2d8`), resueltos el 2026-10-01 con `docker buildx imagetools inspect` | Una etiqueta se puede mover a otra imagen; un digest no. Dependabot (ecosistema `docker`, con `cooldown` de 7 días) propone las actualizaciones. Las líneas `FROM` van escritas enteras (sin `ARG`) para que Dependabot las entienda; las dos de Python tienen que ser iguales (lo comprueba un test) |
| Sin el extra `ner` | torch y el modelo añaden más de 1 GB. Habrá una etiqueta aparte `-ner` (con `--extra ner` y el modelo montado de solo lectura) más adelante |
| Usuario fijo sin privilegios `10001:10001`, sin home ni shell de login | Si alguien consigue ejecutar código dentro, no es root. El UID fijo permite usarlo en `securityContext` de Kubernetes |
| El virtualenv es de root y no se puede escribir; bytecode compilado al construir y `PYTHONDONTWRITEBYTECODE=1` | Nada se escribe en tiempo de ejecución: la imagen funciona con el sistema de archivos de solo lectura |
| `.dockerignore` como lista de permitidos (`pyproject.toml`, `uv.lock`, `README.md`, `LICENSE`, `NOTICE`, `src/`), más exclusiones explícitas (`.env*`, `models/`, `evals/`, `.git`, `.venv`, cachés) por si la lista crece por error | Un `.env`, un modelo, un dataset o un archivo nuevo no entran en la imagen por despiste. Ninguna clave va en la imagen: todas llegan como variables de entorno al arrancar |
| Sin pip, setuptools, wheel ni `ensurepip`: se borran de la imagen base en la etapa final | La pasarela no instala nada al ejecutarse; un instalador es una herramienta más para un atacante y más paquetes que escanear. Se borran en una capa posterior: no aparecen en el sistema de archivos del contenedor (`make e2e` comprueba que `python -m pip` falla), pero sus bytes siguen en la capa de la imagen base, por eso el tamaño casi no cambia |
| `HEALTHCHECK` con `python -m antifaz.healthcheck` (sin curl) | La imagen no lleva curl ni wget. El healthcheck pide `/healthz` en `127.0.0.1:8000` con una cabecera `Host` sacada de `ANTIFAZ_ALLOWED_HOSTS`, así lo acepta la comprobación de host sea cual sea la configuración. No usa nunca un proxy aunque haya `HTTP_PROXY`/`HTTPS_PROXY` en el entorno. Cada comprobación (cada 30 s) deja una línea `INFO` de la petición a `/healthz` en el log de Antifaz: es ruido esperado y no lleva datos |
| `ENTRYPOINT ["antifaz"]` y `CMD ["serve"]`; `antifaz serve` arranca uvicorn con `--factory antifaz.api.app:create_app`, `0.0.0.0:8000`, sin log de acceso, sin `X-Forwarded-*`, sin cabecera `Server` y 20 s de apagado limpio | La app se construye al arrancar, así una configuración insegura impide arrancar. Sin log de acceso de uvicorn (Antifaz tiene el suyo, sin datos). `X-Forwarded-*` se ignoran: ver "Proxy inverso". Hasta la 0.1.0 el `ENTRYPOINT` era el propio `uvicorn` con esos parámetros (ver «Una sola orden en la imagen») |

Tamaño medido el 2026-10-01: **259,1 MB** en disco según `docker image inspect` (la base `python:3.12-slim` ya ocupa 177 MB; el virtualenv, 51 MB; la capa de `apt-get upgrade`, 14,4 MB, porque los archivos viejos siguen en la capa de la base) y **60,9 MB comprimida**. `make e2e` lo vuelve a medir en cada ejecución y falla por encima de 300 MB.

### Compose

`docker-compose.yml` arranca un único servicio `antifaz` con la **imagen publicada** `ghcr.io/miquel-moreno/antifaz:${ANTIFAZ_VERSION:-X.Y.Z}` (por defecto, la versión de `pyproject.toml`, que en `main` es la última publicada; un test lo comprueba). Para construirla desde el clon, `compose.build.yml` añade `build:` e `image: antifaz:local` encima (`make up`, `make e2e`). No hay `command:`: corre el `CMD` de la imagen.

- **Claves**: se leen de `.env` (`env_file`) al arrancar el contenedor; nunca están en la imagen ni en el archivo de Compose. `ANTIFAZ_ENV_FILE` permite usar otro archivo (lo usan los tests de extremo a extremo para no leer nunca tu `.env`).
- **Puerto**: solo en `127.0.0.1:${ANTIFAZ_PORT:-8000}`. Desde otra máquina no se llega; para eso, un proxy inverso con HTTPS (abajo).
- **Solo lectura**: `read_only: true` y un `tmpfs` en `/tmp` (16 MB, `noexec`, `nosuid`, `nodev`).
- **Sin privilegios**: `cap_drop: [ALL]` y `no-new-privileges`.
- **Límites**: 512 MB de memoria, 1 CPU y 128 procesos (con el NER activado hace falta más memoria: el modelo ocupa más de 1 GB por proceso).
- **Una sola fuente de configuración**: todo sale de `.env`; el archivo de Compose no tiene `environment:` que pueda pisar lo que pone allí el administrador.
- **Hosts**: `.env.example` trae `ANTIFAZ_ALLOWED_HOSTS=localhost,127.0.0.1,[::1],antifaz`. `antifaz` es el nombre del servicio, para que otros contenedores de la misma red de Compose lleguen en `http://antifaz:8000`.
- Healthcheck, reinicio `unless-stopped` (con una configuración rechazada, se reinicia en bucle con espera creciente: ver el motivo con `docker compose logs antifaz`), 25 s para apagarse limpio y logs rotados (3 × 10 MB).

### Una sola orden en la imagen (issue 42, ADR-0017)

La imagen tiene `ENTRYPOINT ["antifaz"]` y `CMD ["serve"]`. Sin argumentos sirve; con argumentos, la misma imagen ejecuta otra orden de la CLI:

```bash
# Escribir .env en la carpeta actual (desde la v0.2.0). En Windows, sin --user;
# en PowerShell, -v "${PWD}:/work".
docker run --rm -it -v "$PWD:/work" -w /work --user "$(id -u):$(id -g)" --network none \
  ghcr.io/miquel-moreno/antifaz:0.2.0 init

# Servir sin Compose, con el mismo endurecimiento que docker-compose.yml (vale con la 0.1.0)
docker run -d --name antifaz --env-file .env -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp:size=16m,mode=1777,noexec,nosuid,nodev \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 512m --cpus 1 --pids-limit 128 --restart unless-stopped \
  ghcr.io/miquel-moreno/antifaz:0.1.0
```

| Decisión | Por qué |
|---|---|
| `antifaz serve` (`src/antifaz/cli/serve.py`) llama a `uvicorn.run` con los parámetros fijos del `ENTRYPOINT` anterior, más `workers=1`, y no acepta opciones | Un test (`tests/unit/test_cli_serve.py`) fija los parámetros y otro compara, valor a valor, lo que habría ejecutado la línea de órdenes de uvicorn con los argumentos antiguos: solo cambian `app_dir` (uvicorn añadía la carpeta actual a `sys.path`; ahora solo se importa el paquete instalado) `headers` (`[]` frente a `None`: ninguna cabecera extra en los dos casos) y `workers` (antes `None`, un proceso salvo que hubiera `WEB_CONCURRENCY`; ahora siempre 1) |
| `uvicorn.run` en vez de la línea de órdenes de uvicorn | La línea de órdenes leía variables `UVICORN_*` del entorno (por ejemplo `UVICORN_FORWARDED_ALLOW_IPS`), y el entorno sale de `.env`; `uvicorn.run` no las lee. La configuración de uvicorn todavía lee dos variables sin prefijo: `WEB_CONCURRENCY` solo si no se da `workers` (se da: 1) y `FORWARDED_ALLOW_IPS`, que se lee pero no tiene efecto porque las cabeceras de proxy están desactivadas. Así nada de `.env` cambia cómo se sirve. Lo comprueban dos tests (con `WEB_CONCURRENCY=4` y `FORWARDED_ALLOW_IPS=*`) |
| `docker-compose.yml` sin `command:` | Corre el `CMD` de la imagen, así el mismo archivo vale para la 0.1.0 (cuyo `ENTRYPOINT` era uvicorn y no entiende `serve`) y para las siguientes |
| Versión por defecto en Compose = la de `pyproject.toml` | En `main` es la última publicada (la 0.2.0 todavía no existe en ghcr.io), y el commit de cada release sube las dos a la vez. Un test las ata. `ANTIFAZ_VERSION=X.Y.Z` elige otra |
| `init` en el contenedor con `--rm`, `--network none` y `--user` | `--rm`: la clave que imprime `init` queda en el log del contenedor, que desaparece con él. `--network none`: `init` no usa la red. `--user`: en Linux, el `.env` lo crea tu usuario y no el `10001` de la imagen (con `0600`, si no, no podrías leerlo). En Docker Desktop para Windows no hace falta |
| Las claves por **nombre** de variable (`-e NOMBRE` en docker, sin valor) | Con `docker run -e NOMBRE`, docker toma el valor de su propio entorno: la clave no aparece en los argumentos ni en `ps`. Así lo hace `make e2e` |

### Proxy inverso (HTTPS)

Antifaz habla HTTP sin cifrar: para usarlo desde otras máquinas, ponlo detrás de un proxy inverso con HTTPS (Caddy, nginx, Traefik) en la misma máquina o red privada, y no publiques el puerto en `0.0.0.0`.

- Añade a `ANTIFAZ_ALLOWED_HOSTS` el nombre que usan los clientes (por ejemplo `antifaz.empresa.internal`).
- uvicorn arranca con `--no-proxy-headers`: Antifaz no se fía de `X-Forwarded-For` ni `X-Forwarded-Proto`. Hoy no los necesita (no usa la IP del cliente ni construye URLs). Si algún día hicieran falta, se activarían solo para la IP del proxy (`--forwarded-allow-ips`), nunca para todas.
- El proxy no debe guardar los cuerpos de las peticiones en sus logs: llevan los datos personales antes de enmascararse.

### Extremo a extremo: `make e2e`

No forma parte de `make check` (necesita Docker y tarda alrededor de un minuto). Hace esto:

1. Construye la imagen con `docker compose build` (`docker-compose.yml` + `compose.build.yml`, la imagen `antifaz:local`).
2. Arranca Compose con `tests/e2e/docker-compose.e2e.yml` encima de esos dos: añade un **proveedor falso** (`tests/e2e/fake_upstream.py`, solo biblioteca estándar) que corre en la propia imagen de Antifaz (no se descarga ninguna otra), guarda los bytes que recibe y devuelve el texto que le llega.
3. Las claves son aleatorias y nuevas en cada ejecución, en un archivo temporal que se borra al final; tu `.env` no se lee nunca. Las URL de los proveedores apuntan al falso con `http://` dentro de la red de Compose: las URL base son configuración del administrador y Antifaz no les exige HTTPS.
4. Comprueba, con un DNI y un email sintéticos, que el proveedor solo ve marcadores y el cliente recibe sus valores (OpenAI Chat y Anthropic Messages, con y sin streaming, con los marcadores partidos entre eventos); que el proveedor recibe su clave y nunca la de Antifaz; que sin clave se responde 401; que el contenedor está `healthy`, corre como `10001`, no puede escribir fuera de `/tmp`, no tiene capacidades y solo publica en `127.0.0.1`; que la imagen no tiene `.env` (ni en `/app` ni en ningún sitio), ni pytest, ni curl, ni wget, ni pip; y que los logs no tienen el DNI, el email ni ninguna clave, tampoco cuando el DNI va en la query (`/v1/models?after_id=…`, 400) o en una ruta inventada (`/v1/<DNI>`, 404).
5. La instalación en un minuto (issue 42, `tests/e2e/test_image_cli.py`): que la imagen tiene `ENTRYPOINT ["antifaz"]` y `CMD ["serve"]`, que `--help` funciona y que `serve` no acepta opciones; ejecuta `init --non-interactive` dentro de la imagen como dice el README (`-v carpeta:/work -w /work`, `--user` en Linux y macOS, `--network none`, `--read-only`, `--cap-drop ALL`), con claves falsas pasadas por nombre (`-e NOMBRE`), en una carpeta temporal que se borra al final; comprueba que ninguna clave sale por pantalla, que el `.env` tiene las claves (y en POSIX es del usuario y `0600`) y que una segunda ejecución sin `--force` no lo toca; y con ese `.env` arranca Compose (otro proyecto, sin proveedor falso) y el `docker run` endurecido de una línea, y los dos responden `/healthz`.

Resultado el 2026-10-06: **22 de 22 tests en verde** (Windows 11, Docker Desktop 29.8), también `init` dentro de la imagen escribiendo en una carpeta de Windows montada con `-v`. Imagen: 259,2 MB sin comprimir.

Los mismos puntos de la imagen y de Compose (digest, usuario, healthcheck, `.dockerignore`, solo lectura, puerto…) también se comprueban sin Docker en `tests/unit/test_container_files.py`, dentro de `make check`.

### CI de la imagen

Job `image` de `ci.yml` (solo `contents: read`): construye la imagen con `docker buildx build --load --tag antifaz:local .`, con el mismo contexto y etiqueta que `compose.build.yml` (un test lo comprueba), así `make e2e` la reutiliza (este job no publica nada: lo hace `release.yml`, ver «Publicación»), ejecuta `make e2e`, genera el SBOM en CycloneDX con Syft (`anchore/sbom-action`, se sube como artefacto y se guarda 30 días) y la escanea con Grype (`anchore/scan-action`, ver «Escaneo de la imagen»). Las acciones van fijadas por SHA y Syft (v1.51.1) y Grype (v0.118.0) por versión exacta. La procedencia y el SBOM adjuntos a la imagen los pone el workflow de publicación (ver «Publicación»).

### Escaneo de la imagen

Grype solo se ejecuta en la CI, no en `make e2e` ni en local. La política:

| Decisión | Por qué |
|---|---|
| La CI falla solo con vulnerabilidades **altas o críticas que tienen arreglo** (`only-fixed: true`) | Una vulnerabilidad que Debian no arregla («won't fix» o sin versión corregida) no se puede quitar desde el repo: con ella la CI estaría siempre en rojo y se dejaría de mirar. Las que tienen arreglo sí se pueden quitar, y esas paran el merge |
| La etapa final hace `apt-get update && apt-get upgrade -y --no-install-recommends` y borra las listas de apt | Recoge los parches de seguridad de Debian publicados después de la imagen base (el 2026-10-01: openssl, libssl3t64 y openssl-provider-legacy 3.5.7-1~deb13u3, libpcre2-8-0 10.46-1~deb13u3). Solo actualiza lo que ya está; no instala nada nuevo. La base sigue fijada por digest |
| Dependabot (ecosistema `docker`) propone cada digest nuevo de la imagen base | Las reconstrucciones de `python:3.12-slim` traen los parches de Debian ya incluidos. No hay entrada `docker-compose`: el único servicio usa la imagen de Antifaz, cuya versión sube con cada release (un test la ata a `pyproject.toml`) |
| El informe completo (todas las vulnerabilidades, con o sin arreglo, en formato tabla) se sube como artefacto `grype-report` en cada ejecución, también cuando la CI falla, y se guarda 30 días | El filtro de la CI no esconde nada: lo que no rompe la CI sigue a la vista |
| Una sola excepción, en `.github/grype-gate.yaml` (solo la usa el filtro, no el informe): CVE-2026-82049 en Python 3.12.14 | Grype la da por «arreglada» porque existe una versión corregida, pero solo en Python 3.14; no hay arreglo para 3.12. Pasar a 3.14 es otra decisión. Cada excepción lleva motivo y fecha y se revisa al cambiar la imagen base |

Estado real el 2026-10-01 (CI de la rama `feat/7a-docker`, Grype v0.118.0): después del `apt-get upgrade` el informe sigue listando **13 vulnerabilidades altas sin arreglo en paquetes de sistema de Debian** (unas 50 líneas, porque cada una afecta a varios paquetes: util-linux y su familia, libc, perl, ncurses, zlib, acl y las librerías de gcc) y **una en Python 3.12** que solo está arreglada en 3.14. Ninguna está en el código de Antifaz. La pasarela no ejecuta la mayoría de esas herramientas (mount, login, perl, ncurses…): están porque vienen con Debian. Antes del lanzamiento de la v0.2 está previsto pasar a una imagen distroless, que no lleva esos paquetes.

### Publicación (issue 7, parte 7c)

La imagen se publica en `ghcr.io/miquel-moreno/antifaz` con un workflow aparte, `.github/workflows/release.yml`, que **solo** arranca con un tag `vX.Y.Z`.

**El primer tag, solo cuando el repo sea público.** En GitHub Free las atestaciones de procedencia necesitan un repo público: con el repo privado la imagen se subiría y el paso de la atestación fallaría, y quedaría una release a medias (imagen publicada sin procedencia). Antes del primer tag, en la configuración del repo (lo hace Miquel):

- environment `release`: Miquel como revisor obligatorio (*required reviewers*), sin saltarse la protección como administrador (*allow administrators to bypass* desactivado) y con *prevent self-review* desactivado, porque hay un solo mantenedor y si no nadie podría aprobar;
- un *ruleset* de tags que limite quién puede crear tags `v*`.

Cómo va una release:

1. En `main`, con la CI en verde: la versión de `pyproject.toml` y la sección `## [X.Y.Z]` del `CHANGELOG.md` ya están puestas. **En el mismo commit, justo antes del tag**, se sube también la versión por defecto de `docker-compose.yml` (`${ANTIFAZ_VERSION:-X.Y.Z}`, un test la ata a `pyproject.toml`) y la versión de la vía manual del README (EN y ES).
2. **Miquel** crea y sube el tag (`git tag -a v0.1.0 -m "v0.1.0" && git push origin v0.1.0`). Nadie más (ni un agente) crea tags.
3. El job `publish-image` espera en el environment protegido `release`: solo lo pueden usar tags `v*` y, cuando el repo sea público, pide la aprobación manual de Miquel antes de recibir ningún permiso.
4. Comprueba que el commit del tag está en `main` (si no, falla), que el tag es exactamente `vX.Y.Z`, que coincide con `pyproject.toml` y que el `CHANGELOG.md` tiene su sección; si no, falla sin construir nada.
5. Construye la imagen sin caché y la sube como `:X.Y.Z` y `:X.Y`, con el SBOM y la procedencia completa (`provenance: mode=max`) adjuntos por buildx.
6. Firma una atestación de procedencia SLSA con la identidad OIDC del job (Sigstore) y la sube junto a la imagen.
7. Job `release-asset` (issue 42), después de `publish-image` (`needs`) y sin environment propio: solo arranca si `publish-image`, que ya esperó la aprobación en `release`, terminó bien, así que cada release se aprueba **una vez**. No tiene secretos, solo `contents: write`: genera el `docker-compose.yml` de la release con `python3 -m scripts.release_compose` (solo biblioteca estándar y sin token: copia el del repo y cambia solo la línea `image:` a `ghcr.io/miquel-moreno/antifaz:X.Y.Z@sha256:…`, con el digest que sale del paso de construcción; falla si la versión o el digest no tienen la forma esperada) y lo sube a la GitHub Release del tag con `gh release upload --clobber`. Si Miquel todavía no ha creado la release (`gh release view` responde exactamente `release not found`), crea un **borrador** (`--draft --prerelease --verify-tag`, nunca un tag nuevo): Miquel escribe las notas (la sección del CHANGELOG) y lo publica. Cualquier otro error al leer la release (red, token, límite de peticiones) hace fallar el job en vez de crear otra. El borrador sale marcado como *pre-release* (Antifaz es beta); cuando se publique una versión que no lo sea, hay que desmarcarlo al publicarlo.

Comprobar que una imagen salió de este repo y de este workflow:

```bash
gh attestation verify oci://ghcr.io/miquel-moreno/antifaz:0.1.0 \
  --repo miquel-moreno/antifaz \
  --signer-workflow miquel-moreno/antifaz/.github/workflows/release.yml
docker buildx imagetools inspect ghcr.io/miquel-moreno/antifaz:0.1.0 --format "{{ json .SBOM }}"
```

`--repo` y `--signer-workflow` exigen que la firma sea de este repo **y** de `release.yml`, no de cualquier workflow de la cuenta.

| Decisión | Por qué |
|---|---|
| Workflow de publicación separado de la CI y sin escáneres ni herramientas de terceros (solo acciones de GitHub y Docker, fijadas por SHA) | Es el único sitio con permiso para publicar. Un escáner comprometido (como trivy-action en marzo de 2026) no puede tocar la imagen publicada. Los escaneos (Grype, gitleaks, zizmor, CodeQL) ya pasaron en `ci.yml` sobre el mismo commit |
| Solo `GITHUB_TOKEN` y OIDC, sin tokens guardados | El token dura lo que la ejecución y solo vale para este repo. No hay contraseña del registro que pueda filtrarse |
| Permisos de `publish-image`: `contents: read`, `packages: write`, `id-token: write`, `attestations: write` | Lo justo para leer el código, subir la imagen y firmar la procedencia. Nada de `contents: write` |
| `contents: write` solo en el job pequeño `release-asset`, sin más acciones que `actions/checkout` (sin credenciales guardadas) y el `gh` que trae el runner; el digest le llega como salida del otro job y entra en los scripts por `env`, nunca con `${{ }}` dentro del script | Adjuntar el `docker-compose.yml` necesita escribir en la release. Separado, el job con permisos para el registro y la firma no puede tocar el repo, y el que puede tocarlo no ejecuta nada de terceros. El script del repo se ejecuta sin el token; solo el paso de `gh` lo recibe. zizmor sin hallazgos |
| La página de la GitHub Release la escribe Miquel a mano (copiando la sección del CHANGELOG); el workflow solo crea un borrador si falta | El workflow no publica releases: un borrador no lo ve nadie hasta que Miquel lo publica |
| El `docker-compose.yml` de la release fija la imagen por digest | Quien lo descarga ejecuta exactamente la imagen construida, probada y atestada, aunque alguien moviera la etiqueta `X.Y.Z` después |
| Etiquetas `X.Y.Z` y `X.Y`, sin `latest` | Antifaz es beta (0.x): quien la usa elige versión a propósito y una versión nueva no le llega sin querer. Ojo: `0.1` se mueve con cada `0.1.x` (recibe los parches); `0.1.0` no cambia nunca |
| Solo se publica un commit que ya está en `main` (`git merge-base --is-ancestor`) | Un tag puesto en una rama sin revisar no llega a la imagen |
| Anotaciones OCI en el manifiesto y en el índice (`DOCKER_METADATA_ANNOTATIONS_LEVELS: manifest,index`) | Con el SBOM y la procedencia, buildx crea un índice; así ghcr.io enlaza ambos con el repo |
| Solo `linux/amd64` | Es lo único probado (CI y `make e2e`). `arm64` se añadirá cuando tenga pruebas |
| Sin caché de construcción | Nada de una ejecución anterior (ni de un PR) puede acabar en la imagen publicada |
| Sin registro de almacenamiento (`create-storage-record: false`) en la atestación | Pediría otro permiso y no cambia la verificación con `gh attestation verify` |
| Una release cada vez y nunca se cancela a medias (`concurrency` sin cancelación) | Evita dos publicaciones pisándose las etiquetas |
| PyPI, en la v0.2 (trusted publishing con OIDC, en este mismo workflow) | En la v0.1 se distribuye solo la imagen. La firma con cosign y el Scorecard llegan en la v1.0 |

## Arquitectura

Una pieza por responsabilidad, cada una en su paquete de `src/antifaz/`:

| Pieza | Paquete | Hace |
|---|---|---|
| Puerta | `api/` | Endpoints, auth, límites, `request_id`; errores que nunca repiten el cuerpo recibido |
| Detector | `detect/` | Validadores con dígito de control, patrones, NER, diccionarios |
| Política | `policy/` | Qué hacer con cada tipo de dato: `mask`, `surrogate`, `block`, `allow`, `route_local` |
| Tabla de marcadores | `vault/` | Valor ↔ marcador, solo durante la petición |
| Enmascarador | `mask/` | Cambia los valores por marcadores |
| Guardia de salida | `guard/` | Segunda comprobación sobre los bytes finales; si encuentra algo, bloquea |
| Enrutador | `providers/` | OpenAI, Anthropic, Ollama; destinos fijos |
| Restaurador | `restore/` | Vuelve a poner los valores, también en streaming |
| Evidencias | `audit/` | Registro encadenado sin datos personales (v0.2) |

El docstring de cada paquete dice qué hace y qué tiene prohibido. Las decisiones (aceptadas) están en [`docs/adr/`](adr/README.md) y las amenazas en [`docs/security/threat-model.md`](security/threat-model.md).

## Detector: identificadores con dígito de control (issue 2, PR 2a)

`antifaz.detect.scan(text)` devuelve la lista de spans `(start, end, type, layer, confidence)`: dónde hay un dato y de qué tipo, **nunca el valor** (ADR-0009).

| Tipo | Validación | Fuente |
|---|---|---|
| `ES_DNI` | 8 cifras + letra `TRWAGMYFPDXBNJZSQVHLCKE[n mod 23]` | Ministerio del Interior |
| `ES_NIE` | X/Y/Z → 0/1/2 + regla del DNI | Ministerio del Interior |
| `ES_NIF` (K/L/M) | 7 cifras + letra del DNI | RD 1065/2007 (sin algoritmo publicado; como python-stdnum) |
| `ES_CIF` | Luhn sobre las 7 cifras, como dígito o como letra `JABCDEFGHI` (ambas formas) | Orden EHA/451/2008 (sin algoritmo); ADR-0009 |
| `ES_NSS` | mod 97; si el número < 10^7, `(número + provincia·10^7) mod 97` | Sin fuente oficial de la TGSS; vectores en `tests/data/nss_vectors.md` |
| `ES_CCC` | Dos dígitos mod 11, pesos 1, 2, 4, 8, 5, 10, 9, 7, 3, 6 | AEB (2001) |
| `IBAN` | ISO 13616 mod 97 + estructura del país; los ES exigen además un CCC válido | Registro IBAN (vía python-stdnum) |
| `IT_CODICE_FISCALE`, `EU_VAT` | python-stdnum | — |

**Invariante 10:** en `tests/property/`, cada validador español se compara con python-stdnum en 5.000 casos generados (una parte con el control correcto calculado por stdnum, para probar también el lado "válido"). El NSS, que stdnum no tiene, se prueba con vectores documentados y con la propiedad "cambiar cualquier cifra lo invalida".

**En el texto:** cada patrón solo propone candidatos; un span se devuelve si su validador lo acepta. Los patrones siguen el ADR-0008 (repeticiones acotadas, cuantificadores posesivos, límites que impiden encontrar un DNI dentro de una tira más larga de cifras; ver ADR-0014 para los tipos que pueden tocar letras) y hay pruebas con textos maliciosos de 50.000 caracteres. El IBAN se corta a la longitud de su país, sacada del registro IBAN, y se busca avanzando carácter a carácter tras un candidato falso, para que ningún candidato tape a otro IBAN. Se aceptan mayúsculas y minúsculas y los separadores habituales (espacio, punto, guion; barra en el NSS). La resolución de solapamientos es O(n log n): un texto con 20.000 DNI se resuelve en menos de 2 s.

### Patrones con contexto (issue 2, PR 2b)

Para los datos sin dígito de control, `patterns/personal.py` busca la forma del dato y, en los más ambiguos, una palabra clave en los 40 caracteres anteriores (`patterns/context.py`, con límites de palabra reales: "hotel" no cuenta como "tel").

| Tipo | Cómo se detecta | Contexto | Confianza |
|---|---|---|---|
| `EMAIL` | Caracteres de RFC 5322 y letras Unicode (RFC 6531) en la parte local; dominio con TLD de 2+ letras o `xn--` | No | Alta |
| `IP` | IPv4 con octetos 0–255, no dentro de una serie más larga | No | Media |
| `PHONE` | España: `+34`/`0034`/`(+34)` o `34` pegado opcional, empieza por 6, 7, 8 o 9, 9 cifras en bloques con el mismo separador. Cada serie de cifras se parte en bloques y los teléfonos se montan bloque a bloque, así que un teléfono junto a otro número también se encuentra. Excluye números de empresa (800, 900, 901, 902, 905) y de tarificación adicional (803, 806, 807; CNMC) | Sube a alta con "tel", "teléfono", "móvil", "llamar", "WhatsApp" | Media / alta |
| `CREDIT_CARD` | 13–19 cifras (separador espacio, guion o punto), Luhn y prefijo conocido (Visa, Mastercard, Amex, Discover, JCB, Diners, UnionPay). No empieza en mitad de un número agrupado de 3+ cifras (evita leer parte de un teléfono como tarjeta) | No | Alta (validador) |
| `ES_PASSPORT` | 3 letras + 6 cifras (formato observado, sin norma citada) | Obligatorio ("pasaporte"…) | Media |
| `ES_PLATE` | 4 cifras + 3 consonantes sin vocales, Ñ ni Q (anexo XVIII del RD 2822/1998) | Obligatorio ("matrícula", "coche"…) | Media |
| `DATE_OF_BIRTH` | dd/mm/aaaa (también con - o . y año de 2 cifras), ISO aaaa-mm-dd, "12 de marzo de 1985", "12 marzo 1985", "1 de marzo del 1985"; fecha real | Obligatorio ("nacido", "F. nac."…) | Media |
| `ADDRESS` | Tipo de vía (castellano, catalán y gallego, con abreviaturas) + nombre + número (`nº`, `n.º`, `s/n`) + piso/puerta + CP 01000–52999 opcionales. Con tipos inequívocos (C/, Calle, Carrer, Avda…) el nombre puede ir en minúsculas; con tipos que también son palabras comunes (Camino, Ronda, Paseo, "C.") debe empezar por mayúscula | No | Media; alta con CP |
| `PT_NIF`, `FR_NIR`, `DE_IDNR` | Solo cifras, validados con python-stdnum | Obligatorio, del propio país | Alta (validador) |

Los identificadores de la UE que son solo cifras necesitan contexto porque, sin él, un NIF portugués de 9 cifras es indistinguible de un teléfono español.

**Solapamientos (ADR-0010):** si una detección contiene por completo a otra, gana la que contiene; así un email con un DNI dentro se enmascara entero, no solo el DNI.

### Normalización previa y formatos raros (ADR-0014, red team ronda 1)

Antes de buscar, `detect/normalize.py` crea una vista normalizada del texto con un mapa de posiciones; el texto original no se toca:

- Se quitan los caracteres de formato invisibles (categoría Unicode Cf: espacio de ancho cero, ZWNJ/ZWJ, guion blando, BOM, word joiner, marcas de dirección…), las marcas combinantes (Mn: acentos escritos aparte, selectores de variante) y los rellenos hangul.
- Los caracteres cuyo NFKC es **una** letra o cifra ASCII pasan a ese carácter (cifras y letras de ancho completo, letras matemáticas, superíndices). Los ordinales `º` y `ª` se quedan como están (los usan las direcciones).
- Tabulador, espacio duro (NBSP) y los demás espacios Unicode pasan a un espacio normal. Los saltos de línea se conservan.
- Homoglifos: las letras cirílicas `А В Е К М Н О Р С Т Х У З` y griegas `Α Β Ε Ζ Η Ι Κ Μ Ν Ο Ρ Τ Υ Χ` (y sus minúsculas) pasan a la latina que imitan (tabla `HOMOGLYPHS`; `З` y `Ζ` → `Z`).

Cada carácter se sustituye por uno o se elimina, así que las posiciones se devuelven al original con un mapa monótono (bisección sobre los caracteres eliminados): el marcador cubre los caracteres originales, incluidos los invisibles que haya **dentro** del valor, y `restore()` devuelve el texto exacto. Un texto ASCII sin tabuladores no se copia.

**Separadores:** DNI, NIE y NIF K/L/M aceptan un solo espacio, punto o guion entre grupos (`12 345 678 Z`, `12.345.678-Z`, `X 1 234 567 L`) y **un** salto de línea (`\n` o `\r\n`) en cualquier punto de las cifras o antes de la letra. El IBAN acepta un salto de línea entre grupos. Los recuentos siguen siendo exactos (8 o 7 cifras) y la letra o el mod 97 siguen siendo obligatorios; dos saltos de línea o dos espacios seguidos no valen.

**Límites relajados para los tipos con control fuerte:** DNI, NIE, NIF K/L/M e IBAN pueden ir pegados a letras (`DNI12345678Z`, `NIEX1234567L`, `ref abc12345678Zxyz`, `IBANES91…`), pero nunca a cifras: un DNI dentro de una tira más larga de cifras sigue sin ser un DNI. Tampoco se relaja dentro de una tira toda hexadecimal de 16 caracteres o más (hashes): con 20.000 valores aleatorios, 0 falsos DNI en SHA-1 y SHA-256 y 2 en UUID v4 (sus grupos tienen como mucho 12 caracteres; coste aceptado, se enmascara de más). Si hay separador antes de la letra de control, la letra no puede ir seguida de otra letra (en "12345678 casas" la "c" no es la letra de control). CIF, NSS, CCC, codice fiscale y NIF-IVA mantienen los límites estrictos (controles más débiles; "DNI és 16257107-V" sigue sin ser un CIF).

**Cuerpos muy anidados:** un JSON con más de 100 niveles de objetos o listas se bloquea (`NestingTooDeep`, 400 `antifaz_blocked`) antes de cualquier recorrido recursivo; la comprobación de adjuntos es iterativa. Una respuesta del proveedor igual de profunda da un 502 `bad_upstream_response` fijo. Un `arguments` con JSON demasiado profundo se trata como texto y se enmascara como tal.

**Limitaciones conocidas:**
- Cifras de otros sistemas de numeración (árabes, devanagari…) no se convierten: su NFKC no es ASCII. Solo se cubren los homoglifos de la tabla; otros alfabetos parecidos (armenio, cherokee…) no.
- Un valor partido entre dos cadenas JSON distintas, o codificado (base64, hexadecimal, URL, HTML, al revés, rot13), no se detecta (tests `xfail` del red team).
- Las direcciones se buscan con un patrón al estilo español (tipo de vía y nombre con mayúscula); las que no siguen ese formato quedan para el NER (issue 6). La ciudad no se incluye.
- IPv6 y pasaportes de otros países, fuera de alcance por ahora.
- El contexto solo se busca **antes** del valor y en 40 caracteres: no sirve una cabecera de CSV ni una palabra clave detrás ("AAB123456 (pasaporte)"). Propuesto como issue aparte.
- No se detectan: fechas con solo el año ("nacido en 1985"), matrículas provinciales antiguas (B-1234-XY), emails ofuscados ("juan (at) ejemplo.es"), teléfonos con doble espacio o con el prefijo entre paréntesis ("(93) 123 45 67"), direcciones en euskera con el tipo detrás ("Nagusia kalea 3") ni NIR corsos (2A/2B).
- Algunos números normales se detectan como teléfono o IP con confianza media ("700000000 unidades", "versión 1.2.3.4"): la política decide qué hacer con la confianza media.
- Controles débiles implican falsos positivos: NSS 1/97, CCC 1/121, Luhn 1/10. Es un fallo seguro (se enmascara de más).
- "CCC" aquí es la cuenta bancaria, no el código de cuenta de cotización de la Seguridad Social.
- Faker genera NSS con otra fórmula cuando el número empieza por 0: no sirve de referencia (se tendrá en cuenta en el benchmark).

## Librería: `mask()` y `restore()` (issue 4, PR 4a)

```python
from antifaz import mask, restore

result = mask(["Mi DNI es 12345678Z", "¿y el 12345678Z?"])  # también acepta un solo str
result.texts  # ("Mi DNI es [[ES_DNI_1]]", "¿y el [[ES_DNI_1]]?")
result.hidden  # (EntityType.ES_DNI,)
restore(respuesta_del_modelo, result.vault)
```

- Marcadores `[[TIPO_N]]`, numerados por tipo y por primera aparición en todos los textos; el mismo par (tipo, texto exacto) recibe siempre el mismo marcador ([ADR-0012](adr/0012-escape-and-same-value.md), propuesta).
- Los `[[` que ya escribe el usuario se escapan con un `!` detrás; `restore()` lo quita en una sola pasada y solo restaura marcadores de **esta** petición. Hypothesis comprueba `restore(mask(x)) == x`.
- Política por defecto (`DEFAULT_POLICY`, versión `builtin-1`): enmascara todos los tipos salvo el CIF de empresa.
- Si el detector falla o devuelve spans mal formados: `DetectorFailed`, con un mensaje fijo sin el texto. Nunca sale texto en claro.
- `Vault` no se puede imprimir (solo `Vault(entries=N)`), recorrer, serializar ni copiar. Protege contra fugas **accidentales** (logs, `repr`, pickle), no contra código del mismo proceso que lea sus atributos privados.
- Cada respuesta se restaura solo con la tabla de **su** petición: todas numeran desde 1, así que con la tabla de otra petición `[[ES_DNI_1]]` daría el DNI de otra persona. La pasarela lo garantiza en el issue 5.
- El restaurador solo reconoce marcadores en ASCII: parecidos como `[[ıp_1]]` se dejan tal cual.

## Guardia de salida y CLI (issue 4, PR 4b)

```python
from antifaz.guard import check

check(cuerpo_final_en_bytes, result.vault)  # lanza EgressBlocked si ve un valor oculto
```

- Si el cuerpo es JSON, revisa cada cadena, clave y número decodificados (así ve los escapes Unicode de JSON: una barra invertida seguida de `u0031` es el dígito 1); si no lo es, el texto crudo. Bytes que no son UTF-8 válido, o un JSON demasiado profundo para revisarlo: bloquea.
- Normaliza igual el valor y el texto: NFKC (cifras de ancho completo), sin caracteres de formato Cf (espacio de ancho cero, guion blando), sin acentos (NFD sin marcas Mn, así `İbrahim` = `ibrahim`), `casefold()` y espacios seguidos reducidos a uno.
- Valores con 6 o más letras y cifras: se comparan quitando **todo** lo que no es letra ni cifra y **sin límites de palabra**, así que `DNI12345678Z`, `X12345678ZY`, `12.345.678-Z` o `xana@example.com` bloquean.
- Valores más cortos: con límites alfanuméricos (ni letra ni cifra justo antes o después), para que "Ana" no salte en "semana".
- Se normaliza cada texto una sola vez: 1 MB de JSON con 200 valores ocultos se revisa en unos 0,04 s si es ASCII y unos 0,35 s con acentos (test con límite de 2 s).
- **Un falso positivo bloquea**: la guardia falla cerrada a propósito. No tiene opción para desactivarla.
- `EgressBlocked` lleva un mensaje fijo, sin valores y sin `__context__`.

Limitaciones de la guardia (es una **segunda capa**, no un DLP completo):

- No decodifica otras codificaciones: base64, URL (`%31`), entidades HTML, hexadecimal ni el valor escrito al revés.
- No ve un valor partido entre dos cadenas JSON distintas.
- Solo busca el valor tal como se detectó: un DNI copiado sin la letra, o un teléfono oculto con prefijo (`0034612345678`) que luego aparece sin él, pueden pasar. Al revés sí se detecta: el teléfono oculto sin prefijo bloquea aunque aparezca con `0034` pegado.
- Los falsos positivos bloquean la petición, por diseño.
- Invariante 2 (`tests/property/test_egress_invariant.py`): **sin llamar a la guardia**, un proveedor falso que guarda los bytes no recibe ningún valor oculto, ni tal cual, ni normalizado ni compactado, con `ensure_ascii` activado y desactivado.

CLI (`uv run antifaz ...`):

```bash
antifaz scan fichero.txt   # una línea por detección: TIPO inicio fin (nunca el valor)
antifaz mask fichero.txt   # el texto con marcadores (nunca la tabla)
antifaz mask -             # lee de stdin
antifaz verify             # planta datos falsos con tu configuración (ver "antifaz verify")
antifaz init               # escribe un .env con una clave nueva (ver "antifaz init")
antifaz doctor             # revisa la configuración, la pasarela y, si se pide, las claves (ver "antifaz doctor")
```

Si no puede leer el fichero como UTF-8 o el detector falla: mensaje genérico en stderr y código 2, sin repetir el contenido.

## Proxy compatible con OpenAI Chat (issue 5, PR 5a)

Configura `.env` a partir de `.env.example`: `ANTIFAZ_API_KEY` (la clave que usan tus clientes), `ANTIFAZ_OPENAI_API_KEY` y, si quieres otro servidor compatible, `ANTIFAZ_OPENAI_BASE_URL`. Todas las variables de Antifaz empiezan por `ANTIFAZ_`: así no se mezclan con las que usan Claude Code o los SDK en la misma terminal (si no, Antifaz podría llamarse a sí mismo y enviar su propia clave). Sin `ANTIFAZ_API_KEY` válida, Antifaz no arranca; sin la clave del proveedor, esta ruta responde 503 (ver [La puerta](#la-puerta-cerrada-por-defecto-issue-20)).

```bash
curl http://localhost:8000/v1/chat/completions   -H "Authorization: Bearer $ANTIFAZ_API_KEY" -H "Content-Type: application/json"   -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Mi DNI es 12345678Z"}]}'
```

Con el SDK de OpenAI basta con `base_url="http://localhost:8000/v1"` y `api_key=<ANTIFAZ_API_KEY>`.

Qué hace con cada petición ([ADR-0013](adr/0013-proxy.md), aceptada):

1. La puerta ya comprobó la clave de Antifaz, el `Origin` y el `Content-Type` (ver [La puerta](#la-puerta-cerrada-por-defecto-issue-20)). Esa clave **nunca** llega al proveedor; tampoco ninguna otra cabecera del cliente.
2. Lee el cuerpo con un tope (`ANTIFAZ_MAX_BODY_BYTES`, 4 MiB por defecto): más grande → 413; no es un objeto JSON en UTF-8 → 400.
3. Reúne **todas** las cadenas del cuerpo (mensajes, partes de texto, `name`, resultados de herramientas y cualquier campo nuevo) y llama a `mask()` una sola vez. Los `arguments` de las llamadas a herramientas se parsean como JSON y se enmascaran sus valores. Las claves de los objetos no se cambian: si alguna contiene un dato, la petición se bloquea.
4. Solo pasan partes de tipo `text`, `refusal` o `function`: imágenes, documentos, audio, ficheros, `file_id`, claves como `source` o `data`, o un texto con una URL `data:...;base64,` → 400 `antifaz_blocked`. Los números pasan por el detector: si uno es un dato (un teléfono escrito como número), se bloquea. `NaN`/`Infinity` o un `stream` que no sea booleano → 400.
5. Serializa una vez y la guardia de salida revisa **esos mismos bytes** justo antes de enviarlos.
6. Envía a `ANTIFAZ_OPENAI_BASE_URL` con `ANTIFAZ_OPENAI_API_KEY`. La URL nunca sale del cliente.
7. Restaura `content`, `refusal` y los `arguments` de las herramientas (parseando el JSON, así siguen siendo JSON válido aunque el dato tenga comillas). El resto de campos pasa sin tocar.

Errores:

| Caso | Respuesta |
|---|---|
| Sin clave, clave incorrecta, cabecera de clave repetida o `Authorization` y `x-api-key` en conflicto | 401 `unauthorized` |
| Cabecera `Origin` no permitida | 403 `origin_not_allowed` |
| `Content-Type` que no es `application/json` (UTF-8) | 415 `unsupported_media_type` |
| Claves repetidas en el JSON (también si solo cambian en mayúsculas y es una clave que Antifaz lee) | 400 `invalid_request` |
| `Host` que no está en `ANTIFAZ_ALLOWED_HOSTS` | 400 `Invalid host header` (texto) |
| La respuesta del proveedor repite una clave configurada (también escapada en JSON) | 502 `bad_upstream_response` |
| Dato en un sitio que no se puede enmascarar, adjunto, detector roto o guardia | 400 `antifaz_blocked`, mensaje fijo |
| `stream: true` | La respuesta llega en streaming, restaurada al vuelo (ver [Streaming](#streaming-issue-5-pr-5c)) |
| `stream_options` con algo que no sea `include_usage` o `include_obfuscation` a `true`/`false` | 400 `invalid_request` |
| El proveedor tarda más de `ANTIFAZ_UPSTREAM_TIMEOUT_SECONDS` | 504 `upstream_timeout` |
| No se puede conectar | 502 `upstream_unavailable` |
| El proveedor responde con una redirección (3xx) | 502 `upstream_redirect` (no se sigue) |
| El proveedor responde con error (4xx/5xx) en JSON | Su cuerpo **tal cual**, como `application/json`: solo vio texto enmascarado, así que puede mostrar marcadores `[[TIPO_N]]` |
| El proveedor responde con error que no es JSON (una página HTML) | 502 `bad_upstream_response` |
| Ruta que no existe / método que la ruta no tiene (con clave) | 404 `not_found` / 405 `method_not_allowed`, mensaje fijo |

Ningún log escribe cuerpos, cabeceras ni claves; `httpx` y `httpcore` quedan en `WARNING`. Un test (invariante 8) pasa un DNI centinela por respuestas, errores del proveedor, tiempos agotados y bloqueos de la guardia, con todos los loggers en `DEBUG`, y comprueba que no aparece ni en los logs ni en los cuerpos de error. Otro (invariante 2) quita la guardia **solo en el test** y comprueba que el proveedor falso no recibe ningún valor oculto, tampoco en argumentos ni resultados de herramientas.

## Proxy de Anthropic Messages (issue 5, PR 5b)

Configura `ANTIFAZ_API_KEY` y `ANTIFAZ_ANTHROPIC_API_KEY` en `.env` (y `ANTIFAZ_ANTHROPIC_BASE_URL` solo si usas otro servidor; va **sin** `/v1`). Sin la clave de Anthropic, estas rutas responden 503.

```bash
curl http://localhost:8000/v1/messages   -H "x-api-key: $ANTIFAZ_API_KEY" -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json"   -d '{"model": "claude-sonnet-4-5", "max_tokens": 200, "messages": [{"role": "user", "content": "Mi DNI es 12345678Z"}]}'
```

Con el SDK de Anthropic: `base_url="http://localhost:8000"` y `api_key=<ANTIFAZ_API_KEY>`. **Claude Code**: `ANTHROPIC_BASE_URL=http://localhost:8000` y `ANTHROPIC_API_KEY=<ANTIFAZ_API_KEY>` (o `ANTHROPIC_AUTH_TOKEN`, que llega como `Bearer`). Claude Code usa streaming, soportado desde la parte 5c (probado con el proveedor falso; la prueba documentada con Claude Code de verdad sigue pendiente). Antifaz no lee esas variables (solo las que empiezan por `ANTIFAZ_`), así que se puede arrancar en la misma terminal.

Rutas: `POST /v1/messages` y `POST /v1/messages/count_tokens`. Los pasos son los mismos que en OpenAI (clave, tope de tamaño, un solo `mask()`, una sola serialización revisada por la guardia, errores); el código de esos pasos es compartido (`api/proxy.py` y `providers/json_walk.py`). Lo propio de Anthropic:

1. **Clave**: en `x-api-key` (lo que envían Claude Code y el SDK) o en `Authorization: Bearer`, comprobada por la puerta como en todas las rutas. Al proveedor solo le llega `x-api-key` con `ANTIFAZ_ANTHROPIC_API_KEY`.
2. **Cabeceras**: solo se reenvían `anthropic-version` y `anthropic-beta`, y solo si su valor son letras, dígitos, `.`, `_`, `-` y comas (hasta 200 caracteres); si no → 400 `invalid_header`. Si el cliente no manda `anthropic-version`, Antifaz no se la inventa (el proveedor devolverá su error).
3. **Bloques permitidos** en `messages[].content`: `text`, `tool_use`, `tool_result`, `thinking` y `redacted_thinking`. En `system` y dentro de `tool_result.content`, solo `text`. Cualquier otro (`image`, `document`, `search_result`, `container_upload`, `server_tool_use`, resultados de herramientas del servidor, `citations` o uno nuevo) → 400 `antifaz_blocked`. `cache_control` no pasa por la lista. La `input` de `tool_use` puede tener cualquier `type` (son datos para la herramienta), pero las claves de adjunto (`source`, `data`, `file_id`…) y las URL `data:...;base64,` se bloquean también ahí. Ajustes con claves fijas: `thinking` solo `type` y `budget_tokens`; `tool_choice` solo `type`, `name` y `disable_parallel_tool_use`; otra clave → bloqueo. En `tools` no hay lista de tipos (hay herramientas del servidor como `web_search_20250305`), pero fuera de `input_schema` las claves de adjunto bloquean; dentro de `input_schema` no, porque una propiedad puede llamarse `data` o `file`, y solo se bloquea un objeto con `"type": "base64"`, que no es un tipo de JSON Schema (más las URL `data:` que se bloquean en todo el cuerpo). Todos sus textos se enmascaran. `metadata.user_id` y cualquier campo nuevo se enmascaran como texto.
4. **Razonamiento (invariante 9)**: los bloques `thinking` y `redacted_thinking` de los mensajes se copian **sin tocar**, con su `signature` o `data`: no se enmascaran ni se escapan (sus `[[` siguen igual). Vienen del modelo, que solo vio texto enmascarado, así que llevan marcadores, no datos. Aun así se pasan por el detector: si uno lleva un dato que hay que ocultar (alguien lo editó), la petición se bloquea, porque no se puede enmascarar sin romper la firma. La guardia sigue revisando todos los bytes. Solo se aceptan sus claves exactas (`thinking`: `type`, `thinking`, `signature`; `redacted_thinking`: `type`, `data`), todas de texto; otra clave → bloqueo. `signature` y `data` son opacos (firma y razonamiento cifrado) y no se pueden revisar: riesgo aceptado en el ADR-0013. Los marcadores que ya lleva el razonamiento (de un turno anterior) se **reservan**: un dato nuevo de esta petición no recibe ese número (si `[[ES_DNI_1]]` está en el razonamiento, el DNI nuevo es `[[ES_DNI_2]]`), y como los reservados no están en la tabla, la respuesta los deja tal cual en vez de poner un valor equivocado. Un bloque con forma de `thinking` dentro de la `input` de una herramienta no cuenta: se enmascara como cualquier dato.
5. **Respuesta**: se restauran los bloques `text` y los textos de `tool_use.input` (los valores, no las claves). `thinking`, `redacted_thinking`, las firmas y los bloques desconocidos pasan sin tocar.
6. **`count_tokens`**: se enmascara igual que `/v1/messages` y la respuesta del proveedor (solo números) se devuelve tal cual. `stream` se comprueba igual: no booleano → 400; `true` → 400 `streaming_not_supported`, por coherencia (`count_tokens` no tiene streaming).
7. `stream: true` → la respuesta llega en streaming (ver [Streaming](#streaming-issue-5-pr-5c)). El código de estado 2xx del proveedor se devuelve igual (en las dos rutas). Un texto con un sustituto suelto de UTF-16 (`"\ud800"`), que no se puede enviar en UTF-8, → 400 `invalid_request`.

"Sin tocar" quiere decir que las cadenas son idénticas: el proxy trabaja con el JSON parseado, así que el formato (espacios, escapes `é`) puede cambiar al volver a serializar, pero no el contenido ni la firma. Los tests comparan el bloque serializado dentro de los bytes enviados y de la respuesta.

## Streaming (issue 5, PR 5c)

Con `"stream": true` en `/v1/chat/completions` o en `/v1/messages`, la respuesta llega como eventos SSE (`text/event-stream`) y Antifaz pone los datos en su sitio **al vuelo**. La petición se trata igual que sin streaming: un solo `mask()`, y la guardia revisa los bytes exactos justo antes de enviarlos. Si el proveedor responde con error (429, 5xx…), se devuelve su cuerpo tal cual, como en 5a y 5b (después de comprobar que no repite una clave). `count_tokens` no tiene streaming: `stream: true` ahí → 400 `streaming_not_supported`.

Cómo se restaura ([ADR-0013](adr/0013-proxy.md)):

- **Texto**: cada trozo sale en cuanto llega, salvo el final que **podría** ser el principio de un marcador o de un escape (`[`, `[[`, `[[ES_D`, `[[ es_dni_1 `…), con el mismo patrón que `restore()`. Solo se retiene lo que todavía puede acabar siendo un marcador **de esta petición**: `[[ES_D` se retiene si esta petición emitió `[[ES_DNI_1]]`, pero un enlace wiki `[[Pagina_De_Ejemplo]]`, `[[EMAIL_1` sin emails en la petición o una ristra de letras salen al momento (`restore()` los dejaría igual). De un tramo de `[` seguidos solo se retienen los dos últimos. Tope de seguridad: 64 caracteres (`MAX_HOLDBACK`); solo se pasa con muchos espacios o tabuladores dentro de `[[ ... ]]`, y entonces se suelta el texto tal cual (sale como marcador, nunca como un valor equivocado) sin cortar el stream. Da igual cómo lleguen cortados los trozos: el texto final es el mismo que con `restore()` (invariante 3; tests de propiedades con cortes al azar, también en mitad de un carácter UTF-8, que se decodifica poco a poco).
- **OpenAI**: `delta.content` y `delta.refusal` de cada `choice`. Los `arguments` de `tool_calls` (y del antiguo `function_call`) se acumulan por (choice, índice de la herramienta) y se envían restaurados **en un solo delta**, en el chunk que cierra la choice (`finish_reason`). El primer delta de cada llamada sigue llevando `id`, `type` y `name`, con los `arguments` vacíos. `[DONE]` vacía lo que quede. `stream_options` solo admite `include_usage` e `include_obfuscation`, con `true` o `false`; otra cosa → 400 `invalid_request`.
- **Anthropic**: `text_delta` por índice de bloque. `input_json_delta` se acumula por bloque y sale restaurado como **un** `input_json_delta` justo antes de `content_block_stop` (siempre JSON válido, invariante 4). `thinking_delta` y `signature_delta` pasan **byte a byte** (invariante 9), igual que los bloques `thinking` y `redacted_thinking`. `ping`, `error`, `message_start` y `message_delta` pasan sin tocar; `message_stop` vacía lo que quede.
- **Eventos o campos desconocidos**: pasan sin tocar (ni se restauran ni se bloquean) y se cuentan en memoria (`app.state.stream_counters`, solo el número).
- Si el proveedor ignora `stream` y responde con JSON, se restaura como una respuesta normal.

El formato SSE lo lee un módulo propio y pequeño (`providers/sse.py`, sin dependencias): finales de línea CRLF, LF o CR (también partidos entre dos trozos), `data:` en varias líneas, comentarios y campos desconocidos. Un evento que no se cambia sale **tal cual llegó**, byte a byte.

**Fallos a mitad del stream** (el 200 ya se envió): sale el texto que ya es seguro (restaurado; un marcador a medias sale tal cual, porque es un marcador y no un dato), después **un** evento de error con el formato del proveedor y un mensaje fijo, y se cierra. Unos argumentos de herramienta a medias no se envían.

| Caso | `code` en OpenAI |
|---|---|
| Se agota el tiempo de lectura | `upstream_timeout` |
| Se corta la conexión | `upstream_unavailable` |
| SSE mal formado, UTF-8 inválido, una línea de más de 1 MiB o un evento de más de 4 MiB | `bad_upstream_response` |
| Más de 4 MiB de argumentos de herramientas acumulados | `stream_limit_exceeded` |
| Un evento repite una clave configurada | `bad_upstream_response` |
| El stream acaba sin `[DONE]` o sin `message_stop` | `upstream_stream_cut` |

En OpenAI el error es `data: {"error": {"message": ..., "type": "antifaz_error", "code": ...}}`; en Anthropic, `event: error` con `{"type": "error", "error": {"type": "api_error", "message": ...}}`. Ningún mensaje repite lo recibido; el log solo guarda el `code`.

**Claves (invariante 13).** Se revisa lo que llega del proveedor y, sobre todo, **lo que Antifaz envía** después de restaurar (unos argumentos con la clave escrita como `\u0063...` quedarían en claro al parsearlos y volver a serializarlos). Cada evento se revisa tal cual y decodificado hasta tres capas (JSON dentro de cadenas y escapes deshechos). Además, cada cadena de cada evento se une al final de la misma cadena en los eventos anteriores, con **un final por campo** (índice de choice o de bloque más el nombre del campo, conocido o no: `content`, `refusal`, `reasoning_content`, `thinking`, `cited_text`…), así una clave partida entre dos deltas se detecta aunque lleguen otros campos o choices en medio. Si la cabecera `Content-Type` del proveedor ya lleva una clave → 502 antes de empezar. Sin streaming se hace lo mismo con la respuesta ya restaurada (`tool_calls[].function.arguments`, `tool_use.input`): 502 `bad_upstream_response` fijo.

**Cierre.** Si el cliente se va a mitad, la respuesta cierra la conexión con el proveedor aunque esté esperando datos (`RelayResponse`; hay un test con una desconexión ASGI de verdad).

Tests: `tests/unit/test_stream_restore.py`, `test_sse.py`, `test_stream_transformers.py`; propiedades en `tests/property/test_stream_restore_property.py`, `test_sse_property.py` y `test_stream_protocol_property.py`; ataques en `tests/redteam/test_streaming.py` (429 y 5xx, proveedor lento, cortes en mitad de un marcador, UTF-8 partido o inválido, marcadores de otra petición, razonamiento, eventos desconocidos, claves y desconexión del cliente).

## Tests de contrato con los SDK oficiales (issue 5, PR 5d)

Comprueban que un cliente real (los SDK oficiales `openai` y `anthropic` de Python) entiende todo lo que Antifaz devuelve y recibe los datos restaurados. Sin red y sin claves:

```
SDK oficial --(httpx2.ASGITransport, en el mismo proceso)--> Antifaz --(httpx.MockTransport)--> proveedor falso
```

- Los SDK reciben un `http_client` que llama a la app ASGI de Antifaz dentro del proceso (los SDK usan `httpx2`, un fork de httpx; Antifaz sigue con `httpx`). Detrás, el proveedor falso (`tests/contract/conftest.py`) sirve las respuestas de `tests/contract/fixtures/` cortadas en trozos de 37 bytes (los eventos y los marcadores llegan partidos) y guarda todo lo que recibe. Además, mientras corren, cualquier conexión que no sea a `localhost` falla, y se quitan del entorno las variables `OPENAI_*` y `ANTHROPIC_*`. Los SDK van con `max_retries=0`, para que un 429 o un 502 llegue al test.
- Qué cubren: `chat.completions.create` (texto, herramientas, streaming de texto y de `tool_calls`, y el helper `chat.completions.stream`), `messages.create` (texto, `tool_use`, `thinking`), `messages.stream` (`text_delta`, `input_json_delta`, `thinking_delta` y `signature_delta`), `messages.count_tokens` y los errores: 401 → `AuthenticationError`, 400 → `BadRequestError`, 429 → `RateLimitError` y 502 → `InternalServerError`, con y sin streaming. También la clave de Antifaz equivocada (401) y un adjunto bloqueado (400).
- Cada test comprueba dos cosas: que ningún valor sembrado (DNI, email, IBAN, en cualquier espaciado) ni la clave de Antifaz llegó al proveedor falso, y que el SDK recibe los valores restaurados (texto, argumentos de herramientas como JSON válido, `tool_use.input`). El razonamiento (`thinking` y su firma) sale igual que entró (invariante 9), y un marcador de un razonamiento anterior no se restaura con el dato nuevo.
- **Respuestas escritas a mano y grabadas**: las escritas a mano copian la forma de las respuestas oficiales y solo llevan marcadores y datos inventados; las `openai_recorded_*` son respuestas reales de OpenAI (ver «Prueba real» más abajo). Cada una dice de dónde sale (`provenance`: `hand-written` o `recorded AAAA-MM-DD model X`). Ver `tests/contract/fixtures/README.md`.
- `test_fixtures_sanitised.py` revisa **todas** las respuestas guardadas: nada que parezca una clave (`sk-`, `sk-ant-`, `Bearer`, cadenas largas de alta entropía que no estén en la lista de valores falsos), ninguna cabecera `Authorization`, `x-api-key` o `Cookie`, ningún dato personal (con el detector de Antifaz y patrones de email y teléfono) fuera de la lista de ejemplos sintéticos, y un `provenance` válido. Hay tests de que ese revisor sí detecta cada caso.
- Los SDK son dependencias **solo de desarrollo** (grupo `dev`, no se distribuyen); sus licencias están en [licencias.md](licencias.md).

### Prueba real con OpenAI (2026-09-30)

Primera prueba contra un proveedor de verdad, con OK de Miquel para gastar unos céntimos.

- **Montaje**: el SDK oficial `openai` habla con Antifaz dentro del proceso, y Antifaz habla con la API real de OpenAI (`gpt-4.1-nano-2025-04-14`, el modelo de chat más barato sin razonamiento de la lista de la cuenta). Un transporte intermedio guardaba en memoria los bytes exactos que salían hacia OpenAI y los que volvían. Solo datos sintéticos (DNI `12345678Z`, email `ana.prueba@example.com`).
- **4 peticiones**: (a) «repite esta frase» con el DNI y el email; (b) lo mismo en streaming; (c) llamada a una herramienta `lookup_customer(dni)`; (d) lo mismo en streaming.
- **Lo que recibió OpenAI**: en las 4, solo marcadores (`[[ES_DNI_1]]`, `[[EMAIL_1]]`); ningún valor sintético, ni tal cual ni sin espacios ni signos.
- **Resultado**: el cliente recibió en las 4 los valores originales restaurados; los argumentos de herramienta, como JSON válido (`{"dni": "12345678Z"}`). En streaming, OpenAI partió cada marcador en 5–7 trozos (` [[`, `ES`, `_D`, `NI`, `_`, `1`, `]]`) y Antifaz los unió bien. Con la herramienta forzada (`tool_choice`), OpenAI termina con `finish_reason: "stop"`, no `tool_calls`.
- **Coste**: 202 tokens de entrada y 60 de salida en total (55 + 55 + 76 + 76). Con el precio público de `gpt-4.1-nano` (0,10 $ por millón de entrada y 0,40 $ por millón de salida), unos 0,00005 $: menos de una centésima de céntimo. Listar modelos no cuesta.
- **Grabaciones**: las 4 respuestas quedaron como `tests/contract/fixtures/openai_recorded_*` (solo cuerpos, sin cabeceras, con ids falsos) y tienen sus tests de contrato.
- **Pendiente**: la prueba real con Claude Code y la API de Anthropic (no hay crédito de Anthropic todavía).

## `antifaz verify` (issue 5, PR 5d, primera versión)

Para que quien instala Antifaz compruebe **con su configuración** que los datos no salen, sin gastar nada:

```bash
uv run antifaz verify
```

- Lee la configuración como el servidor (variables `ANTIFAZ_*` y `.env`) y comprueba primero que arrancaría (mismas reglas que `create_app`); si no, código 2 y el nombre de la variable, nunca su valor.
- **Nunca llama al proveedor.** Levanta la pasarela dentro del proceso (`httpx.ASGITransport`) con un proveedor falso (`httpx.MockTransport`) que guarda los bytes que recibe y responde con los marcadores que ha visto. Para la prueba cambia las URL y las claves de los proveedores por unas falsas (así da igual que falten o que sean reales) y acepta un `Host` más (`antifaz-verify.invalid`).
- Siembra seis tipos de dato sintéticos (DNI, NIE, IBAN, email, teléfono y tarjeta) en 16 peticiones (más 2 para las claves): en OpenAI, mensaje de sistema, de usuario, parte de contenido, argumentos de una herramienta y su resultado, con y sin streaming; en Anthropic, `system` (texto y bloques), mensaje de usuario, bloque de texto, `tool_use.input` y `tool_result`, con y sin streaming, y `count_tokens`.
- Falla (código 1) si un valor sembrado llega al proveedor falso (tal cual o con otros espacios y mayúsculas), si la clave de Antifaz llega al proveedor, si una respuesta trae alguna clave configurada (hay una petición por ruta en la que el proveedor falso devuelve las cabeceras que recibió), si la pasarela bloquea o da error en una petición que debía pasar (el enmascarador dejó un valor y la guardia lo paró), si un valor no vuelve en la respuesta, o si una petición no llega al proveedor falso exactamente una vez (`NO UPSTREAM`: la pasarela contestó sin preguntar y la prueba no demostraría nada). Las 2 peticiones de claves, además, tienen que acabar en 502.
- Si `ANTIFAZ_MAX_BODY_BYTES` es tan pequeño que algunas peticiones de prueba dan 413, sale con código 2 ("could not verify", súbelo y repite): no es un fallo de privacidad, porque esas peticiones no salieron.
- El informe solo dice **tipos y sitios** (`LEAK ES_DNI, IBAN reached the provider -- openai chat: tool result`), nunca valores ni claves. Si todo va bien, un `PASS` de cinco líneas.
- Usa la misma política que el servidor (hoy la de fábrica). Un tipo que la política deja pasar (`allow`) no se comprueba y aparece como nota.
- No usa la comprobación de claves de la propia pasarela (`api/proxy.py`) para decidir: tiene la suya, para ver lo que la pasarela se deje.
- Tests: `tests/unit/test_verify.py`, también con la pasarela saboteada (enmascarador ciego → `LEAK` y código 1; enmascarador que deja un valor → `BLOCKED`; comprobación de claves desactivada → `KEY`; clave de Antifaz reenviada, en las dos rutas → `LEAK`; respuesta sin restaurar → `RESTORE`; `count_tokens` contestado sin proveedor → `NO UPSTREAM`). En estos tests y en los de contrato, cualquier búsqueda de nombre o conexión de socket que no sea a la propia máquina falla.

Limitaciones de esta primera versión: los valores sembrados son fijos (los mismos que usan los tests), la política no se lee todavía de un fichero, y no revisa lo que se escribe en los logs (eso lo cubren los tests de las invariantes 8 y 13).

## `antifaz init` (issue 41)

Decisión en [ADR-0017](adr/0017-instalacion-y-secretos-en-la-cli.md) (aceptada). Escribe un `.env` listo para arrancar sin que ninguna clave pase por la línea de órdenes:

```bash
uv run antifaz init                       # pregunta las claves de los proveedores (ocultas)
uv run antifaz init --path /srv/antifaz   # en otra carpeta (por defecto, la actual)

# CI o scripts: nada de preguntas; las claves, por el NOMBRE de una variable o por stdin
OPENAI_FOR_ANTIFAZ=... uv run antifaz init --non-interactive --openai-key-env OPENAI_FOR_ANTIFAZ
uv run antifaz init --non-interactive --anthropic-key-stdin < clave.txt
uv run antifaz init --non-interactive --force --allowed-hosts antifaz.internal,localhost
```

- **Clave de Antifaz**: `secrets.token_hex(32)`, hecha en la máquina (se saca otra si no pasara la comprobación de arranque). Se muestra **una vez**, solo si la salida es un terminal, o con `--show-key`. Si no, se dice que está en `.env`.
- **Claves de proveedores**: con `getpass` (no se ven al escribir) o, con `--non-interactive`, de `--openai-key-env VAR` / `--anthropic-key-env VAR` o de `--openai-key-stdin` / `--anthropic-key-stdin` (como mucho una por stdin). Ninguna opción acepta una clave: `--*-key-env` solo acepta un nombre de variable válido, y si se le pasa algo que parece una clave lo rechaza sin repetirlo. Se quitan espacios, comillas, una marca BOM y un `Bearer ` delante. Por stdin, como mucho 4 KiB, y nunca desde un terminal (hay que usar una tubería, o el modo interactivo). Si `getpass` no puede ocultar lo que se escribe, `init` se para. Los mensajes no repiten el nombre de `--*-key-env` (hay claves con forma de nombre, como `gsk_…`) ni la ruta de `--path`. Error (nombrando la variable, nunca el valor) si la clave es la de ejemplo, no es ASCII imprimible o lleva `"`, `'`, `#`, `$`, `\` o espacios; solo un aviso si no empieza por `sk-` (OpenAI) o `sk-ant-` (Anthropic). Un proveedor sin clave no tiene línea (su ruta da 503). Las variables `ANTIFAZ_*_API_KEY` del entorno **no** se copian solas.
- **`.env` existente**: en interactivo hay que escribir `yes`; con `--non-interactive` hace falta `--force`; si no, código 2 y no cambia nada. Antes se guarda `.env.bak-AAAAMMDD-HHMMSS` (con `-1`, `-2`… si ya existe: una copia nunca pisa otra), creada con `O_EXCL` y `0600`. Si `.env` es un enlace o una carpeta, no se toca.
- **Escritura atómica**: temporal `0600` con `O_EXCL` en la misma carpeta → `fsync` → validación con `check_safe_to_start` leyendo **solo ese archivo** (las variables de entorno del proceso no cuentan) → copia de seguridad → `os.replace` (en Windows se reintenta si el archivo está bloqueado). Pase lo que pase (también Ctrl-C), se borra el temporal; el `.env` anterior queda igual, con un mensaje genérico (si la copia ya se había hecho, dice cómo se llama).
- **Formato**: `VAR=valor` sin comillas, sin comentarios detrás del valor y sin `$`, saltos de línea LF y sin BOM. Lo leen igual pydantic-settings y `docker --env-file` (un test lo compara). La plantilla va en el paquete (`src/antifaz/cli/env_template.py`) porque en la imagen no hay `.env.example`; un test comprueba que tienen las mismas variables en el mismo orden.
- **Sin terminal**: en modo interactivo, si stdin no es un terminal, código 2 y "usa `-it` o `--non-interactive`".
- **Sin red**: no habla con nadie (un test lo comprueba quitando los sockets).
- **Códigos de salida**: 0 escrito; 2 uso incorrecto, `.env` existente sin permiso o un valor malo (no se escribe nada); 1 no se pudo escribir o validar (el `.env` anterior sigue igual).
- **Dentro de Docker** (issue 42): con `-it` la clave se imprime y Docker la guarda en `docker logs` mientras exista el contenedor, así que usa siempre `--rm`. En Linux, añade `--user "$(id -u):$(id -g)"` para que el `.env` sea tuyo y no del usuario del contenedor.
- **Copias y rotación**: las copias `.env.bak-…` contienen las claves antiguas. Después de rotar una clave, borra las copias que la contienen.
- **Permisos en Windows**: `0600` no existe; el archivo hereda los permisos de la carpeta (en el perfil del usuario, solo él). No ejecutes `init` en una carpeta compartida.
- Tests: `tests/unit/test_cli_init.py` (claves canario que nunca salen por stdout, stderr ni logs, también en los errores; `getpass`; copias; fallo de `os.replace` y de validación; `0600` en POSIX; terminal y `--show-key`; Hypothesis para la clave generada). El job `cli-windows` de CI pasa los tests de la CLI en Windows.

## `antifaz doctor` (issue 43)

Parte de [ADR-0017](adr/0017-instalacion-y-secretos-en-la-cli.md): `init` no usa la red y comprobar que las claves funcionan es trabajo de `doctor`, solo si se pide.

```bash
docker compose exec antifaz antifaz doctor               # dentro del contenedor que ya corre
docker compose exec antifaz antifaz doctor --providers   # y además prueba las claves (gratis)
uv run antifaz doctor --path /srv/antifaz --url http://127.0.0.1:8000 --timeout 5
```

Tres comprobaciones, en orden:

1. **Configuración**: lee lo mismo que la pasarela (el `.env` de `--path`, por defecto la carpeta actual, y encima las variables `ANTIFAZ_*` del entorno, que ganan, como en pydantic-settings) y pasa `check_safe_to_start`. Si un valor no se puede leer, solo nombra la variable (el error de Pydantic repite el valor, así que no se muestra). Dice qué proveedores tienen clave (solo los nombres) y si el NER está activado. Ojo: con Compose la pasarela solo lee `.env`; si tu terminal tiene variables `ANTIFAZ_*`, `doctor` desde fuera puede ver otra configuración. Dentro del contenedor (`docker compose exec`) ve exactamente la de la pasarela.
2. **Pasarela**: `GET {url}/healthz`, que es pública: **no se envía ninguna clave**. Sin proxy (`trust_env=False`: se ignoran `HTTP(S)_PROXY` y `NO_PROXY`, la pasarela es local), sin seguir redirecciones, con tiempo máximo y leyendo como mucho 64 KiB. El `Host` es el de `--url` si está en `ANTIFAZ_ALLOWED_HOSTS`; si no, el primero de la lista que no es comodín (o el comodín con `healthcheck.` delante, como el healthcheck del contenedor). Si no hay configuración legible, el de `--url`. Muestra la versión solo si tiene forma de versión, y el estado del NER (`starting` es un aviso; `circuit_open`, `closed` o uno desconocido, fallo). Un 400 suele ser `ANTIFAZ_ALLOWED_HOSTS` distinto en la pasarela (no se ha reiniciado tras cambiar `.env`). Si no responde: "docker compose up -d".
3. **Proveedores**, solo con `--providers`: para cada proveedor con clave, `GET` de su lista de modelos directamente al proveedor (OpenAI `{base}/models` con `Authorization: Bearer`; Anthropic `{base}/v1/models` con `x-api-key` y `anthropic-version: 2023-06-01`). Listar modelos no cuesta nada. Sin redirecciones (una clave nunca sigue a un `Location`: un 3xx es error), con tiempo máximo y **sin leer nunca el cuerpo** de la respuesta (un proveedor puede repetir la clave en el error): solo cuenta el código. 200 bien; 401/403 clave rechazada; 3xx redirección no seguida; tiempo agotado; no se puede conectar; otro código. Una clave con formato imposible no se envía. Estas peticiones **sí** respetan `HTTP(S)_PROXY`, igual que el cliente de la propia pasarela (`providers/http.py`, httpx por defecto): así se prueba el mismo camino que usará la pasarela detrás de un proxy corporativo.

- **Nunca imprime** valores, claves, cuerpos, excepciones ni URL base (una URL puede llevar `usuario:contraseña`); `--url` con usuario, consulta o fragmento se rechaza sin repetirlo. Mensajes fijos que nombran variables.
- **Salida**: texto plano con `✓`/`✗`; si la consola no puede escribirlos (cp1252 en Windows), `OK`/`FAIL`. Al final, los pasos siguientes: `antifaz verify`, `antifaz setup claude-code` (llega con el issue 31) y la documentación.
- **Códigos de salida**: 0 todo bien; 1 algo falla; 2 uso incorrecto (`--path` que no existe, `--url` mala, `--timeout` fuera de 0–120 s).
- Tests: `tests/unit/test_cli_doctor.py` (claves canario que nunca salen por stdout, stderr ni logs, también cuando el proveedor las repite en el cuerpo, en una cabecera o en la excepción; `MockTransport` para 200, 401, 403, 307, 500, tiempo agotado y error de conexión; `/healthz` caído, 503, 400, cuerpo raro o enorme; elección del `Host`; sin `--providers` no hay ninguna petición a proveedores; con servidores reales en 127.0.0.1, que `/healthz` no pasa por el proxy y los proveedores sí; consola cp1252; códigos de salida). También en el job `cli-windows`. E2E en `tests/e2e/test_doctor.py`: desde el host contra el puerto publicado y con `docker compose exec ... doctor --providers` contra el proveedor falso, que comprueba que cada proveedor recibe su clave y nunca la de Antifaz.

## NER: la infraestructura (issue 6, PR 6a)

Decisión en [ADR-0016](adr/0016-ner-en-procesos-aparte.md) (propuesta). Esta parte trae todo lo que rodea al modelo de nombres **sin el modelo**: el modelo real (GLiNER), su descarga y su manifiesto llegan en 6b. Hasta entonces el NER está apagado y, si se enciende, Antifaz no arranca.

**Recorrido.** `mask()` pide al detector todos los textos de la petición de una vez (`Scanner.scan_many`). Para cada texto se construye la vista normalizada del ADR-0014; los patrones y el NER leen esa misma vista y sus posiciones vuelven al original con el mismo mapa:

1. **Caché** (`detect/ner/cache.py`): si el texto ya pasó por el NER con el mismo modelo, etiquetas, umbral y troceador, se usan sus spans. Clave BLAKE2b con una clave secreta aleatoria por proceso; guarda solo posiciones y tipos; LRU con `ANTIFAZ_NER_CACHE_ENTRIES` entradas y, además, como mucho 200.000 spans en total (unos pocos textos llenos de nombres no llenan la memoria).
2. **Ventanas** (`detect/ner/chunker.py`): 200 tokens que se solapan 50 (un token es una palabra de hasta 40 letras o cifras, o un signo). GLiNER lee como mucho 384 palabras y corta el resto sin avisar; con el solape, una entidad de menos de 50 tokens en la frontera se ve entera en alguna ventana.
3. **Pool de procesos** (`detect/ner/pool.py` y `worker.py`): procesos propios con `spawn`, que cargan el modelo una vez. Hablan JSON por un `Pipe` (nunca `pickle`) con tamaño máximo; el hijo escribe en el dispositivo nulo, sin logging, sin red (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`) y solo devuelve códigos de error fijos.
4. **Comprobación** (`detect/ner/engine.py`): una lista por ventana, `[inicio, fin, etiqueta, puntuación]` con enteros dentro de la ventana, una etiqueta pedida y una puntuación entre 0 y 1. Cualquier otra cosa → `DetectorFailed` → 400 `antifaz_blocked`.
5. **Solapamientos**: el NER es la capa con menos prioridad, y el perdedor de un solapamiento parcial se recorta en vez de descartarse (sus trozos sin letras ni cifras se tiran). Todo carácter alfanumérico que alguna capa detectó queda enmascarado.
6. **Propagación** (`mask/propagate.py`): cada valor que encontró el NER se enmascara en todos los textos de la petición, con la regla de la guardia (normalizado; 6 o más letras y cifras sin límites de palabra; más corto, con límites, y un vecino que se va a enmascarar cuenta como límite). Así la guardia no bloquea una petición porque el modelo vio un nombre en un mensaje y no en otro. Solo se propagan valores enteros (que empiezan y terminan en límite de palabra: no los trozos que quedan al recortar alrededor de un DNI) y de 3 o más letras y cifras; más de 200 valores distintos del NER en una petición la bloquean. La propagación normaliza carácter a carácter y la guardia la cadena entera: coinciden en letras, cifras, acentos, homoglifos y espacios (test de propiedad); en secuencias raras cuya forma NFKC depende del vecino, la guardia ve lo que la propagación no vio y bloquea (falla cerrada).

**Límites por petición.** `ANTIFAZ_NER_TIMEOUT_SECONDS` es el tiempo del NER para **toda la petición**, no por llamada: un único plazo (reloj monotónico) cubre esperar un proceso libre, esperar a uno que aún carga el modelo, todas las llamadas por lotes y recibir la respuesta **entera** (un vigilante mata el proceso al llegar el plazo, así que una respuesta que se queda a medias tampoco retiene la petición). Además, una petición de más de 1.024 ventanas (~1 MB de texto) se bloquea antes de llegar al modelo.

**Fallos.** Si el plazo se agota, el proceso se mata (`kill()`), se arranca otro y la petición se bloquea con 400. Lo mismo si el proceso muere, no carga el modelo o responde algo mal formado. Si el backend lanza una excepción, el proceso sigue vivo y la petición se bloquea. Un proceso nuevo que todavía carga no se mata: la petición se bloquea y la siguiente lo usa. Tras 3 fallos seguidos se abre el **cortacircuitos**: toda petición se bloquea al momento durante 1 s, 2 s, 4 s… (máximo 60 s) y luego se vuelve a probar; se cierra con la primera respuesta buena. El log solo dice qué pasó ("replaced", "circuit open"), nunca el texto, y `/healthz` añade `"ner": "ok" | "starting" | "circuit_open" | "closed"` cuando el NER está encendido. Al parar, los procesos se matan sin cerrar la tubería que otro hilo está leyendo. `ProcessPoolExecutor` no sirve: no mata un proceso colgado hasta Python 3.14.

**En la API**, `mask_request` corre en un hilo (`anyio.to_thread.run_sync`) con un limitador propio de 8 hilos (`MASK_THREADS`): ni los patrones ni la espera al NER bloquean el bucle de eventos ni ocupan los hilos compartidos de anyio. Arrancar y parar los procesos usa otro limitador aparte, así que una pasarela ocupada no retrasa su propia parada. Los procesos arrancan con la app (si el modelo no carga, la app no arranca) y se paran con ella.

**Manifiesto** (`detect/ner/manifest.json` y `manifest.py`): cada archivo del modelo con su tamaño y SHA-256. Antes de cargar se exige que el directorio tenga **exactamente** esos archivos (ni uno de menos, ni uno de más). Se recorre sin seguir enlaces y se rechaza cualquier enlace simbólico, unión de Windows (junction) u otro punto de reanálisis, y todo lo que no sea un archivo normal o una carpeta. La pasarela lo comprueba al arrancar y **cada proceso del pool lo vuelve a comprobar** (también el hash del propio manifiesto) justo antes de cargar el modelo, cada vez que arranca: un archivo cambiado después del arranque detiene ese proceso y la petición se bloquea. Queda un instante entre esa comprobación y la carga (TOCTOU); aprovecharlo exige poder escribir en la carpeta del modelo, que debe ser de solo lectura para el usuario de la pasarela. El hash del manifiesto entra en la clave de la caché. El de 6a no tiene archivos: rechaza cualquier directorio.

**Variables** (todas opcionales; el NER está apagado por defecto):

| Variable | Por defecto | Qué hace |
|---|---|---|
| `ANTIFAZ_NER_ENABLED` | `false` | Enciende el NER. Con `true`, si el modelo falta o no coincide con el manifiesto, o el backend no está instalado, Antifaz no arranca |
| `ANTIFAZ_NER_MODEL_DIR` | vacía | Carpeta del modelo descargado (comando de descarga en 6b) |
| `ANTIFAZ_NER_TIMEOUT_SECONDS` | `10` | Tiempo máximo del NER para toda la petición (todas sus ventanas, en lotes de 16) |
| `ANTIFAZ_NER_WORKERS` | `1` | Procesos del pool; cada uno ocupa la memoria de un modelo |
| `ANTIFAZ_NER_THRESHOLD` | `0.5` | Puntuación mínima de una entidad (mayor que 0, como mucho 1) |
| `ANTIFAZ_NER_CACHE_ENTRIES` | `10000` | Textos en la caché; `0` la apaga |

**Tests.** El backend falso (`detect/ner/fake.py`) no se puede elegir con la configuración: no hay variable para el backend y la pasarela usa siempre el de GLiNER; solo el código (los tests) puede pasar otra ruta de fábrica. Busca nombres de un diccionario y, con palabras clave, se cuelga, muere, lanza una excepción con el texto, escribe el texto en stdout, stderr y el log o responde mal. Con él: el proceso colgado se mata al llegar al tiempo máximo y la siguiente petición funciona; `os._exit` bloquea; ni el log (`caplog`) ni la salida de los procesos hijos (`capfd`) llevan el nombre ni el DNI centinela (invariante 8); con la guardia apagada, el proveedor falso no recibe ningún nombre (invariante 2); `restore(mask(x)) == x` con el NER (Hypothesis); la guardia nunca bloquea lo que produjo el enmascarador con la propagación (Hypothesis). Los tests del modelo real llevan el marcador `ner_model` y se saltan si no hay `ANTIFAZ_NER_MODEL_DIR`.

## NER con el modelo real (issue 6, PR 6b): opcional y apagado por defecto

El modelo de nombres (GLiNER, fijado por su commit en `detect/ner/manifest.json`) se instala con `make ner-model` y se mide con `make bench NER=1`. **Sigue apagado por defecto** (`ANTIFAZ_NER_ENABLED=false`) porque **no cumple el suelo de precisión del 85 % en nombres** que se fijó para elegir el umbral (ADR-0011 y su enmienda del 2026-10-01):

- En la partición dev de MEDDOCAN (2026-10-01), la precisión de PERSON contra cualquier dato personal anotado fue del 67,5–70,9 % según el umbral (~70 %): **cerca del 30 % de las detecciones de nombres tapan texto que no es un dato personal** (416–510 detecciones). Por tipo es aún menor (63–69 %). ADDRESS sí llega (~96 %).
- Por eso el umbral publicado es el de respaldo (el más preciso) y el resultado lleva `floor_met: false`. Las cifras de test están en `docs/benchmark.md`.
- **Actívalo solo si te vale que tape de más**: protege más nombres, pero el modelo de IA recibe más marcadores donde había palabras normales y puede responder peor.
- Coste en el PC de desarrollo (Ryzen 7 5700U, solo CPU), de `evals/results/2026-10-01-0.1.0-ner.json`: **unos 740 MB de RAM (RSS) por proceso** del pool (590 MB en una ejecución anterior: varía con lo que el proceso haya procesado) y, por documento de MEDDOCAN (unos 3.000 caracteres), **p50 1,9 s / p95 3,8 s** con la caché fría (con la caché caliente, 2,9 ms / 5,6 ms). Arrancar el pool (comprobar dos veces el SHA-256 de 1,16 GB y cargar el modelo) tardó **51 s** (`load_seconds`).

**Cómo activarlo.**

```bash
make ner-model   # instala el extra ner (torch solo CPU) y descarga el modelo fijado (1,16 GB)
# en .env:
ANTIFAZ_NER_ENABLED=true
ANTIFAZ_NER_MODEL_DIR=models/gliner_multi_pii-v1
ANTIFAZ_NER_THRESHOLD=0.6          # el umbral publicado en docs/benchmark.md
ANTIFAZ_NER_TORCH_THREADS=0        # hilos de torch por proceso; 0 = los que elija torch
```

Un `uv sync` sin `--extra ner` (por ejemplo `make install`) desinstala el extra. torch 2.14 no publica ruedas para **macOS con Intel** (x86_64): allí el extra no se instala y el NER no está disponible.

**Qué lee el modelo.**

- **Sin red**: Hugging Face en modo offline; el nombre del codificador (`microsoft/mdeberta-v3-base`) se cambia en memoria por la copia local del tokenizador; los pesos con `torch.load(weights_only=True)` y `strict=True`.
- **Vista para el NER** (`detect/ner/view.py`): antes de trocear, las URL largas, los bloques base64/hex de 32 caracteres o más sin espacios y las series de 6 o más cifras pegadas a una letra se cambian por espacios de la misma longitud. Las posiciones no se mueven y los validadores y patrones siguen leyendo el texto entero. Un bloque base64 de 4 KB costaba ~12 s de CPU del modelo, pasaba el tiempo máximo, mataba el proceso y la recarga dejaba bloqueadas todas las peticiones; y "1234567890Jordi" era una sola palabra para el modelo.
- **Ventanas recortadas al límite del modelo**: nuestras ventanas de 200 tokens pueden ser miles de subpalabras (números largos, CJK). Pasado su largo de entrenamiento el modelo deja de ver nombres sin dar error (medido: un nombre tras 190 números de 40 cifras, 2.330 subpalabras, no se veía). El backend vuelve a cortar cada ventana con el tokenizador real en trozos de 384 tokens como máximo (prompt incluido) que comparten 32 palabras (y nunca más de la mitad del presupuesto). Una palabra que no cabe en 384 tokens bloquea la petición.
- **Presupuesto por llamada**: si una llamada necesita más de 4 trozos por ventana de media (texto que cuesta muchas más subpalabras que palabras: CJK o emoji al azar), el backend la rechaza **antes** de ejecutar el modelo. El proceso responde un error fijo y sigue vivo (no cuenta para el cortacircuitos); la petición se bloquea con 400. Un texto normal es un trozo por ventana. Una petición legítima muy grande puede seguir pasando el tiempo máximo (el fallo seguro de siempre).
- Las detecciones de los trozos vuelven sin unir; el motor las une después de filtrar por su umbral. Así la respuesta a un umbral es la de uno más bajo filtrada por puntuación, y el benchmark ejecuta el modelo una sola vez por ventana para todos los umbrales candidatos (`ScoreCache`, probado igual que ejecutar cada umbral).

## Comparación con Presidio en Antifaz-Bench (issue 13)

`make bench PRESIDIO=1` mide también Presidio (`evals/presidio_baseline.py`) con el mismo `evaluate()` sobre la partición de test de MEDDOCAN, y lo compara con Antifaz solo en los tipos que cubren los dos y que MEDDOCAN anota (EMAIL, PHONE y PERSON). Publica también las fugas sobre **todos** los datos anotados de cada herramienta (ahí cuentan las detecciones de Presidio sin tipo en Antifaz, como LOCATION) y, en una tabla aparte y con su sesgo a la vista, el conjunto sintético (IBAN, DNI, NIE, tarjeta…). Las cifras de Antifaz con NER se leen del último `evals/results/*-ner.json`; el NER no se vuelve a ejecutar.

- **Dependencias:** presidio-analyzer, spaCy y el modelo `xx_ent_wiki_sm` (MIT) van en el grupo `bench` de `pyproject.toml` (con `>=`; las versiones exactas, 2.2.364, 3.8.16 y 3.8.0, están en `uv.lock` y en cada resultado): no se distribuyen, ni `uv sync` ni la CI los instalan, y un test comprueba que `uv export --no-dev` no los lista. El modelo viene de su release de GitHub, fijado por SHA-256 en `uv.lock`. El grupo y el extra `ner` se resuelven por separado (`[tool.uv] conflicts`) porque Presidio pide `numpy < 2.5`.
- **Español explícito:** motor NLP, registro y analizador solo en `es`, con los reconocedores puestos a mano: todos los que Presidio 2.2.364 activa por defecto y sirven para cualquier idioma o para español (tarjeta con contexto en español, cripto, fecha, email, IBAN, IP, MAC, teléfono con región ES, URL, NIF y NIE), el de pasaporte español (que viene desactivado) y el NER de spaCy. Al arrancar se comprueba que todo es español y que hay reconocedor para cada tipo; si no, falla en vez de seguir en inglés sin avisar (la configuración por defecto de Presidio es inglesa).
- **Tipos:** ES_NIF de Presidio se compara con ES_DNI de Antifaz; LOCATION, ORGANIZATION, DATE_TIME, URL, CRYPTO y MAC_ADDRESS no tienen equivalente y solo cuentan para las fugas. En el informe de Presidio, las etiquetas de tipos que solo busca Antifaz (CALLE, ID_ASEGURAMIENTO) no llevan tipo, así que sus fugas "cubiertas" hablan solo de lo que Presidio busca.
- **Sin red:** el email de Presidio usa tldextract, que por defecto descarga la lista de sufijos públicos; aquí usa la copia que trae el paquete.
- Los tests usan un analizador falso: Presidio no está instalado en la CI.

## Lista de modelos, `/antifaz/scan` y `openapi.json` (issue 29)

**`GET /v1/models`** devuelve la lista de modelos del proveedor configurado, detrás de la puerta (pide la clave como todas las rutas). El SDK de OpenAI pide `GET {base}/models` y el de Anthropic `GET /v1/models` con `anthropic-version`: es la misma ruta, así que **esa cabecera decide el proveedor**:

| La petición trae `anthropic-version` | Va a | Con | Parámetros de consulta permitidos |
|---|---|---|---|
| Sí | `ANTIFAZ_ANTHROPIC_BASE_URL` + `/v1/models` | `x-api-key` de `.env` + `anthropic-version` y `anthropic-beta` comprobados | `limit` (1–1000), `after_id`, `before_id` (letras, dígitos, `. _ : -`, hasta 200) |
| No | `ANTIFAZ_OPENAI_BASE_URL` + `/models` | `Authorization: Bearer` de `.env` | ninguno |

- Si el proveedor elegido no tiene clave → 503 `not_configured` (el otro sigue funcionando). `anthropic-beta` sola no cambia de proveedor.
- Consulta con **lista de permitidos**: un nombre que no está en la lista, repetido, en mayúsculas, vacío o con otros caracteres (`/`, `&` codificado, `%0d%0a`, espacios, `@`…) → 400 `invalid_query`, y no sale nada. La consulta que se envía se **reconstruye** con los valores ya comprobados. Un valor que contiene una clave configurada también → 400, y lo mismo si la clave llega **partida** entre varios valores (en cualquier orden) o aparece en la consulta reconstruida.
- Los valores de la consulta pasan por el **detector** (en el mismo hilo limitado que el proxy, con el NER si está activo): un id no se puede enmascarar, así que un dato personal (un DNI como `after_id`) → 400 `antifaz_blocked`. No se envía cuerpo, así que no hay bytes que revisar con la guardia de salida.
- Cabeceras: `anthropic-version` y `anthropic-beta` con la misma regla que en `/v1/messages` (letras, dígitos, `. _ - ,`, hasta 200); repetidas, o con una clave configurada dentro (también partida entre las dos) → 400 `invalid_header` (desde este issue, también en `/v1/messages` y `count_tokens`). Ninguna otra cabecera del cliente llega al proveedor.
- Respuesta: si es un error del proveedor (4xx/5xx), su cuerpo tal cual si es JSON (si no → 502); si es de éxito, tiene que ser un objeto JSON (si no → 502 `bad_upstream_response`). En los dos casos se descarta con 502 si repite una clave configurada, también escrita con escapes JSON (invariante 13). Una redirección → 502 `upstream_redirect`. No se reenvía ninguna cabecera del proveedor.
- Es un `GET`: la puerta no le pide `Content-Type`. `HEAD`, `POST` u otros métodos → 405 `method_not_allowed` (con clave).
- Todas las respuestas de la pasarela llevan `X-Content-Type-Options: nosniff`: el navegador nunca interpreta un cuerpo como HTML.

**`POST /antifaz/scan`** dice **dónde** hay datos personales en un texto, nunca cuáles son:

```bash
curl http://localhost:8000/antifaz/scan -H "Authorization: Bearer $ANTIFAZ_API_KEY" -H "Content-Type: application/json" \
  -d '{"text": "Mi DNI es 12345678Z"}'
# {"entities":[{"type":"ES_DNI","start":10,"end":19}]}
```

- Cuerpo: un objeto JSON con **solo** `text` (una cadena). Cualquier otra clave (`texts`, `image_url`, `source`, `file_id`…), un `text` que no sea cadena o un cuerpo que no sea un objeto → 400 `invalid_request`. Se lee con las reglas del proxy: tope `ANTIFAZ_MAX_BODY_BYTES` (413), UTF-8, sin claves repetidas, sin `NaN`.
- Usa **el mismo detector que el proxy** (`find_spans` en `mask/`, con el NER si está activo y la propagación de los nombres que encuentra), en el mismo hilo limitado. Si el detector falla o devuelve posiciones imposibles → 400 `antifaz_blocked` con mensaje fijo.
- La respuesta solo lleva `type`, `start` y `end` (`[start, end)`) por detección, también de los tipos que la política deja pasar (dice qué encontró; enmascarar lo decide la política). Las posiciones son **caracteres Unicode** (índices de Python): en JavaScript, un emoji cuenta como 2.
- No llama a ningún proveedor ni escribe el texto en ningún log.
- **Riesgo aceptado**: quien tiene la clave puede usarla para probar el detector (qué encuentra y qué se le escapa). Es lo mismo que puede hacer con la librería o con `antifaz scan`, que ya tiene.

**`docs/openapi.json`**: la pasarela sigue sin servir `/docs`, `/redoc` ni `/openapi.json` (ADR-0015; un test lo comprueba también con la clave). El esquema se publica como archivo estático, generado desde la propia app con `make openapi` (`scripts/export_openapi.py`, con una clave aleatoria que se tira: el esquema no lleva ninguna configuración). Un test compara el archivo del repo con el generado, así que nunca se queda atrás: si cambias una ruta, ejecuta `make openapi` y súbelo.

**Tests.** Las dos rutas entran solas en el test de la invariante 12 (recorre las rutas registradas). Además, cada ruta con clave, por pareja (ruta, método), tiene que estar en una de tres clases: envía un cuerpo al proveedor (pasa por la guardia), solo envía una consulta (que pasa por el detector) o no llama a ningún proveedor; una ruta nueva sin clasificar hace fallar el test. La invariante 13 tiene escenarios canario propios para las dos (consulta con la clave, cabeceras, errores y respuestas del proveedor con la clave, detector roto). Los ataques están en `tests/redteam/test_models_and_scan.py` y la prueba con los SDK oficiales (`models.list()`) en `tests/contract/`.

## La puerta cerrada por defecto (issue 20)

Decisión en [ADR-0015](adr/0015-puerta-cerrada-por-defecto.md) (propuesta). Todo pasa por un solo middleware (`api/gate.py`) antes de llegar a ninguna ruta, así que una ruta nueva queda protegida sin hacer nada.

**Arranque.** Antifaz se niega a arrancar, y dice qué variable falla sin mostrar su valor, si:

- `ANTIFAZ_API_KEY` falta, tiene menos de 32 caracteres o menos de 8 caracteres distintos, es el valor de `.env.example` (`change-me`, también escrito `change_me` o `ChangeMe`) o no es ASCII imprimible sin espacios. Genera una con `openssl rand -hex 32`;
- `ANTIFAZ_OPENAI_API_KEY` o `ANTIFAZ_ANTHROPIC_API_KEY` están definidas pero vacías, son el valor de ejemplo o no son ASCII imprimible sin espacios;
- `ANTIFAZ_ALLOWED_HOSTS` está vacía, tiene `*` o un comodín con menos de dos etiquetas detrás (`*.com`), o `ANTIFAZ_ALLOWED_ORIGINS` tiene algo que no sea `http(s)://host[:puerto]` (sin ruta ni barra final; `*` y `null` tampoco).

La app ya no se crea al importar el módulo: `uvicorn --factory antifaz.api.app:create_app` (es lo que hace `make dev`).

**Variables nuevas** (listas separadas por comas, o en JSON: `[ "a", "b" ]`):

| Variable | Por defecto | Qué hace |
|---|---|---|
| `ANTIFAZ_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Valores aceptados en la cabecera `Host` (sin el puerto y sin distinguir mayúsculas). Otro → 400. Admite `*.ejemplo.com`, no `*` ni `*.com` |
| `ANTIFAZ_ALLOWED_ORIGINS` | vacía | Orígenes de navegador aceptados, exactos y sin barra final (`https://intranet.ejemplo.com`). Vacía: toda petición con `Origin` → 403 |

**En cada petición**, en este orden:

1. `Host` en la lista (si no, 400), **antes** que la clave. Protege del DNS rebinding: una web ajena que apunta su dominio a la IP de la pasarela.
2. Ruta pública solo `/healthz`, con coincidencia exacta sobre la ruta ya decodificada (`scope["path"]`). Cualquier otra ruta, exista o no, pide la clave: `/healthz/`, `//v1/messages` o `/docs` también.
3. **Clave** en `Authorization: Bearer` o en `x-api-key` (en todas las rutas), con `hmac.compare_digest`. Si la cabecera viene dos veces, o si vienen las dos y no llevan las dos la clave → 401.
4. **`Origin`**: si viene y no está en `ANTIFAZ_ALLOWED_ORIGINS` → 403. No hay CORS: el navegador nunca recibe `Access-Control-Allow-Origin`, así que una web ajena no puede leer la respuesta ni pasar el preflight.
5. **`Content-Type`**: en todo lo que no sea `GET`, `HEAD` u `OPTIONS`, debe ser `application/json` (con `charset=utf-8` como único parámetro opcional). Un formulario o `text/plain`, que el navegador manda sin preflight → 415.
6. Al leer el cuerpo, un objeto con **claves repetidas** → 400. Si solo cambian en mayúsculas o anchura (`content` y `Content`) también, cuando es una clave que Antifaz lee (`READ_KEYS` en `providers/json_walk.py`), porque unos programas las leen como la misma y otros no. Un esquema con `Name` y `name` pasa.
7. Las claves de adjunto (`source`, `data`, `image_url`, `file`…) bloquean también en mayúsculas (`SOURCE`, `Data`).

Además: `X-Request-ID` siempre lo genera la pasarela (el del cliente se ignora); sin `/docs`, `/redoc` ni `/openapi.json` (el esquema está en [`docs/openapi.json`](openapi.json)); sin redirecciones de barra final (`/v1/messages/` → 404); los WebSocket se cierran siempre; el log de cada petición solo escribe la ruta si es una ruta registrada (si no, `-`), para que una clave o un DNI pegados en la URL no acaben en el log; y si el proveedor devuelve en su respuesta alguna de las claves configuradas (tal cual o con escapes JSON como `\u0061`), se descarta con un 502 fijo.

**Detrás de Docker o de un proxy inverso** (nginx, Traefik, Caddy):

- Pon en `ANTIFAZ_ALLOWED_HOSTS` el nombre con el que llegan los clientes (`antifaz`, el nombre del servicio en compose, o `antifaz.ejemplo.com`). Si el proxy reescribe `Host`, pon el que reescribe. `X-Forwarded-Host` no se mira.
- El proxy no debe añadir cabeceras CORS ni un `Origin` propio.
- `/healthz` es pública: si el proxy la expone, solo dice la versión.
- `root_path` no está soportado: si la sirves bajo un prefijo (`--root-path`), `/healthz` puede pedir clave, porque la lista pública compara la ruta exacta y, si no coincide, falla cerrada.

**Tests.** `tests/redteam/test_invariants_12_13.py` recorre las rutas que la app registra de verdad (con `iter_route_contexts`, porque en FastAPI `app.routes` guarda los routers incluidos y no sus rutas), cada una con GET, HEAD, POST, PUT, PATCH, DELETE y OPTIONS y con alias (barra final, doble barra, mayúsculas, último carácter codificado): sin clave, 401 salvo `/healthz`. Cada ruta del proxy pasa por la guardia de salida con los mismos bytes que recibe el proveedor, y si la guardia bloquea no sale nada (invariante 12). Otro test usa claves canario y recorre 401, 400, 403, 404, 413, 415, 502 y 504, errores del proveedor y un proveedor que devuelve las cabeceras que recibió, con los loggers en `DEBUG`: ninguna clave aparece en logs, cuerpos ni cabeceras (invariante 13). Los ataques están en `tests/redteam/test_gateway.py`.

## Decisiones técnicas del issue 1

| Decisión | Por qué |
|---|---|
| Acciones de GitHub fijadas por SHA completo y permisos mínimos | Una etiqueta (`v4`) se puede mover a otro código; un SHA no. Incidentes reales: tj-actions (2025) y Trivy (2026) |
| `persist-credentials: false` en cada checkout | El token de GitHub no queda guardado en el disco del runner para pasos posteriores |
| `uv audit` fijado a uv 0.12.20 | Comprueba vulnerabilidades conocidas (OSV) del `uv.lock`. Es un comando experimental de uv: si cambia, se ajusta aquí |
| Comprobación de licencias propia (`scripts/check_licenses.py`) | Regla sencilla y auditable: nada GPL/AGPL/no comercial en lo que se distribuye; copyleft débil solo si está anotado en `docs/licencias.md` |
| Cobertura ≥ 90 % en `detect`, `vault`, `mask`, `guard`, `restore`, `providers` y `api` (`scripts/check_coverage.py`) | Son las piezas de privacidad: un fallo ahí significa datos personales enviados |
| CodeQL solo cuando el repo sea público | En repos privados necesita GitHub Advanced Security (de pago); mientras tanto el job se salta en lugar de fallar |
| Manejador propio de errores 422 | El de FastAPI devuelve el cuerpo recibido, que podría llevar un DNI. El nuestro solo dice qué campo está mal |
| Hooks de Claude Code con tests | Bloquean los casos comunes de leer `.env`, imprimir variables secretas, `--no-verify`, force push y escanear secretos sin `--redact` o sobre el árbol de trabajo (`gitleaks dir`, `--no-git`, `trufflehog filesystem`). Son **defensa en profundidad, no una barrera**: quien ejecuta código arbitrario puede saltárselos, y la revisión de privacidad encontró varios caminos (ver el modelo de amenazas). La barrera real es gitleaks en CI |
| `uv audit` también sobre las herramientas de desarrollo | A propósito: esas herramientas corren en la CI con acceso al código; una vulnerable también es un riesgo |
| Errores 422 sin claves del cliente y con tamaño máximo | En la ubicación del error solo quedan la parte (`body`, `query`…) y los índices; las claves de un diccionario las elige el cliente y podrían ser un DNI |
| `X-Request-ID` propio siempre (issue 20) | Se escribe en logs y cabeceras: el del cliente se ignora, porque hasta un valor corto y sin símbolos puede ser un DNI |

## Decisiones técnicas del issue 21 (CI y cadena de suministro)

| Decisión | Por qué |
|---|---|
| zizmor 1.30.1 en pre-commit (sin conexión), en `make check` y en la CI (con las comprobaciones en línea), perfil `auditor` | Busca fallos de seguridad en los workflows (inyecciones, permisos, acciones sin fijar). El perfil `auditor` también saca los avisos de baja confianza: cualquier aviso rompe la CI |
| `permissions: {}` arriba en cada workflow y permisos por job, cada uno con su comentario | Un job nuevo empieza sin permisos; solo recibe lo que pide y queda explicado por qué |
| `concurrency` por rama; en un PR, un push nuevo cancela la ejecución anterior (en `main` nunca) | No se acumulan ejecuciones viejas y en `main` siempre termina la de cada commit |
| Dependabot con `cooldown` de 7 días en `uv`, `github-actions` y `docker` | Una versión secuestrada suele detectarse y retirarse en pocos días; las actualizaciones de seguridad no esperan |
| `CODEOWNERS` con líneas propias para `pyproject.toml`, `uv.lock`, `.github/` y `Dockerfile` | Son los archivos que cambian qué código se ejecuta en la CI o se distribuye |
| Herramientas de la CI con versión exacta (uv 0.12.20, zizmor 1.30.1, acciones por SHA) | La misma entrada da siempre la misma herramienta; se actualizan a propósito |
| Sin topes superiores (`<3`) en las dependencias de `pyproject.toml`; versiones exactas solo en `uv.lock` | Antifaz también es una librería: un tope impide a quien la instala recibir arreglos de seguridad y crea conflictos con otros paquetes. Lo reproducible es el `uv.lock`. Quitar `python-stdnum<3` no cambió ninguna versión bloqueada. `requires-python` mantiene `<3.13` porque la CI solo prueba 3.12 |

## Telemetría

Antifaz no envía nada a ningún sitio salvo a los proveedores configurados en `.env` (`ANTIFAZ_OPENAI_BASE_URL` y `ANTIFAZ_ANTHROPIC_BASE_URL`). La única conexión de salida de la pasarela es su cliente HTTP hacia esas URL; no hay analítica, ni comprobación de versiones, ni informes de errores. El healthcheck de Docker solo llama a la propia pasarela en `127.0.0.1`. El NER opcional funciona sin red: los procesos del pool ponen Hugging Face en modo offline y con su telemetría apagada (`HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_HUB_DISABLE_TELEMETRY`), y el modelo solo se descarga con `make ner-model`. Si algún día Antifaz envía algo más, será opcional y con la tabla de campos en `SECURITY.md`.

## Demo del README (issue 7, parte 7b)

`docs/images/demo.gif` se genera con `make demo` (`scripts/demo_gif.py`), con salidas reales y datos inventados (`scripts/demo/ticket.txt`: DNI `12345678Z`, email `ana.prueba@example.com`, IBAN `ES9121000418450200051332`):

1. Arranca el proveedor falso de los tests de extremo a extremo (`tests/e2e/fake_upstream.py`) en `127.0.0.1:9000` y Antifaz con uvicorn en `127.0.0.1:8000`, con claves aleatorias que se tiran y desde una carpeta temporal vacía: **nunca lee tu `.env` ni llama a un proveedor real**. Necesita los puertos 8000 y 9000 libres.
2. Ejecuta de verdad los comandos que salen en el GIF: `cat ticket.txt`, `antifaz scan`, `antifaz mask`, `python ask.py` (el SDK oficial de OpenAI con `base_url=http://localhost:8000/v1`) y `python provider_received.py` (lo que guardó el proveedor falso). Se para si un valor llega al proveedor falso, si no vuelve en la respuesta o si la clave aparece en alguna salida.
3. Dibuja la sesión como fotogramas de terminal con Pillow (sin navegador) y guarda el GIF con una paleta fija (unos 100 KB). Solo colorea: los marcadores en ámbar y los valores en azul; el texto es el de la salida real, cortado a 92 columnas como lo haría una terminal.

| Decisión | Por qué |
|---|---|
| Pillow en vez de grabar una página con Playwright | Pillow pesa unos MB y no necesita un navegador (Playwright y Chromium son cientos de MB). El resultado no depende de una grabación de pantalla |
| Pillow fuera de las dependencias del proyecto | `make demo` lo trae solo para esa ejecución (`uv run --with pillow==12.3.0`): no entra en `uv.lock`, ni en la imagen, ni en la CI |
| Uvicorn en local en vez de Docker | Más rápido y sin Docker; la imagen ya se prueba de extremo a extremo con `make e2e` |
| Sin el NER | Es opcional y viene apagado. Por eso el ticket no lleva ningún nombre: sin el NER saldría en claro |

`make demo ARGS=--text` solo imprime la sesión capturada, sin dibujar. La fuente es Consolas en Windows, DejaVu Sans Mono en Linux o Menlo en macOS, así que el GIF cambia un poco según el sistema.

## Limitaciones

- Docker: la imagen solo se ha probado y se publica en `linux/amd64` (en local y en la CI), no en ARM.
- Docker: la imagen se basa en Debian y lleva paquetes de sistema con vulnerabilidades altas que Debian no arregla (13 el 2026-10-01, más una de Python 3.12 arreglada solo en 3.14). La CI solo falla con las que tienen arreglo; el informe completo queda como artefacto. La imagen distroless está prevista antes de la v0.2.
- `POST /antifaz/scan` devuelve posiciones en caracteres Unicode (índices de Python), no en unidades UTF-16 como JavaScript ni en bytes.
- `GET /v1/models` elige el proveedor por la cabecera `anthropic-version`: un cliente de OpenAI que la mande por error recibe la lista de Anthropic.
- El proxy habla OpenAI Chat y Anthropic Messages (con y sin streaming). Hay una prueba real con OpenAI y sus respuestas grabadas (2026-09-30); la prueba real con Claude Code y Anthropic está pendiente (sin crédito de Anthropic), y sus tests de contrato usan respuestas escritas a mano.
- En streaming, los argumentos de las herramientas llegan **de golpe al final de su bloque** (o de la `choice` en OpenAI), no poco a poco: así siempre son JSON válido.
- En streaming, un marcador de esta petición con más de ~60 espacios o tabuladores dentro de `[[ ... ]]` sale sin restaurar (como marcador): es el tope de seguridad de lo retenido. Más de 4 MiB de argumentos de herramientas cortan el stream con `stream_limit_exceeded`; el texto ya restaurado de otras choices se envía antes del error.
- Una llamada a herramienta sin `index` en OpenAI pasa sin tocar (sus argumentos no se pueden unir) y se cuenta como desconocida. En Anthropic, `input_json_delta` solo se restaura en bloques `tool_use`; en otros (herramientas del servidor…) pasa sin tocar y se cuenta.
- Argumentos o `input` que no son JSON válido (cortados, texto suelto): cada valor se inserta escapado como dentro de una cadena JSON (comillas y barras con `\`). Así lo que era JSON sigue siéndolo y un marcador dentro de una cadena no la rompe; en texto suelto el valor sale con esos escapes.
- Si un proveedor manda una clave partida en varios eventos, el stream se corta al completarse, pero el trozo ya enviado llega al cliente (no es la clave entera).
- Un proveedor compatible con OpenAI que omite `choices[].index` (o lo manda como `0.0`, `"0"` o negativo) **corta el stream** con el error fijo de stream mal formado: falla cerrado, porque un SDK uniría ese trozo con otro que la pasarela no puede seguir. Lo mismo en Anthropic con un `index` así o un `content_block_*` sin él.
- Un stream con más de 4.096 rutas de texto distintas (o nombres de campo distintos), por ejemplo con `n` alto y `top_logprobs`, se corta con `stream_limit_exceeded`: la vigilancia de claves partidas no olvida nada para no dejar pasar una.
- El tamaño total de un stream no tiene tope (sí cada línea, cada evento, lo retenido y lo acumulado).
- En Anthropic se bloquean las `citations`, los documentos y las herramientas del servidor (búsqueda web…): fallar cerrado es a propósito.
- Las claves de los objetos JSON no se enmascaran: si contienen un dato, se bloquea la petición.
- El detector no decodifica base64, hexadecimal ni otras codificaciones dentro del texto (solo bloquea las URL `data:...;base64,`).
- Los errores del proveedor se devuelven con los marcadores `[[TIPO_N]]` sin restaurar.
- El tamaño de la respuesta del proveedor no tiene tope en v0.1.
- `Bearer` en la cabecera `Authorization` distingue mayúsculas: `bearer` se rechaza.
- Un objeto con dos claves que solo cambian en mayúsculas se rechaza con 400 si es una clave que Antifaz lee (`content` y `Content`); las demás (`id` e `ID` en un esquema) pasan (ADR-0015).
- Los errores del proveedor en JSON se devuelven tal cual. En sus respuestas solo se detecta la clave **completa**, tal cual o con escapes JSON (`\u0061`). Si la devuelve recortada (por ejemplo `sk-...abcd` en su error de clave incorrecta), en base64, en UTF-16 o de otra forma codificada, llega al cliente (test `xfail` estricto en `tests/redteam/test_models_and_scan.py`).
- La pasarela no se puede usar desde una web pública: no hay CORS.
- En `tools`, `functions`, `response_format` y `tool_choice` (esquemas del desarrollador) no se aplica la lista de tipos permitidos; sus textos sí se enmascaran.
- Los nombres de persona solo se detectan con el NER, que está apagado por defecto: sin él pasan en claro. Con él, cerca del 30 % de las detecciones de nombres tapan texto que no es personal (no cumple el suelo del 85 %; ver "NER con el modelo real").
- NER: una petición tan grande que no cabe en el tiempo máximo, o con más de 1.024 ventanas, se bloquea (el fallo seguro). Un nombre detectado se enmascara en toda la petición, también donde es una palabra corriente ("Mar") o, si tiene 6 o más letras, dentro de otra palabra ("Marina" en "submarina"): falsos positivos aceptados (ADR-0016).
- NER: cada cadena del JSON se lee por separado. Un nombre partido entre dos campos ("Carmen" en uno y "Prueba López" en otro) no lo ve entero ningún trozo, y cada parte puede salir en claro si el modelo no la reconoce sola (test `xfail` estricto en `tests/redteam/test_ner.py`).
- NER: solo se propaga el valor entero que encontró el modelo. Si ve "Carmen Prueba López" en un mensaje y en otro solo aparece "Carmen", ese "Carmen" suelto no se tapa por propagación; solo si el modelo lo detecta allí (test `xfail` estricto). Buscar las partes de un nombre enmascararía palabras corrientes por toda la petición.
- NER: el modelo no lee las URL largas, los bloques base64/hex de 32+ caracteres sin espacios ni las series de 6+ cifras pegadas a letras (vista para el NER): un nombre dentro de una URL (`/usuarios/jordi-inventat`) o de un bloque así no se detecta. Los validadores y patrones sí leen esos trozos (emails, IBAN, DNI…).
- NER: un texto que cuesta muchas más subpalabras que palabras (más de 4 trozos de 384 tokens por ventana de media: CJK o emoji al azar) se bloquea con 400 sin ejecutar el modelo.
- NER: "Apellidos, Nombre" sin contexto ("Inventat Puig, Jordi") no se detecta como nombre (test `xfail` estricto en `tests/integration/test_ner_model.py`).
- NER: entre la comprobación del manifiesto en el proceso del pool y `torch.load` queda un instante (TOCTOU): aprovecharlo exige poder escribir en la carpeta del modelo, que debe ser de solo lectura para el usuario de la pasarela.
- NER: al cargar el tokenizador, transformers avisa de un "incorrect regex pattern" y sugiere `fix_mistral_regex=True`. Es un aviso pensado para tokenizadores de Mistral: se comprobó que la tokenización de mDeBERTa coincide con la de sentencepiece. Los avisos no salen del proceso del pool (está silenciado).
- Dos identificadores pegados sin separador (`12345678Z12345678Z`): el primero no se detecta (un DNI nunca toca cifras) y la guardia bloquea la petición.
