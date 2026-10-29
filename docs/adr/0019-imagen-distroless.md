# ADR-0019 · La imagen de ejecución pasa a distroless

- **Estado:** Aceptada (aprobada por Miquel Moreno el 2026-10-16)
- **Fecha:** 2026-10-16
- **Concreta:** el issue 54 (PR B2) y la fila "Imagen" del modelo de amenazas; completa ADR-0017 (una sola orden en la imagen)

## Contexto

Hasta ahora la imagen final se basaba en `python:3.12-slim` (Debian 13). Esa base trae una distribución casi completa: `sh`, `bash`, coreutils, `apt`, `dpkg`, `perl`, util-linux, ncurses… La pasarela no usa ninguno, pero:

- quien consiguiera ejecutar código dentro tendría una shell y un gestor de paquetes a mano;
- cada paquete es una fuente más de avisos de vulnerabilidades. El 2026-10-16, con la misma base de datos de Grype, la imagen slim tenía 87 paquetes de Debian y 14 CVE altas distintas (todas sin arreglo en Debian);
- la etapa final necesitaba `apt-get upgrade` para recoger parches, y borrar pip dejaba sus bytes en la capa de la base.

Las imágenes distroless de Google traen solo lo necesario para ejecutar un programa. Hay un problema: `gcr.io/distroless/python3-debian13` trae el Python de Debian 13 (3.13), y el proyecto fija 3.12.

## Decisión

**Base final `gcr.io/distroless/cc-debian13:nonroot`**, fijada por digest de índice. `cc` trae glibc, las librerías de GCC (libgcc, libstdc++, libgomp), OpenSSL, zlib, zstd, certificados raíz y tzdata, cada paquete con su registro en `/var/lib/dpkg/status.d/`. No trae shell, ni `apt`, ni `dpkg`, ni coreutils.

**CPython 3.12 copiado de la imagen oficial**, de la misma `python:3.12-slim-trixie@sha256:…` que construye el virtualenv. Una etapa `python` lo recorta y la final copia solo `/usr/local/bin/python3.12`, `/usr/local/lib/libpython3.12.so.1.0`, `/usr/local/lib/python3.12` y `/app/.venv`. El virtualenv ya apunta ahí (`home = /usr/local/bin`) y el binario encuentra `libpython` en `../lib`. Las dos bases son Debian 13: CPython se compiló contra la glibc de trixie y corre sobre la glibc de trixie. Un test unitario exige `-trixie` en la de Python y `-debian13` en la final, y un test de `make e2e` exige que `/etc/debian_version` empiece por `13`.

**Ningún `RUN` en la etapa final**: no hay shell para ejecutarlo. Los parches de Debian llegan con un digest nuevo de la base (Dependabot), no con `apt-get`.

**Se quitan los módulos de la biblioteca estándar cuya librería de sistema no está en `cc`** (`_ctypes`/libffi, `_sqlite3`, `_lzma`, `_bz2`, `readline`, `_curses`, `_curses_panel`, `_dbm`, `_gdbm`, `_uuid`, `_crypt`, `_tkinter`), con los paquetes que solo los envuelven (`ctypes`, `sqlite3`, `curses`), además de pip, setuptools, wheel, `ensurepip`, IDLE, tkinter/turtle y los módulos de pruebas de CPython. Condición de Miquel: solo si ningún paquete instalado los necesita. La comprueba un test de `make e2e` que importa cada módulo de cada distribución del virtualenv (dependencias incluidas, desde `importlib.metadata`) y de `antifaz` con todos sus subpaquetes. Solo admite fallos por paquetes opcionales que no están instalados (trio, h2, gunicorn…) y por módulos solo de Windows (`click._winconsole`, que usaría `ctypes`). El 2026-10-16 importó 953 módulos sin ningún otro fallo. Si un día una dependencia necesita uno de ellos, ese test falla, y el módulo vuelve con su librería **y con el registro de dpkg** de esa librería.

