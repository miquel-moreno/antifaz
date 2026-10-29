# ADR-0021 · PostgreSQL: conexión, esquema y migraciones

- **Estado:** Propuesta
- **Fecha:** 2026-10-29
- **Concreta:** el PR 8b-1 del issue #121 (claves virtuales), ADR-0001 («PostgreSQL 16 con asyncpg para claves y evidencias, a partir de la v0.2») y ADR-0020 (claves virtuales). Decisiones D1–D3, D7–D10 y D12–D14 aprobadas por Miquel el 2026-10-29

## Contexto

Las claves virtuales (ADR-0020) viven en PostgreSQL. Es la primera base de datos de Antifaz, y trae tres riesgos nuevos:

- **Un secreto más** (la contraseña de la base de datos) que puede salir en un mensaje de error, un log o una URL.
- **Un servicio más que puede no responder**: una consulta colgada no puede bloquear la pasarela ni la CLI.
- **Un esquema que cambia con las versiones**: hay que crearlo y actualizarlo sin perder datos ni aplicar dos veces el mismo cambio.

PostgreSQL es **opcional**: sin él, Antifaz funciona como en la v0.1.

## Decisión

### Dependencia: asyncpg, en el núcleo pero cargada solo donde hace falta (D1)

- **asyncpg 0.32.0** (Apache-2.0), dependencia del núcleo y no un extra: la imagen la lleva siempre y no hay que elegir otra variante. Pero **solo `antifaz db` (y desde 8c `antifaz keys`) la cargan**, dentro de su `run()`. La librería de claves, los ajustes, la pasarela, el panel y el resto de la CLI nunca importan `asyncpg` ni `antifaz.db`: lo comprueba un test en un intérprete limpio y otro estático sobre el cierre de imports (con caso negativo).
- asyncpg no trae tipos: mypy la ignora (`ignore_missing_imports`) y `antifaz/db/connect.py` es el único módulo que la importa, detrás de un adaptador tipado (`Session`). Sin `asyncpg-stubs`: una dependencia menos de la que fiarse.

### Ajustes separados, nunca una DSN (D3)

- `ANTIFAZ_DATABASE_HOST` (sin él, PostgreSQL apagado), `_PORT` (1–65535, 5432), `_NAME` (`antifaz`), `_USER` (`antifaz`), `_PASSWORD` (secreto), `_SSLMODE` (`disable` o `verify-full`, por defecto `verify-full`) y `_TIMEOUT_SECONDS` (más de 0 y como mucho 60; 5 por defecto).
- **Nunca una DSN** (`postgresql://usuario:contraseña@host/db`): una URL lleva la contraseña a cualquier sitio donde se imprima. `check_database_settings()` rechaza un host con `/` o `@` (una URL, una DSN o un socket), una contraseña vacía o de ejemplo, y un nombre o usuario vacíos. Sus mensajes **nombran la variable, nunca el valor**, como `check_safe_to_start`. Los ajustes ocultan además el valor en sus errores de validación (`hide_input_in_errors`): una contraseña pegada en `ANTIFAZ_DATABASE_PORT` no sale en el mensaje.
- A asyncpg se le pasan **siempre** host, puerto, usuario, contraseña, base de datos, TLS, negociación TLS, `target_session_attrs` y Kerberos de forma explícita: así nunca lee `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE`, `PGSSLNEGOTIATION`, `~/.pgpass` ni el usuario del sistema. Un test con un servidor falso y todas esas variables apuntando a otro sitio lo comprueba.
- **TLS** (D4): `verify-full` es un `ssl.SSLContext` que comprueba el certificado contra las CA del sistema y el nombre del host, con TLS 1.2 o más. No hay «prefer»: un servidor que no ofrece TLS es `tls_failed`, nunca texto plano. `disable` solo tiene sentido en una red privada o en la misma máquina: `antifaz doctor` y `antifaz db` avisan («PostgreSQL: TLS off») si el host no es de loopback (127.0.0.0/8, ::1, `localhost`). OpenSSL también confía en las CA que señalen `SSL_CERT_FILE` y `SSL_CERT_DIR` (así funciona OpenSSL): quien controla el entorno amplía las CA de `verify-full`. Para una CA propia habrá una variable en 8f.
- `connect()` vuelve a pasar `check_database_settings()` antes de llamar a asyncpg: un llamador que se la salte no puede llegar, por ejemplo, a un socket Unix (`/run/postgresql`, sin TLS diga lo que diga el modo) ni usar una contraseña vacía o de ejemplo.

