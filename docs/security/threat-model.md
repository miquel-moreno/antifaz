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
| Claves de proveedores | Exposición | Solo en el servidor (secretos de Docker o `.env`). Nunca en respuestas ni logs. | Issue 5 |
| Claves de Antifaz | Uso indebido | v0.1: una clave desde `.env`. v0.2: claves virtuales guardadas como HMAC con pepper, con alcance y límites. | Issues 5 y 8 |
| Destino | SSRF | Destinos fijos en la configuración; nunca una URL del cliente. | Issue 5 |
| Disponibilidad | DoS, ReDoS, historiales enormes | Tamaño máximo de petición, pool de procesos con tiempo máximo para el NER, `re2`, caché de spans, límites por clave. | Issues 2, 5, 6 y 8 |
| Fallo del enmascarador | Un dato sale al proveedor | Guardia de salida sobre los bytes finales + cierre por defecto. | Issue 4 |
| Evidencias | Manipulación | Cadena de hashes, sin UPDATE/DELETE, puntos de control firmados. | Issue 9 |
| Panel | Acceso indebido | Token de admin, cookies `HttpOnly` y `SameSite=Strict`, CSRF, CSP. | Issue 11 |
| Cadena de suministro | Dependencia o acción de CI comprometida; licencia incompatible | Acciones fijadas por SHA con permisos mínimos, `uv audit`, CodeQL, gitleaks, comprobación de licencias, Dependabot. | **Hecho** (issue 1) |
| Sesiones de desarrollo con IA | El asistente lee secretos o se salta controles | Hooks de `.claude/` que bloquean leer `.env`, imprimir variables secretas, `--no-verify` y force push, con tests. **Defensa en profundidad, no barrera:** quien puede ejecutar código arbitrario puede saltárselos (una lista de patrones nunca es completa). La barrera real son gitleaks en CI y que las claves no están en el repo. | Hecho como defensa en profundidad (issue 1): los 7 caminos de la revisión de privacidad están bloqueados y probados, y el hook vigila también PowerShell |

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