**Los escáneres no se quedan ciegos.** Cada librería compartida de la imagen está en un `.md5sums` de `status.d`, es parte de CPython (Syft lo identifica por el binario) o está en el `RECORD` de un paquete del virtualenv: lo comprueba `make e2e`. La CI comprueba además, después del informe completo de Grype (ahora también en JSON), con `scripts/check_scan_sees.py` y la regla de Miquel: **la estructura bloquea, la CVE solo avisa**. Bloquea (falla el job) si Grype no ve Debian 13 o no casa ningún paquete `deb`, o si el SBOM no lista `libc6`, `libssl3t64` y `zlib1g` como paquetes de Debian o no lista CPython 3.12.x. Que falte CVE-2026-85091 en `zlib1g` solo da un aviso (`::warning::`), porque Debian puede arreglarla o reclasificarla. Si se perdieran los registros de `status.d`, los escáneres verían menos y el filtro pasaría por el motivo equivocado: `scripts/check_scan_negative.py` (a mano, con Docker) lo demuestra con una copia de la imagen sin `/var/lib/dpkg`, donde la parte del SBOM falla (el 2026-10-16: faltan `libc6`, `libssl3t64` y `zlib1g`) y en la imagen real pasa.

**Mismo usuario `10001:10001`** (decisión de Miquel), no el `65532` de distroless: no cambian los volúmenes, el `securityContext` ni el `--user` documentado. No tiene entrada en `/etc/passwd`: nada de la pasarela la necesita.

**Lo demás se queda igual**: `ENV`, `LABEL`, `HEALTHCHECK` en forma exec con `python -m antifaz.healthcheck`, `ENTRYPOINT ["antifaz"]`, `CMD ["serve"]` y el sistema de archivos de solo lectura. Se añade `LANG=C.UTF-8`, que antes venía de la imagen oficial.

**El NER**: el extra `ner` no está en la imagen (un test lo prohíbe), así que el modelo real no se puede cargar en ella. `make e2e` comprueba que todos los módulos del NER de `antifaz` importan, que el pool de procesos (spawn, tuberías, JSON) funciona con el backend falso y que, con `ANTIFAZ_NER_ENABLED=true` y sin modelo, `serve` se para con el mensaje fijo de ADR-0016, sin `ImportError`. La futura etiqueta `-ner` necesitará `ctypes` y libffi (torch carga sus librerías con `ctypes`): tendrá que traerlos con su registro de dpkg.

**Depurar sin shell**: un contenedor `busybox` temporal, fijado por digest y pegado al de Antifaz (`--pid` y `--network` de ese contenedor). Primero sin ninguna capacidad (`--cap-drop ALL`, `no-new-privileges`, `--read-only`), que basta para `ps` y `wget`. Solo para mirar archivos (`/proc/1/root`), con `SYS_PTRACE` como única capacidad, y avisando de que esa capacidad es la que deja leer el entorno (las claves) y la memoria del proceso. Como alternativa, `docker debug`, que es una función de Docker Desktop de pago (Pro, Team o Business). Está en la guía de instalación, y el límite (quien tiene Docker en el servidor puede leer las claves igualmente) está en el [modelo de amenazas](../security/threat-model.md), fila «Imagen y contenedor».

## Alternativas descartadas

- **`gcr.io/distroless/python3-debian13`**: trae el Python de Debian (3.13), no 3.12. Pasar a 3.13 es otra decisión, con sus propias pruebas. Descartado por ahora.
- **Seguir con `python:3.12-slim` y borrar la shell y apt en un `RUN`**: los bytes siguen en la capa de la base, los escáneres siguen viendo los paquetes y es frágil. Descartado.
- **Chainguard/Wolfi u otras bases mínimas**: Wolfi también usa glibc, pero no encontramos una forma gratuita de fijar Python 3.12 (la imagen gratuita solo ofrece `latest`, Python 3.15.0 medido el 2026-10-16). Y sería otra distribución, con otro feed de vulnerabilidades y otra cadena de confianza que vigilar. Descartado.
- **Copiar libffi, sqlite, liblzma… para no quitar módulos**: más paquetes y más avisos para módulos que nadie usa, y cada librería necesitaría su registro de dpkg. Descartado mientras ningún paquete los necesite.
- **Usuario `nonroot` (65532) de la base**: cambiaría los permisos documentados y los de quien ya monta volúmenes. Descartado (Miquel).

