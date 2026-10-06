# ADR-0017 · Instalación y secretos en la CLI

- **Estado:** Aceptada (aprobada por Miquel Moreno el 2026-10-06)
- **Fecha:** 2026-10-06
- **Concreta:** el bloque de la v0.2 "Instalación en un minuto" (issues 41 `init`, 42 imagen, 31 `setup claude-code`, 44 `setup codex`) y la fila "Arranque" del modelo de amenazas

## Contexto

Hoy, instalar Antifaz es clonar el repo, copiar `.env.example` a `.env`, inventarse una clave aleatoria con `openssl`, pegar las claves de los proveedores a mano y construir la imagen. Son muchos pasos y en casi todos se puede filtrar una clave: el historial de la shell (`--openai-key sk-...`), la lista de procesos (`ps` muestra los argumentos), la salida de un CI que se guarda en un log, un `.env` que otro usuario de la máquina puede leer, o una copia de seguridad que pisa a otra.

Queremos que la instalación sea de un minuto **sin** abrir ninguna de esas vías.

## Decisión

**Una sola orden en la imagen.** La imagen pasa a tener `ENTRYPOINT ["antifaz"]` y `CMD ["serve"]`: `antifaz serve` arranca uvicorn con los mismos parámetros de hoy (un test los fija). Así la misma imagen sirve para `docker run … init`, `docker run … verify` y para servir. Se implementa en el issue 42; se decide aquí.

**`antifaz init` no usa la red.** Escribe el `.env` sin hablar con ningún proveedor ni con internet. Comprobar que las claves funcionan es trabajo de `antifaz doctor` (issue 43), y solo si el usuario lo pide.

**La clave de Antifaz se crea en la máquina.** `secrets.token_hex(32)` (64 caracteres hexadecimales, 256 bits). Si por mala suerte no pasara la comprobación de arranque (menos de 8 caracteres distintos), se saca otra. Se muestra **una sola vez** y solo si la salida es un terminal, con un aviso; si la salida va a un archivo o a un log de CI, no se muestra salvo que se pida con `--show-key`. En cualquier caso está en el `.env`.

**Las claves de los proveedores nunca llegan por argumentos.** Tres caminos, y ninguno deja la clave en el historial ni en `ps`:

- interactivo: entrada oculta con `getpass`;
- `--non-interactive` con `--openai-key-env VAR` / `--anthropic-key-env VAR`: se lee la variable `VAR` (el argumento es el **nombre**; si parece una clave, se rechaza sin repetirla);
- `--non-interactive` con `--openai-key-stdin` o `--anthropic-key-stdin`: se lee de stdin (como mucho una de las dos).

