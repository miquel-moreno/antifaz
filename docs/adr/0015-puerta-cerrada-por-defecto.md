# ADR-0015 · La puerta cerrada por defecto

- **Estado:** Propuesta
- **Fecha:** 2026-09-30
- **Concreta:** el modelo de amenazas (pasarela desde un navegador, JSON ambiguo, rutas, arranque) y las invariantes 12 y 13

## Contexto

Hasta ahora cada ruta del proxy comprobaba la clave por su cuenta. Una ruta nueva que se olvidara de hacerlo quedaría abierta. Además quedaban puertas laterales: un JSON con la misma clave dos veces (se revisa una copia y otro programa lee la otra), una web ajena que usa la pasarela desde el navegador de un empleado, un `Host` manipulado (DNS rebinding) y una pasarela desplegada sin clave o con la de `.env.example`.

## Decisión

**Una sola puerta para todas las rutas** (`api/gate.py`, un middleware ASGI puro):

- Toda ruta exige la clave de Antifaz salvo una **lista de permitidas** con coincidencia exacta: hoy solo `/healthz`. `/healthz/`, `//healthz` o `/HEALTHZ` piden clave.
- La decisión se toma sobre `scope["path"]`, la misma ruta ya decodificada que usa el router. Nunca sobre `request.url` ni cabeceras que el cliente puede reescribir (`X-Forwarded-*`). Así `/v1/chat%2Fcompletions` es la misma ruta, con la misma clave y la misma guardia.
- La clave se acepta en `Authorization: Bearer` o en `x-api-key` (en todas las rutas), comparada con `hmac.compare_digest`. Si una de esas cabeceras viene repetida → 401: dos lectores podrían quedarse con copias distintas.
- Orden: **clave (401) → `Origin` (403) → `Content-Type` (415)**. Sin clave no se da ninguna pista sobre las otras reglas.
- No hay rutas WebSocket: cualquier WebSocket se cierra, con clave o sin ella.
- `redirect_slashes=False`: un alias como `/v1/messages/` es un 404, nunca una redirección 307 construida con el `Host` que manda el cliente.
- `/docs`, `/redoc` y `/openapi.json` están **desactivados**: menos superficie pública y nada que decidir sobre si piden clave.

**Peticiones desde un navegador.** Una petición con cabecera `Origin` se rechaza con 403 salvo que ese origen exacto esté en `ANTIFAZ_ALLOWED_ORIGINS` (vacía por defecto; `*` y `null` no se aceptan). Si llega más de un `Origin`, todos tienen que estar permitidos. No hay CORS: ni siquiera un origen permitido recibe cabeceras `Access-Control-*`. Además, toda petición que puede llevar cuerpo (todo salvo `GET`, `HEAD` y `OPTIONS`) debe ser `application/json`, sin más parámetro que `charset=utf-8`. Un formulario o un `text/plain` (que el navegador envía sin preflight) → 415.

**JSON ambiguo.** Al leer el cuerpo, un objeto con dos claves iguales → 400 `invalid_request`. También cuentan como iguales las que solo cambian en mayúsculas o en anchura (se comparan tras NFKC y `casefold`): `content` y `Content`, o `content` con una `ｃ` de ancho completo. Vale para las dos rutas de Anthropic, `count_tokens` incluido, y para OpenAI. El cuerpo que sale hacia el proveedor se reconstruye siempre desde lo revisado (ADR-0013). Los `arguments` de herramientas (JSON dentro de una cadena) no pasan por esta regla: si traen una clave repetida, se quedan con la última, se enmascara esa y se vuelven a serializar, así que la copia descartada no sale.

**Host.** `TrustedHostMiddleware` de Starlette con `ANTIFAZ_ALLOWED_HOSTS` (por defecto `localhost`, `127.0.0.1` y `[::1]`; `*` no se acepta). Un `Host` que no está en la lista → 400.