## Consecuencias

- Imagen más pequeña: 143,5 MB frente a 249,1 MB (`docker image inspect`, 2026-10-16). Menos paquetes y avisos: 14 paquetes de Debian y 5 CVE altas distintas, frente a 87 paquetes y 14 CVE altas (Syft v1.51.1 y Grype v0.118.0 en local, misma base de datos).
- **CVE-2026-85091 (zlib1g) sigue**: CPython enlaza libz de forma dinámica, y Debian no tiene arreglo. Solo desaparecerá con un digest nuevo de distroless que lo traiga. `check_scan_sees` lo vigila, pero solo avisa: cuando desaparezca, la CI sigue en verde con un aviso, y conviene elegir otro canario.
- Sin `apt-get upgrade`, un parche de Debian publicado después de la base no entra hasta que Dependabot proponga un digest nuevo. Si es alto o crítico con arreglo, el filtro de Grype parará el merge hasta actualizarlo. Si Debian lo arregla y distroless tarda en reconstruir su imagen, la única salida es una excepción con motivo y fecha en `.github/grype-gate.yaml`, que se quita en cuanto llegue el digest nuevo.
- Dos bases que actualizar (python y distroless), en PR separados de Dependabot. El test de "mismo Debian" falla si una cambia de versión de Debian y la otra no. Dependabot renueva el digest de una etiqueta que no es versión (`nonroot`), también fuera de Docker Hub, pero ahí no aplica el `cooldown`, porque no conoce la fecha de publicación. La documentación de Dependabot no lo explica; se deduce del código de su comprobador de imágenes ([`update_checker.rb` de dependabot-core](https://github.com/dependabot/dependabot-core/blob/main/docker/lib/dependabot/docker/update_checker.rb)): con una etiqueta que no es comparable (`return version_tag unless version_tag.comparable?`) mantiene la etiqueta y pide su digest actual al registro (`digest_of`), con el registro que diga la propia línea `FROM` y credenciales opcionales, y solo consulta fechas de publicación en Docker Hub. Hay que confirmarlo en el log del primer job de Dependabot después del merge.
- `docker compose exec antifaz sh` ya no funciona; `antifaz doctor` y `antifaz verify` por `exec` sí.
- Sin `ctypes`, `sqlite3`, `lzma`, `bz2`, `readline`, `curses` ni el `_uuid` compilado. Una dependencia futura que los necesite hace fallar `make e2e` antes del merge.

## Nota del 2026-10-29 (PR 8b-1): sin secciones de depuración en el virtualenv, y límite de 140 MB

Con asyncpg (ADR-0021) la imagen pasó de 145,4 MB (`main` en 99d0225, medida el 2026-10-29) a **164,4 MB** y superó el límite de 160 MB de `make e2e`. asyncpg solo ocupa 14,4 MB en disco, pero Docker con containerd cuenta cada capa comprimida y descomprimida, así que suma unos 19 MB. Casi todo son secciones de depuración (DWARF) que traen las ruedas compiladas y que la pasarela nunca usa. Decisión de Miquel:

- **La etapa `build` instala `binutils` y ejecuta `strip --strip-debug` sobre todos los `.so` del virtualenv**, después del último `uv sync`. `binutils` va fijado a una versión exacta de Debian 13 (`binutils=2.44-3`, comprobada el 2026-10-29 con `apt-cache policy` en la misma imagen base fijada) y **no llega a la imagen final**: la etapa final solo copia `/app/.venv` de esa etapa, y sigue sin ningún `RUN`. Si una actualización de Debian retira esa versión, la construcción falla y hay que actualizar el pin (el `Dockerfile` lo explica).
- **Solo `--strip-debug`**, nunca `--strip-all` ni `--strip-unneeded`: se quitan las secciones `.debug_*` y nada más; el código, la tabla de símbolos, los símbolos dinámicos y las notas (`.note.gnu.build-id`) se quedan. Lo comprueban dos tests. Uno, en `tests/unit/test_container_files.py`, revisa el `Dockerfile`: solo `--strip-debug`, después del último `uv sync`, `binutils=versión` y nada de `binutils` en la etapa final. El otro, en `make e2e`, lee las cabeceras ELF de cada `.so` dentro de la imagen con Python puro, porque la imagen no tiene `binutils`, y no admite ninguna sección `.debug_*` ni `.zdebug_*`. Todos los módulos de `antifaz` y de cada paquete instalado siguen importando, y los 51 tests de `make e2e`, que sirven con uvloop, pasan.
- **Ahorro medido el 2026-10-29**:
  - Los 11 `.so` pasan de 39,6 MB a 10,1 MB en disco, **29,5 MB menos**. Por paquete: uvloop 13,9, asyncpg 11,8, yaml 2,3, httptools 1,5; markupsafe, websockets y watchfiles casi nada; pydantic_core ya venía sin depuración.
  - La imagen pasa de 164,4 MB a **127,2 MB** (`docker image inspect`).
  - CPython y las librerías de distroless ya venían sin depuración y no se tocan.
- **Las huellas de `RECORD` ya no coinciden** con esos 11 archivos: el `RECORD` de cada paquete guarda el SHA-256 de la rueda, y el archivo ahora es otro. Antes del cambio coincidían los 11; después, ninguno. Nada lo comprueba al ejecutar: `pip` no está en la imagen y Python no mira `RECORD` al importar. Quien verifique la instalación con `RECORD` verá esos 11 archivos como modificados, y es lo esperado.
- **No cambia lo que ven Syft ni Grype.** Comprobado, no supuesto:
  - Lo que lee Syft para listar paquetes de Python es `METADATA`, `RECORD` (su columna de huella se copia como texto, sin calcularla sobre el archivo), `top_level.txt` y `direct_url.json`. Se ve en `parse_wheel_egg.go` y `parse_wheel_egg_record.go` de Syft v1.51.1. Para los ELF, su catalogador solo lee la sección `.note.package` (`elf_package_cataloger.go`).
  - Comparadas las dos imágenes, la de antes y la de después del `strip`, todo lo que leen esos catalogadores es idéntico byte a byte:
    - los 206 archivos de `.dist-info` (29 `METADATA`), salvo el `RECORD` y el `uv_cache.json` del propio `antifaz`, que cambian con su código y con cada construcción, no con el `strip`;
    - los 28 registros de `/var/lib/dpkg/status.d`;
    - `python3.12` y `libpython3.12.so.1.0`.
  - Ningún `.so` tenía ni tiene `.note.package`, y las únicas secciones que desaparecen son `.debug_abbrev`, `.debug_aranges`, `.debug_info`, `.debug_line`, `.debug_loc`, `.debug_ranges` y `.debug_str`.
  - Grype «compara los componentes de software que encuentra con una base de datos de vulnerabilidades conocidas» (su documentación), y esos componentes son los del catálogo de Syft, que no cambia.
  - No se ha podido ejecutar Syft en local porque no estaba descargado. La CI genera el SBOM y el informe de Grype en cada ejecución, y `scripts/check_scan_sees.py` sigue exigiendo `libc6`, `libssl3t64`, `zlib1g` y CPython 3.12.
- **El límite de `make e2e` baja de 160 a 140 MB**: deja margen para crecer un poco, pero un salto como una dependencia pesada nueva se detecta antes del merge.

Esta ficha sigue **Aceptada**; la nota no cambia sus decisiones, añade una.