Se quitan espacios, comillas, una marca BOM y un `Bearer ` pegado delante. Por stdin se leen como mucho 4 KiB, y `--*-key-stdin` con stdin en un terminal se rechaza (hay que mandar la clave por una tubería o usar el modo interactivo). Si `getpass` no puede ocultar lo que se escribe (`GetPassWarning`), `init` se para en vez de mostrarlo. Los mensajes nunca repiten el nombre dado en `--*-key-env` (algunas claves parecen nombres de variable, como `gsk_…`) ni la ruta de `--path`. Si la clave no puede viajar en una cabecera (`_format_problem` de `config.py`) o tiene caracteres que no se pueden escribir sin comillas (`"`, `'`, `#`, `$`, `\`, espacios), es un error que nombra la variable, nunca el valor. Un prefijo raro (`sk-`, `sk-ant-`) solo da un aviso: puede ser un proveedor compatible o un proxy. Un proveedor sin clave no tiene línea en el `.env` (su ruta da 503).

**Escritura atómica.** El contenido nuevo va a un archivo temporal creado con `os.open(O_CREAT|O_EXCL|O_WRONLY, 0o600)` **en la misma carpeta** (para que `os.replace` sea un renombrado atómico y no una copia entre discos). Se escribe, se hace `fsync`, se valida leyendo **solo ese archivo** con `check_safe_to_start` (las variables de entorno del proceso no cuentan: un entorno bueno no puede salvar un archivo malo, ni uno malo tumbar uno bueno) y entonces `os.replace`. En Windows, `os.replace` se reintenta unas pocas veces si otro programa tiene el archivo abierto (`PermissionError`). Pase lo que pase (también Ctrl-C), el temporal se borra; el `.env` anterior queda igual y el mensaje es genérico, salvo que diga en qué copia está el anterior si ya se había hecho.

**Un `.env` que ya existe no se pisa sin permiso.** En modo interactivo hay que escribir `yes`; sin terminal hace falta `--force`; si no, código 2 y nada cambia. Antes de reemplazarlo se guarda una copia `.env.bak-AAAAMMDD-HHMMSS` creada con `O_EXCL` y `0600`; si ya existe una con ese nombre (dos ejecuciones en el mismo segundo), se añade `-1`, `-2`… **Una copia nunca pisa a otra.** Si `.env` es un enlace o una carpeta, no se toca.

**Formato del archivo.** Una variable por línea, `VAR=valor`, sin comillas, sin comentarios detrás del valor y sin `$`: así lo leen igual pydantic-settings y `docker --env-file` (un test lo compara). La plantilla va dentro del paquete (`antifaz/cli/env_template.py`), porque en la imagen publicada no hay `.env.example`; un test comprueba que tiene las mismas variables, en el mismo orden, que `.env.example`.

**Permisos.** En Linux y macOS, `.env`, el temporal y las copias son `0600` (solo el dueño). En Windows, `0o600` no existe: los archivos heredan los permisos de la carpeta, que en el perfil del usuario ya son solo suyos. Está documentado; no tocamos ACL.

**Dentro de un contenedor.** Con `docker run -it … init`, la salida va a un terminal y la clave de Antifaz se imprime; Docker guarda esa salida en los logs del contenedor (`docker logs`) mientras el contenedor exista. Por eso la orden documentada lleva siempre `--rm` (el contenedor y sus logs desaparecen al terminar). Nota para el issue 42: el archivo lo crea el usuario del contenedor (UID 10001); en Linux el usuario del host puede no poder leerlo, así que la orden documentada lleva `--user "$(id -u):$(id -g)"`.

**Rotar claves.** Las copias `.env.bak-…` guardan las claves antiguas. Después de rotar una clave (revocarla en el proveedor y volver a ejecutar `init`), hay que borrar las copias que la contienen.

**Sin terminal, sin preguntas.** Si se pide el modo interactivo y stdin no es un terminal, código 2 con el mensaje "usa `docker run -it` o `--non-interactive`". Nunca se queda colgado esperando.

**`antifaz setup claude-code` (issue 31) escribe la URL, nunca la clave.** Pone `ANTHROPIC_BASE_URL` en la configuración de Claude Code y, solo si el usuario lo da, un `apiKeyHelper` (un programa que devuelve la clave cuando hace falta). Antes de escribir enseña el cambio (diff) y un hash del archivo; `--uninstall` lo deshace.

**Compose para usuarios: la imagen publicada.** El `docker-compose.yml` del repo usa `ghcr.io/miquel-moreno/antifaz:<versión>`, y la release adjunta uno con la imagen fijada por digest (`curl -LO …/releases/download/vX.Y.Z/docker-compose.yml`). Construir en local pasa a `compose.build.yml` (Makefile, e2e y CI se adaptan). Issue 42.

**Codex espera.** `antifaz setup codex` (issue 44) no se hace hasta que exista la ruta `/v1/responses`: Codex solo habla la Responses API.

## Alternativas descartadas

- **`--openai-key sk-...`**: lo más cómodo, pero la clave queda en el historial de la shell, en `ps` y en los logs de CI. Descartado.
- **Escribir directamente sobre `.env`**: si el proceso muere a mitad, queda un `.env` roto y sin copia. Descartado.
- **Copia de seguridad con un nombre fijo (`.env.bak`)**: la segunda ejecución pisa la primera, que puede ser la única buena. Descartado.
- **Validar con `Settings()` normal**: lee también el entorno del proceso, así que validaría otra cosa que lo que hay en el archivo. Descartado.
- **Comprobar las claves contra el proveedor en `init`**: necesita red, cuesta (poco) y mezcla dos trabajos. Va en `doctor`, opcional.

## Consecuencias

- Se instala con dos órdenes (`antifaz init` y `docker compose up -d`) y ninguna clave pasa por argumentos, historial ni logs.
- La CLI escribe archivos con secretos: es una pieza nueva de la superficie de ataque. Los tests cubren claves canario en stdout, stderr y logs en todos los caminos (también los de error), `getpass`, copias, fallos de `os.replace` y de validación, permisos `0600` (POSIX) y un job de CI en Windows.
- Hay dos plantillas (`.env.example` para quien clona y la del paquete para la imagen); un test impide que se separen.
- En Windows la protección del `.env` depende de los permisos de la carpeta; si alguien ejecuta `init` en una carpeta compartida, los demás pueden leerlo. Documentado.
- Una carrera muy estrecha sigue posible: si otro proceso crea `.env` entre la pregunta y el `os.replace`, `init` se para sin tocarlo, pero un proceso que lo cree justo en el mismo instante del renombrado podría perder su archivo. Es una carpeta del propio usuario; se acepta.
