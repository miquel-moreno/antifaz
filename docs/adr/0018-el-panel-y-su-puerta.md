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

- La cookie de sesión lleva `Secure`, así que el panel solo funciona por HTTPS o en `localhost`. Por HTTP desde otra dirección, el panel responde con una página fija que explica cómo ponerlo detrás de HTTPS, y no deja iniciar sesión.
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

- Al entrar se crea un identificador nuevo de 256 bits (`secrets.token_urlsafe(32)`). El navegador lo recibe en la cookie `__Host-antifaz_session` con `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/` y `Max-Age` de 8 horas.
- **En el servidor se guarda la huella del identificador, no el identificador**, con su hora de creación, su último uso y su token CSRF. Caducidad: 8 horas como máximo y 30 minutos sin uso.
- **Cierre de sesión real**: `POST /panel/logout` borra la sesión del servidor (la cookie robada deja de servir en ese momento), hace caducar **solo la cookie del panel** (misma cookie con `Max-Age=0`) y envía `Clear-Site-Data: "cache"`. No se usa `"cookies"`: borraría las cookies de todo `localhost`, que no se separan por puerto, y cerraría las sesiones de otras aplicaciones del usuario.
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
- El panel necesita HTTPS o `localhost`. **Sin verificar todavía:** si Safari acepta cookies `Secure` en `http://localhost`. Se comprueba en el primer PR de la puerta; si no las acepta, se documenta.
- Las comprobaciones con Playwright inyectan axe en la página, y la CSP podría bloquearlo. Por eso las infracciones de CSP se miden en una sesión del navegador sin esa inyección, y axe en otra.
- Se añade Jinja2 a la imagen y a la lista de licencias revisadas (`check_licenses.py`).