### Cada sesión, con límites de tiempo

- `timeout=` en **todas** las llamadas a asyncpg (conectar, ejecutar, leer, cerrar). Un test del AST lo exige en el adaptador, que es el único sitio que llama a asyncpg.
- Al conectar, la sesión fija `statement_timeout`, `lock_timeout` e `idle_in_transaction_session_timeout` al mismo tiempo, `application_name=antifaz` y `log_parameter_max_length_on_error=0`. PostgreSQL 16 deja a cualquier rol cambiar este último para su propia sesión (`pg_settings.context = 'user'`, comprobado en un test con PostgreSQL real).
- **Qué quita y qué no** (revisión del 2026-10-29, reproducido con PostgreSQL 16.15): ese ajuste solo quita los argumentos `$n` de la línea de la sentencia que falla en el log del servidor. **No** quita la línea `DETAIL` de un `CHECK` o un `UNIQUE` que falla, que escribe la fila: `Failing row contains (…)` o `Key (pepper_version, key_hash)=(…) already exists`, con el nombre o el hash. Solo `log_error_verbosity = terse` en `postgresql.conf` lo evita, y solo lo cambia un superusuario: se recomienda al administrador. En 8b-1 nada escribe claves todavía; el almacén de 8b-2 validará cada valor en Python antes de escribirlo, así que una fila que la tabla rechace es un fallo de Antifaz, no un camino normal.
- Las consultas son constantes del módulo escritas como `Sql("...")`; los valores viajan como argumentos (`$1`…). Nunca f-strings, `%`, `.format()` ni concatenaciones: un test del AST lo comprueba, con casos negativos. Dos excepciones con nombre: el adaptador reenvía su parámetro `query` y el ejecutor de migraciones ejecuta el texto de un archivo que va dentro del paquete.

### Errores cerrados (los mensajes de asyncpg nunca salen)

- `check_database()` responde «lista» o un motivo de una lista cerrada: `unreachable`, `timeout`, `auth_failed`, `tls_failed`, `too_old`, `not_utf8`, `not_migrated`, `behind`, `ahead`, `migration_changed` u `other`, cada uno con un **texto fijo** que nombra variables y órdenes.
- El mensaje de asyncpg o del servidor puede llevar el usuario, la base de datos, el host, palabras del servidor o los valores de una sentencia: **nunca se muestra, ni se registra, ni se encadena**. El error original se clasifica, se borran las variables de los marcos de su traceback (la contraseña está en los de asyncpg) y el `DatabaseError` se lanza **fuera** del bloque `except`, sin `__cause__` ni `__context__`. Tests con servidores falsos en 127.0.0.1 (puerto cerrado, servidor mudo, servidor que habla HTTP, que pide un método de autenticación inexistente, que rechaza la contraseña o el TLS) y una contraseña canario que no aparece en `str`, `repr`, `args`, la causa, el contexto, el traceback formateado, las variables de sus marcos ni ningún log a nivel DEBUG (el de `asyncpg` incluido).
- **PostgreSQL 16 o más nuevo** (D13) y base de datos en **UTF8**: si no, `too_old` o `not_utf8`, antes de crear nada.

### Migraciones propias, sin Alembic (D2)

