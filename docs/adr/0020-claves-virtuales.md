# ADR-0020 · Claves virtuales

- **Estado:** Propuesta
- **Fecha:** 2026-10-28
- **Concreta:** el ítem 8 de la v0.2 (issue #121), la fila «Claves de Antifaz» del modelo de amenazas y la invariante 13 (ninguna clave en logs ni respuestas). Se enmendará con el ítem 9 (evidencias y separación de roles de la base de datos)

## Contexto

Hasta ahora la pasarela tiene **una sola clave**, `ANTIFAZ_API_KEY`, igual para todos sus clientes. No se puede saber qué aplicación llama, ni limitar a una, ni quitarle el acceso sin cambiar la clave de todas. El ítem 8 da a cada cliente o aplicación **su propia clave**, con nombre, permisos (scopes), límites y caducidad, que se puede revocar en cualquier momento.

Una tabla de claves es un objetivo para un atacante: quien copie la base de datos o una copia de seguridad no debe poder usar ninguna clave, y una clave mal escrita o inventada no debe costar nada a la pasarela.

Los PR del issue: 8a (esta librería, sin conectar a nada), 8b (PostgreSQL), 8c (`antifaz keys`), 8d (la puerta, con enmienda de ADR-0015), 8e (límites) y 8f (compose y prueba de extremo a extremo).

## Decisión

### Formato: `afz_` + 43 caracteres + 6 de control

- Una clave virtual es `afz_` seguido de **43 caracteres base62** (cifras, mayúsculas y minúsculas ASCII) sacados uno a uno con `secrets.choice` (unos 256 bits, sin sesgo) y **6 caracteres base62 de control**. Siempre 53 caracteres.
- El control es el **CRC32** de `afz_` y el cuerpo, escrito en base62 con ancho fijo (62⁶ > 2³²). **No es seguridad**: cualquiera puede calcularlo. Sirve para rechazar una clave mal copiada o inventada **sin buscarla en la base de datos**. El CRC32 detecta cualquier cambio de un carácter (un test de propiedad lo comprueba).
- El prefijo `afz_` deja reconocer la clave a simple vista y a los escáneres de secretos.
- La lectura (`parse_key`) comprueba, en este orden y en tiempo lineal, sin expresiones regulares: que es texto, la longitud, el prefijo, el alfabeto y el control. Rechaza espacios alrededor, caracteres no ASCII y parecidos (una `Ａ` de ancho completo). El error es siempre el mismo (`malformed key`) y no lleva el texto.

### Guardar un HMAC, nunca la clave

- Se guarda **HMAC-SHA256(pepper, clave)**. El pepper es un secreto que vive **fuera** de la base de datos (`ANTIFAZ_KEY_PEPPER` desde 8c, generado por `antifaz init`; nada escribe el `.env` por el administrador). Con solo una copia de la base de datos no se puede comprobar ninguna clave.
- Cada hash guarda la **versión del pepper** con que se calculó (un entero; una columna desde 8b). Así el pepper se puede rotar sin invalidar todas las claves: las nuevas usan la versión actual y las antiguas siguen funcionando mientras su versión siga configurada. Quitar una versión deja sus claves como desconocidas.
- El pepper tiene al menos **32 bytes**; dos versiones no pueden tener el mismo. Ni el pepper, ni la clave, ni su hash, ni el nombre de la clave salen en un `repr`, un `str`, un error o un log; el anillo de peppers, el almacén y la clave recién creada no se pueden serializar ni copiar. La clave en claro (`CreatedKey`) y el hash del registro no son campos de dataclass: `dataclasses.asdict`, `astuple`, `replace` o un volcado de pydantic no llegan a ellos.
- Un hash guardado **nunca es una credencial**: comprobar una clave empieza siempre por la clave en claro. La búsqueda va por un índice de (versión, hash), como hará el índice de PostgreSQL, y el hash encontrado se confirma con `hmac.compare_digest`; ningún objeto de la librería se compara con `==` por la clave ni por el hash (la clave recién creada se compara por identidad y el registro deja el hash fuera de su igualdad).

**¿Por qué no argon2 o bcrypt?** Están pensados para contraseñas, que tienen poca entropía: un hash lento frena a quien prueba millones. Una clave virtual tiene 256 bits aleatorios: no hay nada que adivinar, y un HMAC con un pepper secreto basta. Un hash lento, en cambio, haría que **cualquiera** pueda obligar a la pasarela a gastar CPU en cada petición con claves inventadas (el control ya filtra las mal formadas, pero una bien formada inventada se calcula siempre).

### La clave en claro se ve una vez y no se guarda

- `create()` devuelve la clave **una sola vez**, con su registro. El almacén solo guarda el registro.
- Nada guarda la clave en claro después de `create()` o `verify()`: ni el almacén, ni el registro, ni el objeto «quién llama» (`Caller`, con solo el número y los scopes), ni una excepción. Si una llamada falla a medias, se borran las variables de los marcos de la librería en el traceback; lo mismo con un nombre rechazado y con el hash de un registro que no se ha podido construir.
- El registro de una clave: **número fijo** (no se reutiliza), nombre, scopes, creación, caducidad y revocación (en UTC), hash, versión del pepper y límites por minuto y por día. **Las claves nunca se borran**, solo se revocan: el número sigue sirviendo para las evidencias del ítem 9.
- **El nombre es solo una etiqueta** para el administrador. La clave se identifica por su **número** (`key=N`): es lo que muestran los logs y lo que usa `revoke`. Los nombres no tienen por qué ser únicos.
- **Reglas del nombre**: de 1 a 64 caracteres (la longitud se comprueba antes de mirar los caracteres), imprimibles, sin espacios en blanco al principio ni al final y sin ningún carácter de control ni invisible en ninguna parte: las categorías Unicode Cc, Cf, Zl, Zp, Cs, Co y Cn; todo `Default_Ignorable_Code_Point` (una tabla fija en el código, copiada de `DerivedCoreProperties.txt` de Unicode 15.0.0, la versión de Python 3.12: 4174 puntos de código, entre ellos los controles de dirección del texto U+202A–U+202E y U+2066–U+2069, U+200E, U+200F, U+061C, los de ancho cero U+200B–U+200D, U+2060 y U+FEFF, y los selectores de variación), y U+2800 (BRAILLE PATTERN BLANK). Así la etiqueta enseña lo que lleva. Un test falla si cambia la versión de Unicode de Python, para revisar la tabla. El error no repite el nombre.

### Scopes, caducidad y respuestas

- Scopes por grupo de rutas: chat de OpenAI (`/v1/chat/completions`), mensajes de Anthropic (`/v1/messages` y `/v1/messages/count_tokens`), modelos (`/v1/models`) y `scan` (`/antifaz/scan`). La puerta los aplica en 8d.
- **Caducidad por defecto: 90 días.** Sin caducidad solo si se pide explícitamente (`--no-expiry` en 8c). Una clave vale mientras `ahora < caduca`; en el instante exacto de la caducidad ya no vale.
- `verify()` lee el reloj **antes** de buscar la clave (un reloj roto falla igual para cualquier clave) y una clave revocada se rechaza **siempre**, diga lo que diga el reloj (también si el reloj va hacia atrás o si un registro trae una revocación «en el futuro»).
- `verify()` nunca lanza un error por lo que recibe: responde «quién llama» o un motivo de una lista cerrada: `malformed`, `unknown`, `revoked` o `expired`. **Toda clave rechazada recibe el mismo 401** (no se dice si existe, está revocada o caducada); sin el scope de la ruta, 403 (se aplica en el 8d); por encima del límite, 429 con `Retry-After`. El motivo solo va al log, con el número de la clave (`key=12 reason=revoked`), nunca la clave, su hash ni su nombre.
- `ANTIFAZ_API_KEY` se sigue aceptando, sin límites: es la entrada del administrador si la base de datos no responde.

### Un almacén asíncrono

- El protocolo `KeyStore` y el almacén en memoria son **asíncronos** (`async def create/get/list/revoke/verify`). ADR-0001 eligió asyncpg para PostgreSQL y la puerta es ASGI: una búsqueda en la base de datos no puede bloquear el bucle de eventos mientras espera. Si el protocolo fuera síncrono, el almacén de PostgreSQL tendría que usar un controlador bloqueante o un hilo por llamada.
- Lo que no espera nada se queda síncrono: el formato, el HMAC con el pepper y las comprobaciones del registro.

### PostgreSQL opcional y límites contados en PostgreSQL

- **PostgreSQL es opcional**: sin él, Antifaz funciona como en la v0.1. Solo `antifaz keys` y `antifaz db` cargan el código de la base de datos. Con la base de datos caída, las claves virtuales reciben 503 (se aplica en el 8d) y **nunca pasan sin límites**.
- Los límites se cuentan **en PostgreSQL**, de forma atómica, y no en el Valkey que menciona ADR-0001 «si hace falta estado compartido». Motivo: las claves ya necesitan PostgreSQL, y un segundo servicio solo para contar sería otra pieza que instalar, vigilar y asegurar. Un contador por clave y ventana, actualizado en una sola sentencia, es exacto entre procesos (con N de límite pasan exactamente N) y basta para el volumen de una pasarela de empresa. Si un día no basta, se reconsidera con medidas.
- **Ventanas fijas**: por minuto natural y por día natural, **el día en UTC**. Efectos documentados:
  - alrededor del cambio de minuto pueden pasar **hasta el doble** del límite por minuto (N al final de un minuto y N al principio del siguiente);
  - el día del límite empieza a las 00:00 UTC, que en España es la 01:00 o las 02:00 según la época del año; los días del cambio de hora el día local tiene 23 o 25 horas, pero el día del límite siempre tiene 24.

### El pepper: copia de seguridad

- **Perder el pepper hace que todas las claves virtuales dejen de funcionar** (pasan a `unknown`): no hay forma de recuperarlas, hay que crear claves nuevas. Hay que guardar una copia del pepper **aparte** de la copia de la base de datos: juntas, quien las tenga puede probar claves; por separado, ninguna sirve sola. La guía de instalación lo explica en 8c, con `antifaz init`.

## Alternativas descartadas

- **Guardar la clave cifrada en vez de un hash**: haría falta descifrarla para comprobarla, y quien tenga la clave de cifrado tiene todas las claves. Un HMAC no se puede deshacer. Descartado.
- **argon2, bcrypt o scrypt**: ver arriba; coste de CPU por petición sin ganar nada con claves aleatorias. Descartado.
- **SHA-256 sin pepper**: con solo la base de datos se podría comprobar si una clave filtrada en otro sitio es válida. Descartado.
- **Un pepper sin versión**: rotarlo obligaría a cambiar todas las claves a la vez. Descartado.
- **Un control criptográfico (HMAC truncado) en vez de CRC32**: necesitaría el pepper para filtrar y daría una falsa idea de seguridad. El CRC32 solo filtra erratas, y así se dice.
- **Mensajes distintos para clave desconocida, revocada o caducada**: ayudan a quien prueba claves. Mismo 401 para todo. Descartado.
- **Valkey para contar los límites**: ver arriba. Descartado por ahora.
- **Ventanas deslizantes**: evitan el doble en el cambio de minuto, pero necesitan guardar cada petición o aproximar. Las fijas son exactas, simples y fáciles de explicar. Descartado por ahora.
- **Borrar claves**: se perdería a quién pertenecía un número en las evidencias. Solo se revocan.

## Consecuencias

- 8a deja una librería (`antifaz/keys/`, solo biblioteca estándar) que todavía no usa nada: ni rutas, ni CLI, ni ajustes, ni base de datos. El almacén en memoria implementa el mismo protocolo que tendrá el de PostgreSQL (8b).
- Tests de propiedad: toda clave generada se lee y se verifica; cualquier cambio de un carácter se rechaza como mal formada; la lectura solo lanza su error cerrado. Tests de red team con valores canario: ni la clave, ni su hash, ni el pepper salen en `repr`, `str`, `%r`, logs o tracebacks, y el almacén solo guarda hashes. Cobertura mínima del 90 % en `keys`.
- Hay un secreto nuevo que guardar (el pepper), con su copia de seguridad aparte.
- Los límites por minuto pueden llegar al doble en el cambio de minuto, y el día del límite no es el día local.
- Pendiente para el ítem 9: separar roles de la base de datos (hoy uno solo) y enlazar el número de la clave con las evidencias. Esta ficha se enmendará entonces.
