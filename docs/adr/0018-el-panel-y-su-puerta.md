# ADR-0018 · El panel y su puerta

- **Estado:** Aceptada (aprobada por Miquel Moreno el 2026-10-08)
- **Fecha:** 2026-10-08
- **Concreta:** el ítem 11 de la v0.2 (issue #53), la fila "Panel" del modelo de amenazas, la invariante 12 (rutas cerradas) y la invariante 15 (el panel no enseña datos personales)

## Contexto

La v0.2 añade un panel para el navegador, con cinco vistas: «Prueba un texto», «En directo», «Claves», «Políticas» y «Estado». El diseño ya está aprobado en `design/` (#30). La puerta de la pasarela (ADR-0015) está hecha para programas, no para navegadores: pide la clave en cada petición, rechaza los `Origin` de navegador, solo acepta JSON y no tiene CORS. El panel no puede romper ninguna de esas reglas.

Además, «Prueba un texto» es la única pantalla que recibe datos personales a propósito: la persona escribe un texto con un DNI para ver cómo se enmascara.

## Decisión

### Dónde vive y cuándo existe

- El panel vive **solo** bajo `/panel` (coincidencia exacta de `/panel` o del prefijo `/panel/`, decidida con `scope["path"]`, como en ADR-0015). `/PANEL`, `//panel` o `/panelx` no son el panel: siguen las reglas de la pasarela.
- **Sin `ANTIFAZ_ADMIN_TOKEN`, el panel no existe**: todas sus rutas dan 404 y no se carga nada de `antifaz.web`.
- El token de administrador cumple las mismas reglas que la clave de Antifaz (32 caracteres o más, aleatorio, distinto del valor de ejemplo) y además **tiene que ser distinto de `ANTIFAZ_API_KEY`** y de las claves de los proveedores. Si no, la pasarela no arranca y el mensaje nombra la variable, nunca el valor.
- `antifaz init` lo genera igual que la clave (`secrets.token_hex(32)`) y `antifaz doctor` lo comprueba. Se añade a `.env.example` y a la plantilla del paquete.

### Puertas separadas

- `GateMiddleware` decide primero por la ruta: `/panel*` sigue las reglas del panel y el resto, las de hoy. El `Host` se valida antes, como siempre.
- **La clave de la API no abre el panel y la sesión del panel no abre `/v1/*`.** Un test recorre todas las rutas registradas y comprueba que cada una está en una de las dos puertas, sin ninguna abierta salvo la lista de rutas públicas (invariante 12 ampliada).
- Rutas públicas del panel, con coincidencia exacta: `GET /panel/login`, `POST /panel/login` y `GET /panel/static/<archivo con hash>`.

### Solo HTTPS o localhost

- El panel solo funciona por HTTPS o en `localhost`. Por HTTP desde otra dirección, el panel responde con una página fija que explica cómo ponerlo detrás de HTTPS, y no deja iniciar sesión. (Corregido en el addendum del 2026-10-13: la cookie lleva `Secure` solo por HTTPS.)
- Antifaz no sirve TLS. Detrás de un proxy inverso, el esquema se lee de `X-Forwarded-Proto` **solo si la conexión llega de un proxy de confianza** configurado en `ANTIFAZ_TRUSTED_PROXIES` (IP o rangos CIDR; vacío por defecto). Si llega de cualquier otra IP, la cabecera se ignora. La pasarela no cambia: `proxy_headers` de uvicorn sigue apagado y esta lectura vive solo en la puerta del panel.
- **IP del cliente.** Si la conexión llega de un proxy de confianza, se recorre `X-Forwarded-For` **de derecha a izquierda** y se toma la primera IP que **no** es de un proxy de confianza. Nunca se toma la primera de la lista: esa la escribe el cliente y puede ser falsa. Si todas son de confianza, o la cabecera está vacía o mal formada, se usa la IP de la conexión. Si la conexión no llega de un proxy de confianza, `X-Forwarded-For` se ignora. Un test cubre una IP falsa a la izquierda, varios proxies encadenados, valores mal formados y la cabecera repetida.
- "localhost" se decide por el `Host` ya validado (`localhost`, `127.0.0.1` o `[::1]`), no por la IP de origen: dentro de Docker, una petición que llega por el puerto publicado no viene de `127.0.0.1`. Esta regla protege de un error de configuración honesto (abrir el panel por HTTP en la red local). No es una barrera contra un atacante: esa barrera es el token.

### Inicio de sesión

- Se entra con el token de administrador por `POST /panel/login` (JSON). La comparación es en tiempo constante (`hmac.compare_digest`) sobre huellas SHA-256 de igual longitud.
- **El panel guarda la huella del token, no el token**: al arrancar calcula la huella y es lo único que conserva el módulo del panel. El valor nunca se escribe en logs, respuestas ni plantillas (invariante 13: KeyWatch lo vigila como a las demás claves).
- **Límite de intentos**: como mucho 5 fallos por minuto por IP del cliente (ver «IP del cliente»). Al pasarse, 429 con `Retry-After`. **No hay límite global**: con un token de 256 bits no protege de nada y permitiría dejar fuera al administrador.
- **En Docker sin proxy, todas las peticiones llegan desde la misma IP** (la puerta de enlace de la red de Docker), así que en esa instalación el límite por IP funciona en la práctica como un límite para todos: quien falle 5 veces en un minuto bloquea el login de todos durante ese minuto. Con el puerto publicado solo en `127.0.0.1` (lo que trae el compose), solo puede hacerlo alguien que ya está en la máquina. Con un proxy de confianza delante, cada cliente tiene su IP. Documentado en la guía de instalación.
- Las respuestas de error del login son siempre iguales y no dicen si el token estaba cerca.

### Sesiones con cierre real

- Al entrar se crea un identificador nuevo de 256 bits (`secrets.token_urlsafe(32)`). El navegador lo recibe en la cookie de sesión con `HttpOnly`, `SameSite=Strict` y `Max-Age` de 8 horas. El nombre, `Secure` y `Path` dependen del esquema: ver el addendum del 2026-10-13.
- **En el servidor se guarda la huella del identificador, no el identificador**, con su hora de creación, su último uso y su token CSRF. Caducidad: 8 horas como máximo y 30 minutos sin uso.
- **Cierre de sesión real**: `POST /panel/logout` borra la sesión del servidor (la cookie robada deja de servir en ese momento), hace caducar **solo la cookie del panel del esquema de la petición** (misma cookie con `Max-Age=0`; ver el addendum del 2026-10-13) y envía `Clear-Site-Data: "cache"`. No se usa `"cookies"`: borraría las cookies de todo `localhost`, que no se separan por puerto, y cerraría las sesiones de otras aplicaciones del usuario.
- **Al cerrar o caducar una sesión se cortan sus conexiones de «En directo».** Cada conexión SSE queda ligada a su sesión; el cierre de sesión las cierra en el momento y la caducidad se comprueba en cada envío. Un test de red-team abre el SSE, cierra la sesión y comprueba que no llega nada más y que la conexión termina.
- Las sesiones viven en memoria detrás de una interfaz (`SessionStore`). Al reiniciar se cierran todas. Hoy `antifaz serve` arranca **un solo proceso**; si alguien arranca varios, cada uno tiene sus sesiones y el panel avisa en «Estado». Cuando llegue PostgreSQL (ítem 8), la interfaz permite guardarlas allí.

### CSRF

Tres capas en todo lo que no es `GET`:

1. `SameSite=Strict` en la cookie.
2. Cabecera `X-Antifaz-CSRF` con el token CSRF de la sesión (aleatorio, comparado en tiempo constante). La página lo recibe en un `<meta>`.
3. `Sec-Fetch-Site: same-origin` y `Origin` igual al esquema y `Host` validados. Si falta alguna de las dos, se rechaza. ADR-0015 descartó `Sec-Fetch-Site` para la pasarela porque la usan programas; el panel solo lo usan navegadores, así que aquí sí sirve.

Además, todo cuerpo es JSON: un formulario de otra web no puede enviarlo sin preflight, y sin CORS el preflight falla.

### Cabeceras de seguridad

En todas las respuestas de `/panel*`:

- `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`. Todo está cerrado por defecto y cada tipo de recurso se permite uno a uno. **Sin `'unsafe-inline'`, sin `'unsafe-eval'` y sin `data:`.**
- `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin` y una `Permissions-Policy` que apaga cámara, micrófono, geolocalización y similares.
- `Cache-Control: no-store` en el HTML y en la API del panel. Los archivos estáticos llevan nombre con hash y `Cache-Control: public, max-age=31536000, immutable`.
- Sin CORS.

Para cumplir la CSP, el prototipo se adapta: nada de `<script>` ni `<style>` en el HTML, nada de atributos `style="…"` ni `on…=`. El grano de fondo, hoy un SVG `data:` dentro del CSS, pasa a ser un archivo. Las fuentes y `tokens.css` salen de `design/` y pasan a los estáticos del paquete con sus licencias OFL.

### Plantillas y JavaScript: Jinja2, sin HTMX

- **Jinja2** (BSD-3) con autoescape, solo para el esqueleto de las páginas. Pasa a ser dependencia directa.
- **Sin HTMX.** Se usa JavaScript nativo, como el prototipo aprobado. HTMX mete un `<style>` en línea para sus indicadores y algunas de sus funciones evalúan texto como código, y las dos cosas chocan con la CSP de arriba. Es un cambio respecto a §3.2 y §9.1 de la especificación, que decían "Jinja2 + HTMX".
- **Sin SRI** (`integrity`) en los scripts: SRI protege de un CDN que cambia el archivo, y aquí todo se sirve desde la propia pasarela. Lo que protege los scripts es la CSP (`script-src 'self'`) y la cadena de suministro del repo.
- **Lo que escribe el usuario nunca pasa por una plantilla.** Viaja en JSON y el navegador lo pinta solo con `textContent` (nunca con `innerHTML`). Así no hay inyección de plantillas ni XSS por esa vía.

### «Prueba un texto»

- `POST /panel/api/try` (sesión + CSRF). Texto de 20 KB como mucho. Límite de 30 peticiones por minuto por sesión, más el límite de concurrencia del enmascarado que ya existe.
- Recorrido: `mask` con la política por defecto → **respuesta simulada** → `restore`. La respuesta simulada se construye **solo con los marcadores que ha emitido `mask`**. No llama a ninguna IA.
- **El código de la simulación no tiene acceso a ningún cliente de proveedor.** Vive en un módulo que no importa `antifaz.providers`, `antifaz.api.proxy` ni `httpx`, y su función no recibe ningún cliente. Dos tests lo demuestran: uno revisa las importaciones del módulo y todas las que arrastra, y otro arranca la app con transportes de proveedor que fallan en cuanto se usan, llama a «Prueba un texto» y comprueba que no se ha hecho ninguna llamada.
- Es la **única excepción a la invariante 15**: el valor solo vuelve en la respuesta a esa misma petición, a la persona que lo escribió, con `no-store`. El texto **nunca** va a los logs (un test con todo en `DEBUG`), a las evidencias ni al contador: el contador solo recibe tipos y cantidades, con el origen «Prueba un texto».
- El cuadro de texto lleva `spellcheck="false"`, `autocomplete="off"` y `autocorrect="off"`: algunos correctores del navegador envían el texto a un servidor externo.
- Llamar a la IA de verdad desde aquí va en un issue aparte, más adelante, con un OK de gasto.

### «En directo», «Claves», «Políticas» y «Estado»

- **«En directo»** muestra un contador **en memoria** con solo tipos y cantidades. Los eventos se construyen solo con enums, enteros y la hora, así que no hay sitio para un texto libre. El programa cliente sale de una lista fija de `User-Agent` conocidos; uno desconocido sale como «Otro» y el `User-Agent` nunca se repite.
- El contador está detrás de una interfaz (`LiveFeed`: `record`, `snapshot`, `subscribe`) que el registro de evidencias (ítem 9) podrá sustituir.
- El panel pone **«desde el arranque»** junto a las cifras y explica que, con varios procesos, cada uno cuenta lo suyo.
- Llega al navegador por SSE (`/panel/api/live`), **solo mientras alguien mira**: el navegador abre la conexión con la vista visible y la cierra cuando se oculta. Como mucho 5 conexiones a la vez y 30 minutos por conexión; cada conexión se corta cuando su sesión se cierra o caduca. Sin conexiones abiertas no hay ningún temporizador en el servidor (cero CPU si nadie mira).
- **«Claves»** y **«Políticas»** son de solo lectura hasta los ítems 8 y 10. «Claves» dice cuántas hay y de dónde salen; nunca enseña una clave ni un trozo.
- **«Estado»** enseña la versión, el estado del NER y qué proveedores están configurados. «Comprobar ahora» usa la misma lógica que `antifaz doctor --providers`.

### GIF y capturas

El GIF de la v0.2 y las capturas del panel real se graban con Playwright contra un proveedor falso y **solo con datos sintéticos**, sacados de un archivo de textos de ejemplo del repo. Un test comprueba que el script de grabación no usa otros textos.

## Addendum (2026-10-13, aprobado por Miquel Moreno): la cookie según el esquema

La decisión de arriba ponía siempre `__Host-antifaz_session` con `Secure`. Al probarlo con navegadores reales, WebKit no guarda ninguna cookie `Secure` que llega por `http://localhost`, así que el panel no funcionaría en `http://localhost:8000/panel` con ese motor. Este addendum fija dos cookies, una por esquema, y corrige tres apartados.

**Prueba (Playwright 1.63.0, 2026-10-09).** Atributos `HttpOnly; SameSite=Strict; Path=/`. Pregunta: ¿el navegador devuelve la cookie en la siguiente petición?

| Navegador | Dirección | (a) `__Host-` + `Secure` | (b) `antifaz_session` + `Secure` | (c) `antifaz_session` sin `Secure` |
|---|---|---|---|---|
| Chromium 153.0.8010.12 | `http://localhost` y `http://127.0.0.1` | Sí | Sí | Sí |
| Firefox 155.0 | `http://localhost` y `http://127.0.0.1` | Sí | Sí | Sí |
| WebKit 26.6 (build de Windows, no Safari) | `http://localhost` y `http://127.0.0.1` | No | No | Sí |

Salen **dos casos, no tres**: (b) no ayuda nunca, porque Chromium y Firefox ya aceptan (a) y WebKit rechaza cualquier cookie `Secure` por HTTP.

**Decisión.**

- **HTTPS** (directo, o `X-Forwarded-Proto` de un proxy de confianza, y solo de él): cookie `__Host-antifaz_session` con `Secure; HttpOnly; SameSite=Strict; Path=/` y `Max-Age=28800`.
- **`http://localhost`, `127.0.0.1` o `[::1]`**: cookie `antifaz_session` **sin** `Secure`, con `HttpOnly; SameSite=Strict; Path=/panel` y `Max-Age=28800`.
- **Nunca `Domain`.**
- **El servidor solo acepta el nombre del esquema de la petición.** Una cookie sin `Secure` también viaja por HTTPS, así que una cookie puesta por `http://localhost` nunca vale en una sesión HTTPS, ni al revés.
- **La cabecera `Cookie` se lee a mano**: se juntan todas las líneas `Cookie` (HTTP/2 puede partirlas), el nombre se compara exacto y con mayúsculas, y si el nombre del esquema aparece **más de una vez**, no hay sesión (decisión de Miquel). Puede pasar de verdad: dos instancias de Antifaz en puertos distintos de `localhost` se pisan la sesión, porque las cookies no se separan por puerto; y otra aplicación local con una cookie del mismo nombre puede impedir entrar hasta que se borre esa cookie.

**Límite conocido.** En `localhost` las cookies no se separan por puerto, y `SameSite` tampoco separa puertos (`localhost:3000` es el mismo sitio que `localhost:8000`). Lo que protege es la capa 3 del CSRF (`Sec-Fetch-Site: same-origin` y `Origin` con el puerto) y el token CSRF de la sesión. `Path=/panel` reduce lo que ve otra aplicación del mismo `localhost`.

**Pendiente.** Comprobar Safari de verdad en 11f, en un runner macOS de GitHub. Sustituye a la frase de «Consecuencias» que decía que se comprobaría en el primer PR de la puerta.

**Corrige:**

- «Solo HTTPS o localhost»: la cookie no lleva siempre `Secure`; solo por HTTPS.
- «Sesiones con cierre real»: el nombre y la ruta de la cookie dependen del esquema.
- «Cierre de sesión»: se hace caducar solo la cookie del esquema de la petición.

**Nota (2026-10-14, PR 11b-2b).** La sesión guarda el esquema con el que se creó; si se presenta por el otro esquema, se rechaza y se borra (motivo `scheme_mismatch`). El nombre de la cookie ya separaba los esquemas; esto cubre que alguien copie el id a mano de uno a otro. El CSRF se comprueba en la puerta para todo lo que no es GET o HEAD; la única excepción a la capa del token es el login, que conserva origen + JSON.

**Nota (2026-10-16, PR 11c-1).** Concreta cuatro puntos, sin cambiar la decisión:

- **La ruta pública de los estáticos** es `GET` y solo `GET` de una ruta que es **exactamente** una del manifiesto (`/panel/static/<nombre>.<12 hex del SHA-256>.<ext>`, `web/assets.py`). Cualquier otro nombre bajo `/panel/static/` (sin hash, una licencia OFL, un truco de ruta) no es público y sigue la regla general: 401 sin sesión y 404 con ella. Los archivos se leen una vez al arrancar y se sirven desde memoria, por nombre exacto; solo `.css`, `.js`, `.woff2` y `.svg`, con su `Content-Type` fijo.
- **`Cache-Control` se decide por respuesta, en la puerta**: `public, max-age=31536000, immutable` solo para un 200 de un archivo del manifiesto; `no-store` para todo lo demás, también los errores bajo `/panel/static/`.
- **Los archivos de diseño tienen un solo sitio, el paquete** (decisión de Miquel): `tokens.css`, `fonts.css`, las fuentes y sus licencias OFL viven en `src/antifaz/web/static/`, y el prototipo los carga desde ahí. El logo sigue en `docs/images/logo.svg` y el paquete lleva una copia idéntica (con test) para el favicon.
- **«Comprobar ahora» pasa a 11e.** En 11c, la tarjeta «Estado» dirá «Desde la terminal: `antifaz doctor --providers`»; el botón que hace la misma comprobación desde el panel llega con 11e.

**Nota (2026-10-18, PR 11c-2b).** El panel tiene idiomas, la pantalla para entrar y el esqueleto de las cinco vistas, sin cambiar la puerta, las rutas ni las cabeceras de arriba:

- **Idiomas.** Los textos viven en catálogos `web/i18n/<código>.json` (texto plano, `en.json` es la referencia) que se leen y comprueban al arrancar; un catálogo roto no deja arrancar. El idioma de cada petición sale de `?lang=xx` solo si es un catálogo cargado (nunca se repite el valor que llega), si no de `Accept-Language` (con límite de tamaño y de entradas) y si no, inglés. Las dos páginas HTML añaden `Content-Language` y `Vary: Accept-Language`; siguen con `no-store`. Los estáticos no dependen del idioma.
- **Entrar.** `login.js` (del manifiesto) envía el token como JSON a `POST /panel/login` y vacía el campo antes de esperar la respuesta. No envía `X-Antifaz-CSRF`: el login sigue siendo la única excepción a la capa del token. El campo del token no tiene `name`, así que el formulario no envía nada por sí solo.
- **Salir.** `panel.js` envía `POST /panel/logout` con `X-Antifaz-CSRF` (del `<meta>`) y vuelve a la pantalla de entrar. El botón de atrás no enseña el panel tras salir gracias a la puerta y a `no-store`; además, como defensa añadida, una página que vuelve de la caché de atrás y adelante se recarga.
- **Nada guardado en el navegador**: ni el idioma, ni el tema, ni el token. Los scripts no tienen texto visible (todo sale de la página) ni comentarios, y no usan `innerHTML`, `eval`, almacenamiento ni `document.cookie`.
- **Otro sitio no puede entrar ni salir.** Un test con navegadores reales abre una página de `localhost` (otro sitio que `127.0.0.1`) que envía al login y al logout de `127.0.0.1` un `fetch` JSON, uno `no-cors` en `text/plain` y un formulario `text/plain` con cuerpo JSON: la puerta los rechaza todos (o el navegador ni los envía) en Chromium, Firefox y WebKit. Lo mismo desde `127.0.0.1` en otro puerto, el mismo sitio con otro origen, donde la cookie sí viaja.

**Nota (2026-10-19, PR 11c-3b).** «Estado» enseña datos reales, sin cambiar la puerta:

- **`GET /panel/api/status`** (`async def`), solo con sesión del esquema de la petición; la clave de la API no lo abre y no es pública. Sin token CSRF por ser `GET`; los demás métodos, 403 o 405, y `HEAD` 405. **Solo la propia página lo lee**: en `/panel/api/*` (también `GET`), un `Sec-Fetch-Site` presente que no sea `same-origin` (otra web, otro puerto de `localhost`, la barra de direcciones) es 403 antes de mirar la sesión; sin la cabecera (no es un navegador) decide la sesión. `GET /panel` no cambia. Cabeceras de la puerta y `no-store`, sin `Vary` ni `Content-Language` (el JSON no tiene idioma). Esquema cerrado (pydantic, `extra="forbid"`): solo booleanos, enteros, enums, la versión y, de una URL de proveedor propia, el host y el puerto comprobados; nunca usuario, contraseña, ruta, consulta ni fragmento (si hay usuario, la vista avisa). El host es el que contacta httpx (la URL se lee con `httpx.URL`); si httpx la rechaza, o una etiqueta parece una clave (más de 32 caracteres o empieza por `sk-`), sale `custom`. Como solo se enseña host[:puerto], un host no debe llevar nunca un secreto. El estado general es `danger` si el NER está parado o ningún proveedor tiene clave (no se puede reenviar nada), y `warn` si el NER arranca, está en pausa o es desconocido, o una dirección lleva usuario.
- **Los datos salen de un `StatusFacts` sin secretos** que la pasarela construye al arrancar; los `Settings` siguen sin llegar a `antifaz.web`. El esquema es el único módulo de `antifaz.web` que importa pydantic, y solo piezas de modelo (nunca `SecretStr`).
- **Sin sondeo**: el navegador pide el estado al enseñar la vista por primera vez, al pulsar «Actualizar» (lee el estado local, nunca llama a un proveedor) y al volver a estar visible la página con «Estado» elegido. Nada al cargar, ningún temporizador y nada con la página oculta.
- **Respecto al diseño aprobado**: fuera «Escucha en», «Base de datos», `/healthz` y las latencias; dentro «Panel servido por», los recuentos de hosts, orígenes y proxies de confianza, y la nota de que las sesiones viven en memoria por proceso. Evidencias queda como «Llega con el registro de evidencias (punto 9)». La píldora de la cabecera, el punto en la pestaña, los contadores y «Comprobar ahora» siguen en 11e.

**Nota (2026-10-19, PR 11d-a).** «Prueba un texto» tiene su API (`POST /panel/api/try`), sin pantalla todavía (11d-b), sin cambiar la puerta salvo por la ruta nueva:

- **Ruta y límites** (decisiones de Miquel): sesión + CSRF; cuerpo exactamente `{"text", "lang"}` (si no, 400 `invalid_try_body` sin repetir nada); 128 KiB de cuerpo como mucho, con o sin `Content-Length`, leído en 5 s (413 y 408); el texto, 20 480 bytes en UTF-8 como mucho (413 `text_too_large`); 30 intentos en 60 s **por sesión** (huella de la sesión, tabla acotada que se vacía al cerrar la sesión, comprobado antes de leer el cuerpo; 429 `too_many_tries` con `Retry-After`). Concreta los «20 KB» y «30 por minuto» de arriba.
- **Concurrencia: el proxy va primero**: una sola prueba a la vez y sin cola (otra a la vez es 503 `try_busy` con `Retry-After: 1`), tomada antes del límite de enmascarado: el panel nunca ocupa más de uno de sus hilos. Con el NER, cada prueba usa `ner.for_try()`: el mismo modelo y los mismos procesos, **sin la caché de spans** (un acierto de caché delataría por el tiempo que el texto pasó por el proxy; por eso la respuesta tampoco lleva tiempos), **como mucho 2 ventanas, una por lote, con un plazo de 5 s** (más ventanas: 413 `text_too_long_for_ner` sin pedir nada a ningún proceso), un proceso **solo si ninguna petición del proxy lo espera** y está libre y cargado (si no, 503 `ner_busy`, comprobado antes de cada lote), **nunca cuenta para el cortacircuitos** y, si rompe un proceso, 60 s de enfriamiento solo para las pruebas. Cota honesta: una petición del proxy que llega mientras corre una prueba espera un lote (una ventana); con un solo proceso, esperar cero obligaría a matar el lote y recargar el modelo. Las respuestas «ocupado» revelan actividad, nunca su contenido.
- **El recorrido es el real**: `mask` con el detector de la pasarela (sus validadores, patrones y modelo NER) y la política de la pasarela, la **guardia de salida** sobre el texto enmascarado, la respuesta simulada solo con los tokens y `restore`. Bloqueado (detector que falla o guardia) es un 200 sin nada enviado. El módulo (`web/try_text.py`) no importa tampoco `antifaz.api` (el proxy importa httpx), `socket` ni `ssl`; el test del cierre de importaciones lo comprueba dentro del proyecto (python-stdnum carga `socket` y `ssl` para consultas en línea que nunca se usan).
- **Cambio en el núcleo** (aprobado): `MaskResult.found_spans`, por texto, con inicio, fin, tipo, oculto o permitido y token; nunca el valor. Solo lo leen `mask/` y `web/try_text.py` (test estático).
- **Las posiciones van en unidades UTF-16**, la unidad de JavaScript, convertidas en el servidor, con `length_utf16` para que la página compruebe que habla del mismo texto. Python cuenta puntos de código; sin convertir, un emoji desplazaría todas las marcas siguientes.
- **La respuesta** tiene un esquema cerrado (`web/try_response.py`, el segundo módulo del panel que importa pydantic), sin `response_model` en la ruta (se valida una vez) y nunca devuelve el texto original ni tiempos. Los segmentos se cortan con el patrón del restaurador. La versión de la política sigue la regla única de `antifaz.policy`. Volver a entrar empieza el límite de 30 de cero (la entrada se borra al cerrar la sesión).
- **Memoria**: Python no puede borrar una cadena; el texto vive en memoria del proceso mientras dura la petición y hasta que se reutiliza (puede acabar en un volcado o en el swap). Con el NER, también viaja como JSON por una tubería al proceso del NER y vive en su memoria. Se documenta tal cual en TECNICO.
- Aún sin contador (11e) ni textos de ejemplo (11d-b).

**Nota (2026-10-20, PR 11d-b).** La pantalla de «Prueba un texto» y el aviso de caducidad de la sesión (decisiones de Miquel):

- **Sin el detector de nombres, solo si el usuario lo pide**: el cuerpo admite además `"ner"`, un booleano JSON de verdad (0, `"false"`, `null`, repetido o con otra clave: el 400 de siempre). `"ner": false` enmascara con el escáner sin NER, sin el detector del proxy y sin su caché. La respuesta dice `names_checked` (verdadero solo si el NER de la prueba leyó el texto). Con el NER encendido y sin `"ner": false`, un fallo del NER (proceso muerto, plazo, cortacircuitos abierto, ocupado) es un error (bloqueado `detector_failed` o 503 `ner_busy`), nunca un resultado sin nombres. La oferta «Probar sin el detector de nombres» aparece solo tras el 413 `text_too_long_for_ner`.
- **Un solo número para «unas N palabras»**: `TRY_NER_MAX_WORDS` en `api/app.py`, derivado de las ventanas del NER (`WINDOW_TOKENS + (TRY_NER_MAX_CHUNKS - 1) × (WINDOW_TOKENS - OVERLAP_TOKENS)`), pasado al panel en `PanelState` y pintado en la página; un test prohíbe el número en plantillas, scripts y catálogos. El contador de la página es una estimación (≈); manda el servidor.
- **Sin recortar**: cada columna enseña el texto entero y se desplaza por dentro (región con nombre y `tabindex=0`).
- **Sesión**: `POST /panel/api/session` (sesión + CSRF) solo alarga la inactividad, porque es un uso como cualquier otro, y nunca pasa de las 8 horas desde el login. La página recibe los segundos que quedan en dos `meta`. `static/session.js` tiene **el único temporizador** del panel: avisa unos 2 minutos antes con un aviso en el flujo de la página (`role=status`, sin mover el foco ni tapar nada), pregunta al servidor solo al pulsar «Seguir» y, al caducar, borra el texto y vuelve al login. Cerca de las 8 horas no hay «Seguir».
- **Tres scripts independientes** (`panel.js`, `session.js`, `try.js`) que hablan con eventos del DOM; cada uno pide solo su URL literal.
- Fuera del prototipo, por depender de trabajo futuro: el selector de política, los tiempos, el modelo, el 504, la cadena rota, la animación y la píldora de estado de la cabecera. El móvil usa la barra inferior real del panel.

**Nota (2026-10-20, PR 11e-1).** El motor de «En directo», sin pantalla todavía (11e-2), sin cambiar la puerta salvo por la ruta nueva y una excepción (decisiones de Miquel):

- **El contador** (`antifaz/live.py`, biblioteca estándar, nunca importa `antifaz.web`): `LiveFeed` (`record`, `snapshot`, `subscribe`), `MemoryLiveFeed` con el panel y `NullLiveFeed` sin él. Eventos solo de enums, enteros y la hora **redondeada al minuto**; el cliente de una lista cerrada de prefijos de `User-Agent` (nunca el valor); los tipos son valores distintos ocultados (`Vault.type_counts()`), nunca los permitidos. Los últimos 20, totales por tipo y resultado solo del proxy («Prueba un texto» y `/antifaz/scan` aparte), el tiempo en marcha y cuántos procesos hay.
- **Solo en memoria**: ningún evento va a un log ni al disco (tests estático y de red team).
- **Se registra en un solo sitio** (`api/live_record.py`), una vez por petición al acabar; el contador nunca rompe ni frena el proxy.
- **`GET /panel/api/live`** (SSE, sesión por la puerta, `Accept: text/event-stream` o 406, solo `GET`): `snapshot`, `request` y `end` (`logout`, `expired`, `replaced`, `busy`, `max_age`, `shutdown`), nunca `retry:`. Esquema cerrado (`web/live_response.py`, el tercer módulo del panel con pydantic): cada hoja es un enum o un entero.
- **Excepción de la puerta, `NOT_A_USE`**: `GET /panel/api/live` (y su `HEAD`, 405) pasa la puerta como cualquier ruta con sesión, pero **nunca cuenta como un uso**: mirar «En directo» no alarga la sesión. Es la única ruta de la lista (test).
- **Límites de conexión**: 5 por proceso (como arriba) y además **2 por sesión**; pasado eso, 200 con `end: busy`. El plazo de cada una es el menor de 30 minutos y lo que le queda a la sesión, comprobado otra vez al llegar («Seguir» la alarga). Cola de 32 por conexión; llena, un `snapshot` nuevo. Latido cada 25 s solo con conexiones abiertas; sin conexiones, ni tareas ni temporizadores. Un sitio nunca queda ocupado por una conexión cortada, atascada o rota, y cada envío tiene un plazo de 30 s. Al apagar, uvicorn espera hasta 20 s y después corta las conexiones abiertas, antes de avisar al `lifespan`: `end: shutdown` solo llega si el `lifespan` termina primero. Los 5 sitios los puede ocupar cualquiera con el token (límite de disponibilidad, documentado).
- **Lo que la hora puede delatar**: en directo, quien mira puede atar un evento a quien sabe que mandó algo en ese momento; el minuto reduce la precisión de lo que queda en el resumen, no lo que se ve al momento. Documentado en TECNICO.

## Alternativas descartadas

- **Reutilizar la clave de la API para el panel**: mezcla dos permisos. Quien usa la IA no tiene por qué poder ver el panel.
- **Límite global de intentos de login**: con un token de 256 bits no añade protección y permite dejar fuera al administrador.
- **SRI en scripts propios**: no protege de nada sin un CDN de por medio.
- **Tomar la primera IP de `X-Forwarded-For`**: la escribe el cliente.
- **Cookie firmada sin sesión en el servidor**: sirve con varios procesos, pero no permite un cierre de sesión real. Una cookie robada seguiría valiendo hasta caducar.
- **`itsdangerous` o una librería de sesiones**: es una dependencia más para algo que son pocas líneas con la biblioteca estándar.
- **HTMX**: choca con la CSP estricta (ver arriba).
- **Pedir el estado cada pocos segundos (sondeo)**: gasta CPU aunque nadie mire.
- **Leer `X-Forwarded-Proto` de cualquiera**: un cliente podría decir que viene por HTTPS sin ser verdad.
- **Llamar a la IA real en «Prueba un texto» desde el principio**: cuesta dinero, envía datos fuera al probar y añade superficie de ataque. Va después, aparte.

## Consecuencias

- Hay dos variables nuevas, `ANTIFAZ_ADMIN_TOKEN` y `ANTIFAZ_TRUSTED_PROXIES`, en `init`, `doctor`, `.env.example` y la plantilla del paquete. Un test impide que esas tres copias se separen.
- La puerta tiene una rama nueva y es la pieza más delicada del issue. Lleva tests de red-team: XSS por «Prueba un texto», CSRF con formulario `text/plain` y con `fetch` de otro origen, clickjacking, trucos de ruta, cookie manipulada o de una sesión cerrada, SSE que sigue abierto tras cerrar sesión, `X-Forwarded-For` falsificado, fuerza bruta en el login, demasiadas conexiones SSE, cuerpos enormes y DNS rebinding.
- La invariante 15 se prueba de punta a punta: se pasa un DNI centinela por el proxy y por «Prueba un texto», y después se piden todas las páginas del panel y se leen eventos SSE. El centinela no aparece en ninguna, ni en los logs.
- Las sesiones y el contador se reinician con cada arranque y van por proceso hasta los ítems 8 y 9.
- El panel necesita HTTPS o `localhost`. Lo que hacen los navegadores con las cookies en `http://localhost` está en el addendum del 2026-10-13; **Safari de verdad** se comprueba en 11f, en un runner macOS de GitHub.
- Las comprobaciones con Playwright inyectan axe en la página, y la CSP podría bloquearlo. Por eso las infracciones de CSP se miden en una sesión del navegador sin esa inyección, y axe en otra.
- Se añade Jinja2 a la imagen y a la lista de licencias revisadas (`check_licenses.py`).
