# Modelo de amenazas

_Versión inicial (v0.1, issue 1). Se revisa en cada issue que toca una pieza y en la
auditoría final de la v1.0._

## Qué protege Antifaz

Una aplicación envía texto a un proveedor de LLM (OpenAI, Anthropic, Ollama). Antifaz se
pone en medio: detecta datos personales, los cambia por marcadores, comprueba que no sale
nada que la política manda ocultar y, en la respuesta, vuelve a poner los valores.

**Regla de oro:** ningún dato que la política manda ocultar sale hacia un proveedor
externo. Si algo falla al revisar la petición, se bloquea.

## Actores

- **Cliente legítimo:** una aplicación o persona con una clave de Antifaz.
- **Proveedor de LLM:** recibe el texto enmascarado. Se considera "curioso": no debe ver los
  datos que Antifaz detecta.
- **Atacante externo:** sin clave; intenta abusar de la pasarela.
- **Usuario malicioso con clave:** intenta sacar datos de otros usuarios o saltarse la
  revisión (por ejemplo, con inyección de prompt).

## Amenazas y controles

| Activo | Amenaza | Control | Estado |
|---|---|---|---|
| Tabla de marcadores | Fuga por logs, errores o volcados | Vive solo durante la petición. Logs con lista de campos permitidos y filtro de PII. Errores genéricos. | Errores que no repiten el cuerpo: **hecho** (issue 1). Resto: issues 4–5 |
| Datos entre usuarios | Una inyección de prompt pide "imprime [[ES_DNI_1]]" | Sin estado compartido (ADR-0004). Solo se restauran marcadores emitidos en la propia petición. | Issues 4–5 |
| Claves de proveedores | Exposición | Solo en el servidor (secretos de Docker o `.env`). Nunca en respuestas ni logs; una respuesta del proveedor que repite una clave se descarta (502). | Issue 5; test de claves canario (invariante 13): **hecho** (issue 20) |
| Claves de Antifaz | Uso indebido | v0.1: una clave desde `.env`. v0.2: claves virtuales guardadas como HMAC con pepper, con alcance y límites. | Issues 5 y 8 |
| Destino | SSRF | Destinos fijos en la configuración; nunca una URL del cliente. | Issue 5 |
| Disponibilidad | DoS, ReDoS, historiales enormes | Tamaño máximo de petición, pool de procesos con tiempo máximo para el NER, `re2`, caché de spans, límites por clave. | Issues 2, 5, 6 y 8 |
| Fallo del enmascarador | Un dato sale al proveedor | Guardia de salida sobre los bytes finales + cierre por defecto. | Issue 4 |
| Evidencias | Manipulación | Cadena de hashes, sin UPDATE/DELETE, puntos de control firmados. | Issue 9 |
| Pasarela desde un navegador | Una web ajena usa la pasarela desde el navegador de un empleado | `Origin` rechazado (403) salvo origen exacto configurado, `Content-Type: application/json` obligatorio (415), sin CORS (ADR-0015). | **Hecho** (issue 20) |
| JSON ambiguo | Clave duplicada (`content` dos veces, o `content` y `Content`): se revisa una copia y se envía otra | Claves repetidas, también si solo cambian en mayúsculas o anchura → 400. El cuerpo enviado se reconstruye desde lo revisado. | **Hecho** (issue 20) |
| Rutas | `Host` manipulado (DNS rebinding) o ruta nueva sin protección | `TrustedHostMiddleware`; clave exigida por un único middleware para toda ruta salvo `/healthz` exacto; sin `/docs` ni redirecciones de barra; test que recorre las rutas registradas (invariante 12). | **Hecho** (issue 20) |
| Arranque | Pasarela desplegada sin clave, con una clave corta o con la de `.env.example` | Se niega a arrancar y dice qué variable falla, sin mostrar su valor. | **Hecho** (issue 20) |
| `.env` generado y sus copias | Una clave de proveedor en el historial de la shell, en `ps` o en un log de CI; un `.env` legible por otros; una copia que pisa a otra; un `.env` a medio escribir | `antifaz init` (ADR-0017): claves solo por `getpass`, variable de entorno nombrada o stdin, nunca por argumentos; la clave de Antifaz se muestra una vez y solo en un terminal (o con `--show-key`); temporal `0600` con `O_EXCL`, validado con `check_safe_to_start` leyendo solo el archivo, y `os.replace`; copia `.env.bak-…` con `O_EXCL` y `0600` que nunca pisa otra; mensajes sin valores. En Windows, permisos heredados de la carpeta del usuario (documentado). En Docker, `--rm` para que la clave no quede en `docker logs`. Las copias guardan claves antiguas: se borran tras rotar (documentado). | **Hecho** (issue 41) |
| Panel | Acceso indebido | Puerta propia en `/panel*` (la clave de la API no la abre y la sesión del panel no abre `/v1/*`); sin `ANTIFAZ_ADMIN_TOKEN`, 404 y sin cargar nada. Solo HTTPS o `localhost` (si no, página fija 403 y sin cookie). Login con el token de admin comparado por huella SHA-256 y `hmac.compare_digest`, cuerpo de 1 KiB como mucho, 5 fallos por minuto por IP antes de comparar (429), mismo 401 para todo fallo. Sesión nueva en cada login (sin fijación), guardada como huella; cookie por esquema (`__Host-` y `Secure` por HTTPS, `Path=/panel` en `localhost`), `HttpOnly`, `SameSite=Strict`, y solo vale la del esquema de la petición. CSRF en la puerta para todo lo que no es `GET` o `HEAD`: capa 3 (`Sec-Fetch-Site` y `Origin`) y solo JSON antes de mirar la sesión, sesión viva del esquema y token CSRF de la sesión en `X-Antifaz-CSRF` (aleatorio propio, no derivado del id, solo en el `<meta>` de una página `no-store`); el login es la única excepción a la capa del token. La sesión queda ligada a su esquema: presentada por el otro, se rechaza y se borra. Cierre de sesión real (`POST /panel/logout`): borra la sesión del servidor, caduca solo la cookie del esquema y envía `Clear-Site-Data: "cache"`. CSP estricta y cabeceras de seguridad en toda respuesta de la puerta, también en sus 500 (ADR-0018). Cuerpo del login con plazo de 5 s; desconexiones y cuerpos lentos no cuentan. | Hecho (11b-2a + 11b-2b) |
| Otra app u otra instancia en otro puerto de `localhost` | Las cookies no se separan por puerto y `SameSite` tampoco: otra instancia pisa la sesión, otra app puede poner una cookie con el mismo nombre o enviar peticiones «del mismo sitio» | Solo vale la cookie del esquema y, si su nombre llega dos veces, no hay sesión. `Origin` comparado con el puerto y `Sec-Fetch-Site: same-origin` en todo lo que no es `GET` o `HEAD`, y además el token CSRF de la sesión en todo salvo el login. `Path=/panel` en `localhost`. Documentado: dos instancias se pisan la sesión. | Hecho (11b-2a + 11b-2b) |
| Cadena de suministro | Dependencia o acción de CI comprometida; licencia incompatible | Acciones fijadas por SHA con permisos mínimos, `uv audit`, CodeQL, gitleaks, comprobación de licencias, Dependabot. | **Hecho** (issue 1) |
| Sesiones de desarrollo con IA | El asistente lee secretos o se salta controles | Hooks de `.claude/` que bloquean leer `.env`, imprimir variables secretas, `--no-verify`, force push y escanear secretos sin `--redact` o sobre el árbol de trabajo, con tests. gitleaks solo analiza el historial de git y siempre con `--redact` (Makefile, pre-commit y CI). **Defensa en profundidad, no barrera:** quien puede ejecutar código arbitrario puede saltárselos (una lista de patrones nunca es completa). La barrera real son gitleaks en CI y que las claves no están en el repo. | Hecho como defensa en profundidad (issue 1): los 7 caminos de la revisión de privacidad están bloqueados y probados, y el hook vigila también PowerShell |

## Fuera del modelo

- **Datos que el detector no encuentra.** Ningún detector es perfecto; el benchmark mide
  cuánto se escapa.
- **Inferencia por contexto.** El LLM puede deducir atributos personales de lo que queda
  sin enmascarar.
- **Quien controla a la vez el servidor y sus claves.** Puede leer todo; las evidencias
  detectan cambios, pero no le protegen de sí mismo.

## Referencias

- Autoevaluación frente a OWASP ASVS 5.0 nivel 2: `docs/security/asvs-l2.md` (a partir de
  la v0.2).
- OWASP Top 10 for LLM Applications 2025: LLM02, LLM05, LLM07 y LLM10.