- Archivos `src/antifaz/db/migrations/NNNN_nombre.sql`, dentro de la rueda, numerados desde 0001 sin huecos, en UTF-8 con saltos de línea LF (una copia con CRLF cambiaría la suma). `SCHEMA_VERSION` es el número del último; un test los ata.
- Tabla `antifaz_schema_version (version integer PRIMARY KEY, checksum bytea NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())`.
- `antifaz db migrate`: comprueba versión y codificación, toma `pg_advisory_lock` con una constante (dos `migrate` a la vez aplican cada archivo una sola vez: el segundo espera como mucho `ANTIFAZ_DATABASE_TIMEOUT_SECONDS`, que es su `lock_timeout`; si el primero sigue, sale con `timeout` y, al repetirlo, no encuentra nada que hacer), crea la tabla de versiones si falta y compara lo aplicado con los archivos. **Solo hacia delante**: una base de datos más nueva (`ahead`) o con un archivo aplicado que ha cambiado o falta (`migration_changed`) se rechaza sin tocar nada. Cada archivo pendiente va en **su propia transacción** junto con su fila (versión y SHA-256); si falla, se deshace ese archivo y se para. Ese mismo `ANTIFAZ_DATABASE_TIMEOUT_SECONDS` es el `statement_timeout` de cada migración: una migración pesada futura necesitará subirlo para esa ejecución. Las versiones aplicadas se leen con `LIMIT` (una más que los archivos conocidos, que ya basta para saber que es `ahead`); más filas de las pedidas es un error cerrado.
- **Permisos del rol**: tiene que ser dueño de la base de datos o tener `CREATE` en el esquema donde se crean las tablas (desde PostgreSQL 15, en `public` solo puede crear el dueño de la base de datos).
- **Sin esquema en los nombres**: `virtual_keys` y `antifaz_schema_version` van sin esquema y dependen del `search_path` del rol (el primer esquema donde pueda crear). Riesgo bajo en PostgreSQL 16 si nadie más puede crear en ese esquema; las funciones de los disparadores fijan su propio `search_path`. No se cualifican los nombres ni se usa un esquema propio en 8b-1: queda documentado.
- Un archivo publicado **no se edita nunca**: un test fija su suma. Un cambio es un archivo nuevo.
- **`serve` nunca migra** (D7): el administrador ejecuta `antifaz db migrate` a propósito. `antifaz db status` dice la versión del esquema y cuántas claves hay activas, revocadas y caducadas (solo con SQL; las filas que no pasan las comprobaciones de Python se cuentan en 8b-2). Códigos de salida como `doctor`: 0 bien, 1 problema de la base de datos, 2 uso o configuración. `antifaz doctor` solo dice si PostgreSQL está configurado (D8), sin conectarse ni cargar asyncpg.

### El esquema 0001: la tabla `virtual_keys` (D12, D14)

- `id bigint GENERATED ALWAYS AS IDENTITY`: el número de la clave no se elige a mano ni se reutiliza.
- Los `CHECK` repiten las reglas de `keys/records.py` hasta donde llega SQL: nombre de 1 a 64 caracteres, sin controles C0 y C1, sin separadores de línea y párrafo, sin las marcas invisibles más comunes (anchura cero, dirección del texto, U+00AD, U+034F, U+061C, U+FEFF, U+2800) y sin espacios en blanco al principio ni al final (todos los que quita `str.strip()` de Python; los que son controles ya se rechazan en cualquier sitio); de 1 a 4 scopes de la lista, sin repetir ni nulos; tiempos finitos; caducidad después de la creación; revocación no antes de la creación; hash de 32 bytes; versión del pepper ≥ 1; límites ≥ 1; `UNIQUE (pepper_version, key_hash)`.
- **Hueco documentado**: las reglas Unicode completas del nombre (todo `Default_Ignorable_Code_Point` y las categorías Cf, Cs, Co y Cn) se quedan en Python. SQL deja entrar, por ejemplo, un carácter de uso privado o una etiqueta de idioma; un test lo enseña. El almacén de PostgreSQL (8b-2) comprobará cada nombre con `check_name` antes de escribirlo.
- **Disparadores**: `BEFORE DELETE` (por fila) y `BEFORE TRUNCATE` (por sentencia) lanzan un error: las claves nunca se borran, solo se revocan. `BEFORE UPDATE` rechaza cambiar el número, el hash, la versión del pepper o la creación, y quitar o cambiar una revocación ya puesta.
- **Topes comunes** (D14): los límites caben en un `integer` y la versión del pepper en un `smallint`. `keys/records.py` define `MAX_LIMIT = 2 147 483 647` y `MAX_PEPPER_VERSION = 32 767`; `KeyRecord`, `MemoryKeyStore` y `PepperRing` rechazan lo que pase de ahí, así los dos almacenes rechazan lo mismo.
- **Límite honesto hasta el ítem 9**: con un solo rol (ADR-0020), ese rol es el dueño de la tabla. Puede `ALTER TABLE virtual_keys DISABLE TRIGGER …` y después borrar; `DROP TABLE` no dispara ningún disparador; y `name`, `scopes`, `expires_at` y los límites siguen editables (se pueden ampliar los scopes o la caducidad de una clave). Los disparadores paran errores y el uso normal de la aplicación, no a quien tiene ese rol ni a quien administra la base de datos. La separación de roles llega con el ítem 9.