**Arranque seguro.** `create_app()` se niega a arrancar (`UnsafeConfigError`, con la variable y el motivo, nunca el valor) si:

- falta `ANTIFAZ_API_KEY`, tiene menos de 32 caracteres, empieza por `change-me` (todas las claves de `.env.example` empiezan así) o no es ASCII imprimible sin espacios (viaja en una cabecera HTTP);
- `ANTIFAZ_OPENAI_API_KEY` o `ANTIFAZ_ANTHROPIC_API_KEY` empiezan por `change-me` (si faltan, esa ruta responde 503, como hasta ahora);
- `ANTIFAZ_ALLOWED_HOSTS` está vacía o tiene `*`, o `ANTIFAZ_ALLOWED_ORIGINS` tiene `*` o `null`.

La app ya no se construye al importar el módulo: el servidor la crea con `uvicorn --factory antifaz.api.app:create_app`.

**Claves fuera de logs y respuestas (invariante 13).** El log de cada petición solo escribe la ruta si es una ruta registrada; si no, `-` (una clave o un DNI pegados en la URL acabarían en el log). Si la respuesta del proveedor repite alguna clave configurada (un proveedor descuidado que devuelve las cabeceras que recibió), se descarta con un 502 fijo.

## Alternativas descartadas

- **Dependencia de FastAPI en cada router**: es seguridad registrada ruta a ruta; un router nuevo sin ella quedaría abierto. El middleware se aplica antes del router, a todo.
- **`CORSMiddleware` con una lista vacía**: es código de más para no permitir nada, y un error de configuración abriría la pasarela a cualquier web. Rechazar `Origin` es más simple y cierra por defecto.
- **Mirar `Sec-Fetch-Site`**: solo lo mandan los navegadores modernos; `Origin` basta y el `Content-Type` obligatorio fuerza el preflight que, sin CORS, el navegador no supera.
- **Rechazar solo duplicados exactos**: `content` y `Content` los tratan igual algunos lectores y distinto otros. Cerrar también las variantes de mayúsculas y anchura cuesta una línea. Contrapartida: un esquema de herramienta con dos propiedades que solo cambian en mayúsculas (`id` e `ID`) se rechaza.
- **Dejar `/docs` y `/openapi.json` detrás de la clave**: funcionaría, pero es superficie sin uso en una pasarela; se pueden reactivar en local si hace falta.
- **Aceptar `*` en `ANTIFAZ_ALLOWED_HOSTS` detrás de un proxy inverso**: el proxy puede reenviar un `Host` cualquiera. Se pide la lista de nombres reales (se admiten comodines de subdominio, `*.ejemplo.com`).
- **Limpiar la clave del cuerpo del proveedor en vez de descartarlo**: más código y una respuesta modificada que ya no es la del proveedor. Un 502 fijo es más simple y honesto.

## Consecuencias

- Invariante 12: un test recorre las rutas registradas (con HEAD, OPTIONS, alias y barra final) y comprueba que sin clave dan 401 salvo `/healthz`, y que cada ruta del proxy pasa por la guardia de salida. Una ruta nueva queda cubierta sin tocar el test.
- Invariante 13: un test con claves canario recorre 401, 400, 403, 404, 413, 415, 502, 504 y errores del proveedor, con todos los loggers en `DEBUG`, y comprueba que ninguna clave aparece en logs, cuerpos ni cabeceras.
- **Rompe** despliegues que usaban una clave corta, la de ejemplo o un `Host` distinto de `localhost`: hay que ajustar `.env` (ver `docs/TECNICO.md`).
- Un navegador no puede usar la pasarela salvo que se configure su origen exacto, y aun así sin CORS (sirve para herramientas propias que mandan `Origin`, no para una web pública).
- La ruta de OpenAI acepta ahora la clave también en `x-api-key`.
- Limitación: un proveedor que devuelve la clave **parcialmente** (por ejemplo `sk-...abcd` en su error de clave incorrecta) no se detecta; es su propio formato recortado, no la clave completa.
