# ADR-0015 · La puerta cerrada por defecto

- **Estado:** Aceptada (aprobada por Miquel Moreno el 2026-09-30)
- **Fecha:** 2026-09-30
- **Concreta:** el modelo de amenazas (pasarela desde un navegador, JSON ambiguo, rutas, arranque) y las invariantes 12 y 13

## Contexto

Hasta ahora cada ruta del proxy comprobaba la clave por su cuenta. Una ruta nueva que se olvidara de hacerlo quedaría abierta. Además quedaban puertas laterales: un JSON con la misma clave dos veces (se revisa una copia y otro programa lee la otra), una web ajena que usa la pasarela desde el navegador de un empleado, un `Host` manipulado (DNS rebinding) y una pasarela desplegada sin clave o con la de `.env.example`.

## Decisión

**Orden en cada petición:** `Host` (400) → ruta pública o clave (401) → `Origin` (403) → `Content-Type` (415). El `Host` va **antes** que la clave a propósito: es la defensa contra el DNS rebinding, y una petición para un nombre que no es nuestro no llega a nada más.

**Una sola puerta para todas las rutas** (`api/gate.py`, un middleware ASGI puro):

- Toda ruta exige la clave de Antifaz salvo una **lista de permitidas** con coincidencia exacta: hoy solo `/healthz`. `/healthz/`, `//healthz` o `/HEALTHZ` piden clave.
- La decisión se toma sobre `scope["path"]`, la misma ruta ya decodificada que usa el router. Nunca sobre `request.url` ni cabeceras que el cliente puede reescribir (`X-Forwarded-*`). Así `/v1/chat%2Fcompletions` es la misma ruta, con la misma clave y la misma guardia.
- **`root_path` no está soportado.** Si se sirve bajo un prefijo (`uvicorn --root-path`), la lista pública compara la ruta exacta y puede no coincidir: entonces `/healthz` también pide clave. Falla cerrada; está documentado.
- La clave se acepta en `Authorization: Bearer` o en `x-api-key` (en todas las rutas), comparada con `hmac.compare_digest`. Si una de esas cabeceras viene repetida → 401. Si vienen las dos, las dos tienen que llevar la clave: un conflicto (una buena y otra mala) → 401. Nunca se elige cuál creer.
- El middleware **falla cerrado por sí solo**: si se construye con una clave vacía o de menos de 32 caracteres, lanza `ValueError`, aunque alguien se salte la comprobación de arranque.
- No hay rutas WebSocket: cualquier WebSocket se cierra, con clave o sin ella.
- `redirect_slashes=False`: un alias como `/v1/messages/` es un 404, nunca una redirección 307 construida con el `Host` que manda el cliente.
- `/docs`, `/redoc` y `/openapi.json` están **desactivados**: menos superficie pública y nada que decidir sobre si piden clave.

**Peticiones desde un navegador.** Una petición con cabecera `Origin` se rechaza con 403 salvo que ese origen exacto esté en `ANTIFAZ_ALLOWED_ORIGINS` (vacía por defecto). Cada origen configurado tiene que ser `http(s)://host[:puerto]`, sin ruta ni barra final (así lo envían los navegadores); `*`, `null` o una URL con ruta impiden arrancar. Si llega más de un `Origin`, todos tienen que estar permitidos. No hay CORS: ni siquiera un origen permitido recibe cabeceras `Access-Control-*`. Además, toda petición que puede llevar cuerpo (todo salvo `GET`, `HEAD` y `OPTIONS`) debe ser `application/json`, sin más parámetro que `charset=utf-8`. Un formulario o un `text/plain` (que el navegador envía sin preflight) → 415.

**JSON ambiguo.** Al leer el cuerpo:

- Un objeto con dos claves **idénticas** → 400 `invalid_request`, siempre.
- Dos claves que solo cambian en mayúsculas o anchura (se comparan tras NFKC y `casefold`) → 400 **solo si** esa clave es una de las que Antifaz lee por nombre: `READ_KEYS`, definida en un único sitio (`providers/json_walk.py`): `content`, `role`, `type`, `text`, `messages`, `system`, `tools`, `tool_calls`, `arguments`, `input`, las claves de adjunto (`source`, `data`, `image_url`, `file`…) y el resto de campos que el código consulta. `content` y `Content` se rechazan; un esquema con las propiedades `Name` y `name`, o `ss` y `ß`, pasa, porque Antifaz no lee esas claves (`name` no está en la lista).
- Vale para las dos rutas de Anthropic, `count_tokens` incluido, y para OpenAI. El cuerpo que sale hacia el proveedor se reconstruye siempre desde lo revisado (ADR-0013). Los `arguments` de herramientas (JSON dentro de una cadena) no pasan por esta regla: si traen una clave repetida, se quedan con la última, se enmascara esa y se vuelven a serializar, así que la copia descartada no sale.

**Claves de adjunto sin distinguir mayúsculas.** `source`, `Source` y `SOURCE` (y el resto de claves de adjunto, también en anchura completa) bloquean igual en las dos rutas: un proveedor o un intermediario podría leerlas sin distinguir mayúsculas.

**Host.** `TrustedHostMiddleware` de Starlette con `ANTIFAZ_ALLOWED_HOSTS` (por defecto `localhost`, `127.0.0.1` y `[::1]`). Se compara sin mayúsculas: la lista se pasa a minúsculas al leerla y la cabecera `Host` también. `*` no se acepta, y un comodín necesita al menos dos etiquetas detrás (`*.ejemplo.com` sí; `*.com` no). Un `Host` que no está en la lista → 400.

**Arranque seguro.** `create_app()` se niega a arrancar (`UnsafeConfigError`, con la variable y el motivo, nunca el valor) si:

- falta `ANTIFAZ_API_KEY`, tiene menos de 32 caracteres, menos de 8 caracteres distintos (`abab…` no es aleatoria), no es ASCII imprimible sin espacios (viaja en una cabecera HTTP) o es el valor de ejemplo. El valor de ejemplo se reconoce sin mayúsculas, espacios, `-` ni `_`: `change-me`, `change_me`, `ChangeMe`;
- `ANTIFAZ_OPENAI_API_KEY` o `ANTIFAZ_ANTHROPIC_API_KEY` están definidas pero vacías, son el valor de ejemplo o no son ASCII imprimible sin espacios (si no están, esa ruta responde 503, como hasta ahora);
- `ANTIFAZ_ALLOWED_HOSTS` está vacía o tiene un comodín demasiado amplio, o `ANTIFAZ_ALLOWED_ORIGINS` tiene algo que no sea un origen exacto.

Las listas se leen separadas por comas o en JSON (`[ "a", "b" ]`, con espacios). La app ya no se construye al importar el módulo: el servidor la crea con `uvicorn --factory antifaz.api.app:create_app`.

**Claves y datos fuera de logs y respuestas (invariante 13).**

- El log de cada petición solo escribe la ruta si es una ruta registrada; si no, `-` (una clave o un DNI pegados en la URL acabarían en el log).
- `X-Request-ID`: la pasarela **siempre** genera el suyo y no usa el del cliente, así nada que escriba el cliente llega a los logs ni a las cabeceras por ahí.
- Si la respuesta del proveedor (de éxito o de error) repite alguna clave configurada, se descarta con un 502 fijo. Se busca en los bytes tal cual y en las cadenas y claves del JSON ya decodificado, así que una clave escrita con escapes (`a`, `\/`) también se detecta.

**Enmienda del 2026-10-01 (issue 29) · Propuesta (la aprueba Miquel en el PR).**

- El esquema OpenAPI se publica como **archivo estático**, `docs/openapi.json`, generado desde la propia app con `make openapi`. Un test falla si el archivo del repo no coincide con el generado. La pasarela sigue sin servir `/docs`, `/redoc` ni `/openapi.json`: la documentación existe sin abrir ninguna ruta.
- Los 404 y 405 del router usan el mismo formato de error que el resto (`not_found`, `method_not_allowed`), con mensaje fijo. El 405 no se documenta por operación en el OpenAPI (sería una respuesta de un método que la ruta no tiene); está en `docs/TECNICO.md`.
- Cada ruta con clave se clasifica por **(ruta, método)** en el test de la invariante 12: envía cuerpo al proveedor (guardia), solo envía consulta (detector) o no llama a ningún proveedor. Un método nuevo en una ruta existente también hay que clasificarlo.

## Alternativas descartadas

- **Dependencia de FastAPI en cada router**: es seguridad registrada ruta a ruta; un router nuevo sin ella quedaría abierto. El middleware se aplica antes del router, a todo.
- **`CORSMiddleware` con una lista vacía**: es código de más para no permitir nada, y un error de configuración abriría la pasarela a cualquier web. Rechazar `Origin` es más simple y cierra por defecto.
- **Mirar `Sec-Fetch-Site`**: solo lo mandan los navegadores modernos; `Origin` basta y el `Content-Type` obligatorio fuerza el preflight que, sin CORS, el navegador no supera.
- **Rechazar toda colisión de mayúsculas**: rompía esquemas legítimos (`Name` y `name`). **Rechazar solo duplicados exactos**: `content` y `Content` los tratan igual algunos lectores y distinto otros. Se rechaza la colisión solo en las claves que Antifaz lee.
- **Aceptar el `X-Request-ID` del cliente si es corto y sin símbolos**: un DNI (`12345678Z`) cumple esa regla. Generar siempre el nuestro es más simple.
- **Dejar `/docs` y `/openapi.json` detrás de la clave**: funcionaría, pero es superficie sin uso en una pasarela; se pueden reactivar en local si hace falta.
- **Aceptar `*` en `ANTIFAZ_ALLOWED_HOSTS` detrás de un proxy inverso**: el proxy puede reenviar un `Host` cualquiera. Se pide la lista de nombres reales (se admiten comodines de subdominio, `*.ejemplo.com`).
- **Limpiar la clave del cuerpo del proveedor en vez de descartarlo**: más código y una respuesta modificada que ya no es la del proveedor. Un 502 fijo es más simple y honesto.

## Consecuencias

- Invariante 12: un test recorre las rutas registradas (con HEAD, OPTIONS, alias y barra final) y comprueba que sin clave dan 401 salvo `/healthz`, y que cada ruta del proxy pasa por la guardia de salida. Una ruta nueva queda cubierta sin tocar el test.
- Invariante 13: un test con claves canario recorre 401, 400, 403, 404, 413, 415, 502, 504 y errores del proveedor (también con la clave escapada en JSON), con todos los loggers en `DEBUG`, y comprueba que ninguna clave aparece en logs, cuerpos ni cabeceras.
- **Rompe** despliegues que usaban una clave corta o poco variada, la de ejemplo, un `Host` distinto de `localhost`, un origen con ruta o clientes que reutilizaban su `X-Request-ID`, y peticiones con claves repetidas: hay que ajustar `.env` o el cliente (ver `docs/TECNICO.md`).
- Un navegador no puede usar la pasarela salvo que se configure su origen exacto, y aun así sin CORS (sirve para herramientas propias que mandan `Origin`, no para una web pública).
- La ruta de OpenAI acepta ahora la clave también en `x-api-key`.
- Limitación: un proveedor que devuelve la clave **parcialmente** (por ejemplo `sk-...abcd` en su error de clave incorrecta) no se detecta; es su propio formato recortado, no la clave completa.