### CI con PostgreSQL de verdad (D9)

- El job `quality` de la CI tiene un servicio `postgres:16` **fijado por digest**, publicado solo en `127.0.0.1`, con `scram-sha-256` (nunca `trust`) y comprobación de salud con `pg_isready`. Su contraseña es de usar y tirar y solo existe en ese job.
- Los tests marcados `postgres` solo corren con `--postgres`: sin la opción se saltan; **con ella y sin base de datos, fallan**. Leen el servidor de variables `TEST_POSTGRES_*` (nunca `ANTIFAZ_*`). Cada sesión crea una base de datos `antifaz_test_<aleatorio>` y un rol sin superusuario con contraseña aleatoria; cada test, su propio esquema. Una guarda rechaza cualquier base de datos cuyo nombre no empiece por `antifaz_test`.
- `make test-postgres` corre los mismos tests con la misma imagen (leída de `ci.yml`) en Docker, en 127.0.0.1 y un puerto libre. `make test` sin PostgreSQL salta también la regla del 90 % de cobertura de `db`; la CI no.
- Dependabot no actualiza las imágenes de `services:`: el digest se renueva a mano hasta que 8f añada el ecosistema `docker-compose` y un test que ate los dos digests.

## Alternativas descartadas

- **Alembic**: trae SQLAlchemy y un motor de migraciones entero para una tabla y pocas migraciones, y sus migraciones son código Python que se ejecuta. Unos archivos SQL numerados con su suma son fáciles de revisar y de auditar. Descartado por ahora.
- **psycopg 3**: válido, pero ADR-0001 ya eligió asyncpg y la puerta es ASGI. Descartado.
- **Una DSN** (`ANTIFAZ_DATABASE_URL`): ver arriba. Descartado.
- **asyncpg como extra opcional**: una imagen más o una instalación con dos variantes, para ahorrar unos 14 MB. Descartado (D1); el test de imports mantiene el resto libre de ella.
- **Migrar al arrancar `serve`**: varios procesos o réplicas migrarían a la vez, y un cambio de esquema pasaría sin que nadie lo decidiera. Descartado (D7).
- **`sslmode=prefer` o `require`**: `prefer` cae a texto plano sin avisar y `require` no comprueba el certificado. Descartado.
- **Borrar claves con `ON DELETE`**: las claves solo se revocan (ADR-0020). Descartado.

## Consecuencias

- La imagen lleva asyncpg aunque no se use PostgreSQL: 14,4 MB en disco tal como viene la rueda, unos 19 MB en la medida de Docker. Sin las secciones de depuración (nota del 2026-10-29 en ADR-0019) quedan 2,6 MB, y la imagen entera mide 127,2 MB. `make e2e` comprueba que no pasa de 140 MB y que todos sus módulos importan.
- Un secreto más: `ANTIFAZ_DATABASE_PASSWORD`. Sin `.env.example` ni plantilla de `init` todavía (8f).
- La CI depende de un servicio PostgreSQL: si no arranca, el job falla (no se salta).
- `serve` no comprueba todavía los ajustes de la base de datos ni se conecta: llega con 8d. El almacén de PostgreSQL, `KeyStoreUnavailable`, el motivo `invalid` y la batería común de contrato llegan con 8b-2.
- Para 8f: un servidor con certificado propio (no firmado por una CA del sistema) necesitará una variable para su CA; hoy `verify-full` solo confía en las del sistema.
