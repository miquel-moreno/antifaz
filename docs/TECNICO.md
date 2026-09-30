# Detalles técnicos

> Antifaz está en desarrollo (v0.1). Esta página crece con cada issue.

## Ponerlo en marcha (desarrollo)

```bash
make install   # dependencias + hooks de pre-commit
make check     # lint + tipos + tests (también los de contrato) + gitleaks + zizmor
make contract  # solo los tests de contrato: SDK oficiales contra Antifaz, sin red
make audit     # vulnerabilidades conocidas en las dependencias (uv audit, experimental)
make licenses  # licencias de lo que se distribuye
make dev       # API en http://localhost:8000 (uvicorn --factory; necesita .env, ver abajo)
```

`docker compose up` llega en el issue 7.

## Arquitectura

Una pieza por responsabilidad, cada una en su paquete de `src/antifaz/`:

| Pieza | Paquete | Hace |
|---|---|---|
| Puerta | `api/` | Endpoints, auth, límites, `request_id`; errores que nunca repiten el cuerpo recibido |
| Detector | `detect/` | Validadores con dígito de control, patrones, NER, diccionarios |
| Política | `policy/` | Qué hacer con cada tipo de dato: `mask`, `surrogate`, `block`, `allow`, `route_local` |
| Tabla de marcadores | `vault/` | Valor ↔ marcador, solo durante la petición |
| Enmascarador | `mask/` | Cambia los valores por marcadores |
| Guardia de salida | `guard/` | Segunda comprobación sobre los bytes finales; si encuentra algo, bloquea |
| Enrutador | `providers/` | OpenAI, Anthropic, Ollama; destinos fijos |
| Restaurador | `restore/` | Vuelve a poner los valores, también en streaming |
| Evidencias | `audit/` | Registro encadenado sin datos personales (v0.2) |

El docstring de cada paquete dice qué hace y qué tiene prohibido. Las decisiones (aceptadas) están en [`docs/adr/`](adr/README.md) y las amenazas en [`docs/security/threat-model.md`](security/threat-model.md).

## Detector: identificadores con dígito de control (issue 2, PR 2a)

`antifaz.detect.scan(text)` devuelve la lista de spans `(start, end, type, layer, confidence)`: dónde hay un dato y de qué tipo, **nunca el valor** (ADR-0009).

| Tipo | Validación | Fuente |
|---|---|---|
| `ES_DNI` | 8 cifras + letra `TRWAGMYFPDXBNJZSQVHLCKE[n mod 23]` | Ministerio del Interior |
| `ES_NIE` | X/Y/Z → 0/1/2 + regla del DNI | Ministerio del Interior |
| `ES_NIF` (K/L/M) | 7 cifras + letra del DNI | RD 1065/2007 (sin algoritmo publicado; como python-stdnum) |
| `ES_CIF` | Luhn sobre las 7 cifras, como dígito o como letra `JABCDEFGHI` (ambas formas) | Orden EHA/451/2008 (sin algoritmo); ADR-0009 |
| `ES_NSS` | mod 97; si el número < 10^7, `(número + provincia·10^7) mod 97` | Sin fuente oficial de la TGSS; vectores en `tests/data/nss_vectors.md` |
| `ES_CCC` | Dos dígitos mod 11, pesos 1, 2, 4, 8, 5, 10, 9, 7, 3, 6 | AEB (2001) |
| `IBAN` | ISO 13616 mod 97 + estructura del país; los ES exigen además un CCC válido | Registro IBAN (vía python-stdnum) |
| `IT_CODICE_FISCALE`, `EU_VAT` | python-stdnum | — |

**Invariante 10:** en `tests/property/`, cada validador español se compara con python-stdnum en 5.000 casos generados (una parte con el control correcto calculado por stdnum, para probar también el lado "válido"). El NSS, que stdnum no tiene, se prueba con vectores documentados y con la propiedad "cambiar cualquier cifra lo invalida".

**En el texto:** cada patrón solo propone candidatos; un span se devuelve si su validador lo acepta. Los patrones siguen el ADR-0008 (repeticiones acotadas, cuantificadores posesivos, límites que impiden encontrar un DNI dentro de una tira más larga de cifras; ver ADR-0014 para los tipos que pueden tocar letras) y hay pruebas con textos maliciosos de 50.000 caracteres. El IBAN se corta a la longitud de su país, sacada del registro IBAN, y se busca avanzando carácter a carácter tras un candidato falso, para que ningún candidato tape a otro IBAN. Se aceptan mayúsculas y minúsculas y los separadores habituales (espacio, punto, guion; barra en el NSS). La resolución de solapamientos es O(n log n): un texto con 20.000 DNI se resuelve en menos de 2 s.

### Patrones con contexto (issue 2, PR 2b)

Para los datos sin dígito de control, `patterns/personal.py` busca la forma del dato y, en los más ambiguos, una palabra clave en los 40 caracteres anteriores (`patterns/context.py`, con límites de palabra reales: "hotel" no cuenta como "tel").

| Tipo | Cómo se detecta | Contexto | Confianza |
|---|---|---|---|
| `EMAIL` | Caracteres de RFC 5322 y letras Unicode (RFC 6531) en la parte local; dominio con TLD de 2+ letras o `xn--` | No | Alta |
| `IP` | IPv4 con octetos 0–255, no dentro de una serie más larga | No | Media |
| `PHONE` | España: `+34`/`0034`/`(+34)` o `34` pegado opcional, empieza por 6, 7, 8 o 9, 9 cifras en bloques con el mismo separador. Cada serie de cifras se parte en bloques y los teléfonos se montan bloque a bloque, así que un teléfono junto a otro número también se encuentra. Excluye números de empresa (800, 900, 901, 902, 905) y de tarificación adicional (803, 806, 807; CNMC) | Sube a alta con "tel", "teléfono", "móvil", "llamar", "WhatsApp" | Media / alta |
| `CREDIT_CARD` | 13–19 cifras (separador espacio, guion o punto), Luhn y prefijo conocido (Visa, Mastercard, Amex, Discover, JCB, Diners, UnionPay). No empieza en mitad de un número agrupado de 3+ cifras (evita leer parte de un teléfono como tarjeta) | No | Alta (validador) |
| `ES_PASSPORT` | 3 letras + 6 cifras (formato observado, sin norma citada) | Obligatorio ("pasaporte"…) | Media |
| `ES_PLATE` | 4 cifras + 3 consonantes sin vocales, Ñ ni Q (anexo XVIII del RD 2822/1998) | Obligatorio ("matrícula", "coche"…) | Media |
| `DATE_OF_BIRTH` | dd/mm/aaaa (también con - o . y año de 2 cifras), ISO aaaa-mm-dd, "12 de marzo de 1985", "12 marzo 1985", "1 de marzo del 1985"; fecha real | Obligatorio ("nacido", "F. nac."…) | Media |
| `ADDRESS` | Tipo de vía (castellano, catalán y gallego, con abreviaturas) + nombre + número (`nº`, `n.º`, `s/n`) + piso/puerta + CP 01000–52999 opcionales. Con tipos inequívocos (C/, Calle, Carrer, Avda…) el nombre puede ir en minúsculas; con tipos que también son palabras comunes (Camino, Ronda, Paseo, "C.") debe empezar por mayúscula | No | Media; alta con CP |
| `PT_NIF`, `FR_NIR`, `DE_IDNR` | Solo cifras, validados con python-stdnum | Obligatorio, del propio país | Alta (validador) |

Los identificadores de la UE que son solo cifras necesitan contexto porque, sin él, un NIF portugués de 9 cifras es indistinguible de un teléfono español.

**Solapamientos (ADR-0010):** si una detección contiene por completo a otra, gana la que contiene; así un email con un DNI dentro se enmascara entero, no solo el DNI.

### Normalización previa y formatos raros (ADR-0014, red team ronda 1)

Antes de buscar, `detect/normalize.py` crea una vista normalizada del texto con un mapa de posiciones; el texto original no se toca:

- Se quitan los caracteres de formato invisibles (categoría Unicode Cf: espacio de ancho cero, ZWNJ/ZWJ, guion blando, BOM, word joiner, marcas de dirección…), las marcas combinantes (Mn: acentos escritos aparte, selectores de variante) y los rellenos hangul.
- Los caracteres cuyo NFKC es **una** letra o cifra ASCII pasan a ese carácter (cifras y letras de ancho completo, letras matemáticas, superíndices). Los ordinales `º` y `ª` se quedan como están (los usan las direcciones).
- Tabulador, espacio duro (NBSP) y los demás espacios Unicode pasan a un espacio normal. Los saltos de línea se conservan.
- Homoglifos: las letras cirílicas `А В Е К М Н О Р С Т Х У З` y griegas `Α Β Ε Ζ Η Ι Κ Μ Ν Ο Ρ Τ Υ Χ` (y sus minúsculas) pasan a la latina que imitan (tabla `HOMOGLYPHS`; `З` y `Ζ` → `Z`).

Cada carácter se sustituye por uno o se elimina, así que las posiciones se devuelven al original con un mapa monótono (bisección sobre los caracteres eliminados): el marcador cubre los caracteres originales, incluidos los invisibles que haya **dentro** del valor, y `restore()` devuelve el texto exacto. Un texto ASCII sin tabuladores no se copia.

**Separadores:** DNI, NIE y NIF K/L/M aceptan un solo espacio, punto o guion entre grupos (`12 345 678 Z`, `12.345.678-Z`, `X 1 234 567 L`) y **un** salto de línea (`\n` o `\r\n`) en cualquier punto de las cifras o antes de la letra. El IBAN acepta un salto de línea entre grupos. Los recuentos siguen siendo exactos (8 o 7 cifras) y la letra o el mod 97 siguen siendo obligatorios; dos saltos de línea o dos espacios seguidos no valen.

**Límites relajados para los tipos con control fuerte:** DNI, NIE, NIF K/L/M e IBAN pueden ir pegados a letras (`DNI12345678Z`, `NIEX1234567L`, `ref abc12345678Zxyz`, `IBANES91…`), pero nunca a cifras: un DNI dentro de una tira más larga de cifras sigue sin ser un DNI. Tampoco se relaja dentro de una tira toda hexadecimal de 16 caracteres o más (hashes): con 20.000 valores aleatorios, 0 falsos DNI en SHA-1 y SHA-256 y 2 en UUID v4 (sus grupos tienen como mucho 12 caracteres; coste aceptado, se enmascara de más). Si hay separador antes de la letra de control, la letra no puede ir seguida de otra letra (en "12345678 casas" la "c" no es la letra de control). CIF, NSS, CCC, codice fiscale y NIF-IVA mantienen los límites estrictos (controles más débiles; "DNI és 16257107-V" sigue sin ser un CIF).

**Cuerpos muy anidados:** un JSON con más de 100 niveles de objetos o listas se bloquea (`NestingTooDeep`, 400 `antifaz_blocked`) antes de cualquier recorrido recursivo; la comprobación de adjuntos es iterativa. Una respuesta del proveedor igual de profunda da un 502 `bad_upstream_response` fijo. Un `arguments` con JSON demasiado profundo se trata como texto y se enmascara como tal.

**Limitaciones conocidas:**
- Cifras de otros sistemas de numeración (árabes, devanagari…) no se convierten: su NFKC no es ASCII. Solo se cubren los homoglifos de la tabla; otros alfabetos parecidos (armenio, cherokee…) no.
- Un valor partido entre dos cadenas JSON distintas, o codificado (base64, hexadecimal, URL, HTML, al revés, rot13), no se detecta (tests `xfail` del red team).
- Las direcciones se buscan con un patrón al estilo español (tipo de vía y nombre con mayúscula); las que no siguen ese formato quedan para el NER (issue 6). La ciudad no se incluye.
- IPv6 y pasaportes de otros países, fuera de alcance por ahora.
- El contexto solo se busca **antes** del valor y en 40 caracteres: no sirve una cabecera de CSV ni una palabra clave detrás ("AAB123456 (pasaporte)"). Propuesto como issue aparte.
- No se detectan: fechas con solo el año ("nacido en 1985"), matrículas provinciales antiguas (B-1234-XY), emails ofuscados ("juan (at) ejemplo.es"), teléfonos con doble espacio o con el prefijo entre paréntesis ("(93) 123 45 67"), direcciones en euskera con el tipo detrás ("Nagusia kalea 3") ni NIR corsos (2A/2B).
- Algunos números normales se detectan como teléfono o IP con confianza media ("700000000 unidades", "versión 1.2.3.4"): la política decide qué hacer con la confianza media.
- Controles débiles implican falsos positivos: NSS 1/97, CCC 1/121, Luhn 1/10. Es un fallo seguro (se enmascara de más).
- "CCC" aquí es la cuenta bancaria, no el código de cuenta de cotización de la Seguridad Social.
- Faker genera NSS con otra fórmula cuando el número empieza por 0: no sirve de referencia (se tendrá en cuenta en el benchmark).

## Librería: `mask()` y `restore()` (issue 4, PR 4a)

```python
from antifaz import mask, restore

result = mask(["Mi DNI es 12345678Z", "¿y el 12345678Z?"])  # también acepta un solo str
result.texts  # ("Mi DNI es [[ES_DNI_1]]", "¿y el [[ES_DNI_1]]?")
result.hidden  # (EntityType.ES_DNI,)
restore(respuesta_del_modelo, result.vault)
```

- Marcadores `[[TIPO_N]]`, numerados por tipo y por primera aparición en todos los textos; el mismo par (tipo, texto exacto) recibe siempre el mismo marcador ([ADR-0012](adr/0012-escape-and-same-value.md), propuesta).
- Los `[[` que ya escribe el usuario se escapan con un `!` detrás; `restore()` lo quita en una sola pasada y solo restaura marcadores de **esta** petición. Hypothesis comprueba `restore(mask(x)) == x`.
- Política por defecto (`DEFAULT_POLICY`, versión `builtin-1`): enmascara todos los tipos salvo el CIF de empresa.
- Si el detector falla o devuelve spans mal formados: `DetectorFailed`, con un mensaje fijo sin el texto. Nunca sale texto en claro.
- `Vault` no se puede imprimir (solo `Vault(entries=N)`), recorrer, serializar ni copiar. Protege contra fugas **accidentales** (logs, `repr`, pickle), no contra código del mismo proceso que lea sus atributos privados.
- Cada respuesta se restaura solo con la tabla de **su** petición: todas numeran desde 1, así que con la tabla de otra petición `[[ES_DNI_1]]` daría el DNI de otra persona. La pasarela lo garantiza en el issue 5.
- El restaurador solo reconoce marcadores en ASCII: parecidos como `[[ıp_1]]` se dejan tal cual.

## Guardia de salida y CLI (issue 4, PR 4b)

```python
from antifaz.guard import check

check(cuerpo_final_en_bytes, result.vault)  # lanza EgressBlocked si ve un valor oculto
```

- Si el cuerpo es JSON, revisa cada cadena, clave y número decodificados (así ve los escapes Unicode de JSON: una barra invertida seguida de `u0031` es el dígito 1); si no lo es, el texto crudo. Bytes que no son UTF-8 válido, o un JSON demasiado profundo para revisarlo: bloquea.
- Normaliza igual el valor y el texto: NFKC (cifras de ancho completo), sin caracteres de formato Cf (espacio de ancho cero, guion blando), sin acentos (NFD sin marcas Mn, así `İbrahim` = `ibrahim`), `casefold()` y espacios seguidos reducidos a uno.
- Valores con 6 o más letras y cifras: se comparan quitando **todo** lo que no es letra ni cifra y **sin límites de palabra**, así que `DNI12345678Z`, `X12345678ZY`, `12.345.678-Z` o `xana@example.com` bloquean.
- Valores más cortos: con límites alfanuméricos (ni letra ni cifra justo antes o después), para que "Ana" no salte en "semana".
- Se normaliza cada texto una sola vez: 1 MB de JSON con 200 valores ocultos se revisa en unos 0,04 s si es ASCII y unos 0,35 s con acentos (test con límite de 2 s).
- **Un falso positivo bloquea**: la guardia falla cerrada a propósito. No tiene opción para desactivarla.
- `EgressBlocked` lleva un mensaje fijo, sin valores y sin `__context__`.

Limitaciones de la guardia (es una **segunda capa**, no un DLP completo):

- No decodifica otras codificaciones: base64, URL (`%31`), entidades HTML, hexadecimal ni el valor escrito al revés.
- No ve un valor partido entre dos cadenas JSON distintas.
- Solo busca el valor tal como se detectó: un DNI copiado sin la letra, o un teléfono oculto con prefijo (`0034612345678`) que luego aparece sin él, pueden pasar. Al revés sí se detecta: el teléfono oculto sin prefijo bloquea aunque aparezca con `0034` pegado.
- Los falsos positivos bloquean la petición, por diseño.
- Invariante 2 (`tests/property/test_egress_invariant.py`): **sin llamar a la guardia**, un proveedor falso que guarda los bytes no recibe ningún valor oculto, ni tal cual, ni normalizado ni compactado, con `ensure_ascii` activado y desactivado.

CLI (`uv run antifaz ...`):

```bash
antifaz scan fichero.txt   # una línea por detección: TIPO inicio fin (nunca el valor)
antifaz mask fichero.txt   # el texto con marcadores (nunca la tabla)
antifaz mask -             # lee de stdin
antifaz verify             # planta datos falsos con tu configuración (ver "antifaz verify")
```

Si no puede leer el fichero como UTF-8 o el detector falla: mensaje genérico en stderr y código 2, sin repetir el contenido.

## Proxy compatible con OpenAI Chat (issue 5, PR 5a)

Configura `.env` a partir de `.env.example`: `ANTIFAZ_API_KEY` (la clave que usan tus clientes), `ANTIFAZ_OPENAI_API_KEY` y, si quieres otro servidor compatible, `ANTIFAZ_OPENAI_BASE_URL`. Todas las variables de Antifaz empiezan por `ANTIFAZ_`: así no se mezclan con las que usan Claude Code o los SDK en la misma terminal (si no, Antifaz podría llamarse a sí mismo y enviar su propia clave). Sin `ANTIFAZ_API_KEY` válida, Antifaz no arranca; sin la clave del proveedor, esta ruta responde 503 (ver [La puerta](#la-puerta-cerrada-por-defecto-issue-20)).

```bash
curl http://localhost:8000/v1/chat/completions   -H "Authorization: Bearer $ANTIFAZ_API_KEY" -H "Content-Type: application/json"   -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Mi DNI es 12345678Z"}]}'
```

Con el SDK de OpenAI basta con `base_url="http://localhost:8000/v1"` y `api_key=<ANTIFAZ_API_KEY>`.

Qué hace con cada petición ([ADR-0013](adr/0013-proxy.md), aceptada):

1. La puerta ya comprobó la clave de Antifaz, el `Origin` y el `Content-Type` (ver [La puerta](#la-puerta-cerrada-por-defecto-issue-20)). Esa clave **nunca** llega al proveedor; tampoco ninguna otra cabecera del cliente.
2. Lee el cuerpo con un tope (`ANTIFAZ_MAX_BODY_BYTES`, 4 MiB por defecto): más grande → 413; no es un objeto JSON en UTF-8 → 400.
3. Reúne **todas** las cadenas del cuerpo (mensajes, partes de texto, `name`, resultados de herramientas y cualquier campo nuevo) y llama a `mask()` una sola vez. Los `arguments` de las llamadas a herramientas se parsean como JSON y se enmascaran sus valores. Las claves de los objetos no se cambian: si alguna contiene un dato, la petición se bloquea.
4. Solo pasan partes de tipo `text`, `refusal` o `function`: imágenes, documentos, audio, ficheros, `file_id`, claves como `source` o `data`, o un texto con una URL `data:...;base64,` → 400 `antifaz_blocked`. Los números pasan por el detector: si uno es un dato (un teléfono escrito como número), se bloquea. `NaN`/`Infinity` o un `stream` que no sea booleano → 400.
5. Serializa una vez y la guardia de salida revisa **esos mismos bytes** justo antes de enviarlos.
6. Envía a `ANTIFAZ_OPENAI_BASE_URL` con `ANTIFAZ_OPENAI_API_KEY`. La URL nunca sale del cliente.
7. Restaura `content`, `refusal` y los `arguments` de las herramientas (parseando el JSON, así siguen siendo JSON válido aunque el dato tenga comillas). El resto de campos pasa sin tocar.

Errores:

| Caso | Respuesta |
|---|---|
| Sin clave, clave incorrecta, cabecera de clave repetida o `Authorization` y `x-api-key` en conflicto | 401 `unauthorized` |
| Cabecera `Origin` no permitida | 403 `origin_not_allowed` |
| `Content-Type` que no es `application/json` (UTF-8) | 415 `unsupported_media_type` |
| Claves repetidas en el JSON (también si solo cambian en mayúsculas y es una clave que Antifaz lee) | 400 `invalid_request` |
| `Host` que no está en `ANTIFAZ_ALLOWED_HOSTS` | 400 `Invalid host header` (texto) |
| La respuesta del proveedor repite una clave configurada (también escapada en JSON) | 502 `bad_upstream_response` |
| Dato en un sitio que no se puede enmascarar, adjunto, detector roto o guardia | 400 `antifaz_blocked`, mensaje fijo |
| `stream: true` | La respuesta llega en streaming, restaurada al vuelo (ver [Streaming](#streaming-issue-5-pr-5c)) |
| `stream_options` con algo que no sea `include_usage` o `include_obfuscation` a `true`/`false` | 400 `invalid_request` |
| El proveedor tarda más de `ANTIFAZ_UPSTREAM_TIMEOUT_SECONDS` | 504 `upstream_timeout` |
| No se puede conectar | 502 `upstream_unavailable` |
| El proveedor responde con una redirección (3xx) | 502 `upstream_redirect` (no se sigue) |
| El proveedor responde con error (4xx/5xx) | Su cuerpo **tal cual**: solo vio texto enmascarado, así que puede mostrar marcadores `[[TIPO_N]]` |

Ningún log escribe cuerpos, cabeceras ni claves; `httpx` y `httpcore` quedan en `WARNING`. Un test (invariante 8) pasa un DNI centinela por respuestas, errores del proveedor, tiempos agotados y bloqueos de la guardia, con todos los loggers en `DEBUG`, y comprueba que no aparece ni en los logs ni en los cuerpos de error. Otro (invariante 2) quita la guardia **solo en el test** y comprueba que el proveedor falso no recibe ningún valor oculto, tampoco en argumentos ni resultados de herramientas.

## Proxy de Anthropic Messages (issue 5, PR 5b)

Configura `ANTIFAZ_API_KEY` y `ANTIFAZ_ANTHROPIC_API_KEY` en `.env` (y `ANTIFAZ_ANTHROPIC_BASE_URL` solo si usas otro servidor; va **sin** `/v1`). Sin la clave de Anthropic, estas rutas responden 503.

```bash
curl http://localhost:8000/v1/messages   -H "x-api-key: $ANTIFAZ_API_KEY" -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json"   -d '{"model": "claude-sonnet-4-5", "max_tokens": 200, "messages": [{"role": "user", "content": "Mi DNI es 12345678Z"}]}'
```

Con el SDK de Anthropic: `base_url="http://localhost:8000"` y `api_key=<ANTIFAZ_API_KEY>`. **Claude Code**: `ANTHROPIC_BASE_URL=http://localhost:8000` y `ANTHROPIC_API_KEY=<ANTIFAZ_API_KEY>` (o `ANTHROPIC_AUTH_TOKEN`, que llega como `Bearer`). Claude Code usa streaming, soportado desde la parte 5c (probado con el proveedor falso; la prueba documentada con Claude Code de verdad sigue pendiente). Antifaz no lee esas variables (solo las que empiezan por `ANTIFAZ_`), así que se puede arrancar en la misma terminal.

Rutas: `POST /v1/messages` y `POST /v1/messages/count_tokens`. Los pasos son los mismos que en OpenAI (clave, tope de tamaño, un solo `mask()`, una sola serialización revisada por la guardia, errores); el código de esos pasos es compartido (`api/proxy.py` y `providers/json_walk.py`). Lo propio de Anthropic:

1. **Clave**: en `x-api-key` (lo que envían Claude Code y el SDK) o en `Authorization: Bearer`, comprobada por la puerta como en todas las rutas. Al proveedor solo le llega `x-api-key` con `ANTIFAZ_ANTHROPIC_API_KEY`.
2. **Cabeceras**: solo se reenvían `anthropic-version` y `anthropic-beta`, y solo si su valor son letras, dígitos, `.`, `_`, `-` y comas (hasta 200 caracteres); si no → 400 `invalid_header`. Si el cliente no manda `anthropic-version`, Antifaz no se la inventa (el proveedor devolverá su error).
3. **Bloques permitidos** en `messages[].content`: `text`, `tool_use`, `tool_result`, `thinking` y `redacted_thinking`. En `system` y dentro de `tool_result.content`, solo `text`. Cualquier otro (`image`, `document`, `search_result`, `container_upload`, `server_tool_use`, resultados de herramientas del servidor, `citations` o uno nuevo) → 400 `antifaz_blocked`. `cache_control` no pasa por la lista. La `input` de `tool_use` puede tener cualquier `type` (son datos para la herramienta), pero las claves de adjunto (`source`, `data`, `file_id`…) y las URL `data:...;base64,` se bloquean también ahí. Ajustes con claves fijas: `thinking` solo `type` y `budget_tokens`; `tool_choice` solo `type`, `name` y `disable_parallel_tool_use`; otra clave → bloqueo. En `tools` no hay lista de tipos (hay herramientas del servidor como `web_search_20250305`), pero fuera de `input_schema` las claves de adjunto bloquean; dentro de `input_schema` no, porque una propiedad puede llamarse `data` o `file`, y solo se bloquea un objeto con `"type": "base64"`, que no es un tipo de JSON Schema (más las URL `data:` que se bloquean en todo el cuerpo). Todos sus textos se enmascaran. `metadata.user_id` y cualquier campo nuevo se enmascaran como texto.
4. **Razonamiento (invariante 9)**: los bloques `thinking` y `redacted_thinking` de los mensajes se copian **sin tocar**, con su `signature` o `data`: no se enmascaran ni se escapan (sus `[[` siguen igual). Vienen del modelo, que solo vio texto enmascarado, así que llevan marcadores, no datos. Aun así se pasan por el detector: si uno lleva un dato que hay que ocultar (alguien lo editó), la petición se bloquea, porque no se puede enmascarar sin romper la firma. La guardia sigue revisando todos los bytes. Solo se aceptan sus claves exactas (`thinking`: `type`, `thinking`, `signature`; `redacted_thinking`: `type`, `data`), todas de texto; otra clave → bloqueo. `signature` y `data` son opacos (firma y razonamiento cifrado) y no se pueden revisar: riesgo aceptado en el ADR-0013. Los marcadores que ya lleva el razonamiento (de un turno anterior) se **reservan**: un dato nuevo de esta petición no recibe ese número (si `[[ES_DNI_1]]` está en el razonamiento, el DNI nuevo es `[[ES_DNI_2]]`), y como los reservados no están en la tabla, la respuesta los deja tal cual en vez de poner un valor equivocado. Un bloque con forma de `thinking` dentro de la `input` de una herramienta no cuenta: se enmascara como cualquier dato.
5. **Respuesta**: se restauran los bloques `text` y los textos de `tool_use.input` (los valores, no las claves). `thinking`, `redacted_thinking`, las firmas y los bloques desconocidos pasan sin tocar.
6. **`count_tokens`**: se enmascara igual que `/v1/messages` y la respuesta del proveedor (solo números) se devuelve tal cual. `stream` se comprueba igual: no booleano → 400; `true` → 400 `streaming_not_supported`, por coherencia (`count_tokens` no tiene streaming).
7. `stream: true` → la respuesta llega en streaming (ver [Streaming](#streaming-issue-5-pr-5c)). El código de estado 2xx del proveedor se devuelve igual (en las dos rutas). Un texto con un sustituto suelto de UTF-16 (`"\ud800"`), que no se puede enviar en UTF-8, → 400 `invalid_request`.

"Sin tocar" quiere decir que las cadenas son idénticas: el proxy trabaja con el JSON parseado, así que el formato (espacios, escapes `é`) puede cambiar al volver a serializar, pero no el contenido ni la firma. Los tests comparan el bloque serializado dentro de los bytes enviados y de la respuesta.

## Streaming (issue 5, PR 5c)

Con `"stream": true` en `/v1/chat/completions` o en `/v1/messages`, la respuesta llega como eventos SSE (`text/event-stream`) y Antifaz pone los datos en su sitio **al vuelo**. La petición se trata igual que sin streaming: un solo `mask()`, y la guardia revisa los bytes exactos justo antes de enviarlos. Si el proveedor responde con error (429, 5xx…), se devuelve su cuerpo tal cual, como en 5a y 5b (después de comprobar que no repite una clave). `count_tokens` no tiene streaming: `stream: true` ahí → 400 `streaming_not_supported`.

Cómo se restaura ([ADR-0013](adr/0013-proxy.md)):

- **Texto**: cada trozo sale en cuanto llega, salvo el final que **podría** ser el principio de un marcador o de un escape (`[`, `[[`, `[[ES_D`, `[[ es_dni_1 `…), con el mismo patrón que `restore()`. Solo se retiene lo que todavía puede acabar siendo un marcador **de esta petición**: `[[ES_D` se retiene si esta petición emitió `[[ES_DNI_1]]`, pero un enlace wiki `[[Pagina_De_Ejemplo]]`, `[[EMAIL_1` sin emails en la petición o una ristra de letras salen al momento (`restore()` los dejaría igual). De un tramo de `[` seguidos solo se retienen los dos últimos. Tope de seguridad: 64 caracteres (`MAX_HOLDBACK`); solo se pasa con muchos espacios o tabuladores dentro de `[[ ... ]]`, y entonces se suelta el texto tal cual (sale como marcador, nunca como un valor equivocado) sin cortar el stream. Da igual cómo lleguen cortados los trozos: el texto final es el mismo que con `restore()` (invariante 3; tests de propiedades con cortes al azar, también en mitad de un carácter UTF-8, que se decodifica poco a poco).
- **OpenAI**: `delta.content` y `delta.refusal` de cada `choice`. Los `arguments` de `tool_calls` (y del antiguo `function_call`) se acumulan por (choice, índice de la herramienta) y se envían restaurados **en un solo delta**, en el chunk que cierra la choice (`finish_reason`). El primer delta de cada llamada sigue llevando `id`, `type` y `name`, con los `arguments` vacíos. `[DONE]` vacía lo que quede. `stream_options` solo admite `include_usage` e `include_obfuscation`, con `true` o `false`; otra cosa → 400 `invalid_request`.
- **Anthropic**: `text_delta` por índice de bloque. `input_json_delta` se acumula por bloque y sale restaurado como **un** `input_json_delta` justo antes de `content_block_stop` (siempre JSON válido, invariante 4). `thinking_delta` y `signature_delta` pasan **byte a byte** (invariante 9), igual que los bloques `thinking` y `redacted_thinking`. `ping`, `error`, `message_start` y `message_delta` pasan sin tocar; `message_stop` vacía lo que quede.
- **Eventos o campos desconocidos**: pasan sin tocar (ni se restauran ni se bloquean) y se cuentan en memoria (`app.state.stream_counters`, solo el número).
- Si el proveedor ignora `stream` y responde con JSON, se restaura como una respuesta normal.

El formato SSE lo lee un módulo propio y pequeño (`providers/sse.py`, sin dependencias): finales de línea CRLF, LF o CR (también partidos entre dos trozos), `data:` en varias líneas, comentarios y campos desconocidos. Un evento que no se cambia sale **tal cual llegó**, byte a byte.

**Fallos a mitad del stream** (el 200 ya se envió): sale el texto que ya es seguro (restaurado; un marcador a medias sale tal cual, porque es un marcador y no un dato), después **un** evento de error con el formato del proveedor y un mensaje fijo, y se cierra. Unos argumentos de herramienta a medias no se envían.

| Caso | `code` en OpenAI |
|---|---|
| Se agota el tiempo de lectura | `upstream_timeout` |
| Se corta la conexión | `upstream_unavailable` |
| SSE mal formado, UTF-8 inválido, una línea de más de 1 MiB o un evento de más de 4 MiB | `bad_upstream_response` |
| Más de 4 MiB de argumentos de herramientas acumulados | `stream_limit_exceeded` |
| Un evento repite una clave configurada | `bad_upstream_response` |
| El stream acaba sin `[DONE]` o sin `message_stop` | `upstream_stream_cut` |

En OpenAI el error es `data: {"error": {"message": ..., "type": "antifaz_error", "code": ...}}`; en Anthropic, `event: error` con `{"type": "error", "error": {"type": "api_error", "message": ...}}`. Ningún mensaje repite lo recibido; el log solo guarda el `code`.

**Claves (invariante 13).** Se revisa lo que llega del proveedor y, sobre todo, **lo que Antifaz envía** después de restaurar (unos argumentos con la clave escrita como `\u0063...` quedarían en claro al parsearlos y volver a serializarlos). Cada evento se revisa tal cual y decodificado hasta tres capas (JSON dentro de cadenas y escapes deshechos). Además, cada cadena de cada evento se une al final de la misma cadena en los eventos anteriores, con **un final por campo** (índice de choice o de bloque más el nombre del campo, conocido o no: `content`, `refusal`, `reasoning_content`, `thinking`, `cited_text`…), así una clave partida entre dos deltas se detecta aunque lleguen otros campos o choices en medio. Si la cabecera `Content-Type` del proveedor ya lleva una clave → 502 antes de empezar. Sin streaming se hace lo mismo con la respuesta ya restaurada (`tool_calls[].function.arguments`, `tool_use.input`): 502 `bad_upstream_response` fijo.

**Cierre.** Si el cliente se va a mitad, la respuesta cierra la conexión con el proveedor aunque esté esperando datos (`RelayResponse`; hay un test con una desconexión ASGI de verdad).

Tests: `tests/unit/test_stream_restore.py`, `test_sse.py`, `test_stream_transformers.py`; propiedades en `tests/property/test_stream_restore_property.py`, `test_sse_property.py` y `test_stream_protocol_property.py`; ataques en `tests/redteam/test_streaming.py` (429 y 5xx, proveedor lento, cortes en mitad de un marcador, UTF-8 partido o inválido, marcadores de otra petición, razonamiento, eventos desconocidos, claves y desconexión del cliente).

## Tests de contrato con los SDK oficiales (issue 5, PR 5d)

Comprueban que un cliente real (los SDK oficiales `openai` y `anthropic` de Python) entiende todo lo que Antifaz devuelve y recibe los datos restaurados. Sin red y sin claves:

```
SDK oficial --(httpx2.ASGITransport, en el mismo proceso)--> Antifaz --(httpx.MockTransport)--> proveedor falso
```

- Los SDK reciben un `http_client` que llama a la app ASGI de Antifaz dentro del proceso (los SDK usan `httpx2`, un fork de httpx; Antifaz sigue con `httpx`). Detrás, el proveedor falso (`tests/contract/conftest.py`) sirve las respuestas de `tests/contract/fixtures/` cortadas en trozos de 37 bytes (los eventos y los marcadores llegan partidos) y guarda todo lo que recibe. Además, mientras corren, cualquier conexión que no sea a `localhost` falla, y se quitan del entorno las variables `OPENAI_*` y `ANTHROPIC_*`. Los SDK van con `max_retries=0`, para que un 429 o un 502 llegue al test.
- Qué cubren: `chat.completions.create` (texto, herramientas, streaming de texto y de `tool_calls`, y el helper `chat.completions.stream`), `messages.create` (texto, `tool_use`, `thinking`), `messages.stream` (`text_delta`, `input_json_delta`, `thinking_delta` y `signature_delta`), `messages.count_tokens` y los errores: 401 → `AuthenticationError`, 400 → `BadRequestError`, 429 → `RateLimitError` y 502 → `InternalServerError`, con y sin streaming. También la clave de Antifaz equivocada (401) y un adjunto bloqueado (400).
- Cada test comprueba dos cosas: que ningún valor sembrado (DNI, email, IBAN, en cualquier espaciado) ni la clave de Antifaz llegó al proveedor falso, y que el SDK recibe los valores restaurados (texto, argumentos de herramientas como JSON válido, `tool_use.input`). El razonamiento (`thinking` y su firma) sale igual que entró (invariante 9), y un marcador de un razonamiento anterior no se restaura con el dato nuevo.
- **Respuestas escritas a mano** (de momento): copian la forma de las respuestas oficiales y solo llevan marcadores y datos inventados. Cada una dice de dónde sale (`provenance`: `hand-written` o `recorded AAAA-MM-DD model X`). Las grabaciones reales llegarán en la segunda fase de 5d, con OK de Miquel. Ver `tests/contract/fixtures/README.md`.
- `test_fixtures_sanitised.py` revisa **todas** las respuestas guardadas: nada que parezca una clave (`sk-`, `sk-ant-`, `Bearer`, cadenas largas de alta entropía que no estén en la lista de valores falsos), ninguna cabecera `Authorization`, `x-api-key` o `Cookie`, ningún dato personal (con el detector de Antifaz y patrones de email y teléfono) fuera de la lista de ejemplos sintéticos, y un `provenance` válido. Hay tests de que ese revisor sí detecta cada caso.
- Los SDK son dependencias **solo de desarrollo** (grupo `dev`, no se distribuyen); sus licencias están en [licencias.md](licencias.md).

## `antifaz verify` (issue 5, PR 5d, primera versión)

Para que quien instala Antifaz compruebe **con su configuración** que los datos no salen, sin gastar nada:

```bash
uv run antifaz verify
```

- Lee la configuración como el servidor (variables `ANTIFAZ_*` y `.env`) y comprueba primero que arrancaría (mismas reglas que `create_app`); si no, código 2 y el nombre de la variable, nunca su valor.
- **Nunca llama al proveedor.** Levanta la pasarela dentro del proceso (`httpx.ASGITransport`) con un proveedor falso (`httpx.MockTransport`) que guarda los bytes que recibe y responde con los marcadores que ha visto. Para la prueba cambia las URL y las claves de los proveedores por unas falsas (así da igual que falten o que sean reales) y acepta un `Host` más (`antifaz-verify.invalid`).
- Siembra seis tipos de dato sintéticos (DNI, NIE, IBAN, email, teléfono y tarjeta) en 16 peticiones (más 2 para las claves): en OpenAI, mensaje de sistema, de usuario, parte de contenido, argumentos de una herramienta y su resultado, con y sin streaming; en Anthropic, `system` (texto y bloques), mensaje de usuario, bloque de texto, `tool_use.input` y `tool_result`, con y sin streaming, y `count_tokens`.
- Falla (código 1) si un valor sembrado llega al proveedor falso (tal cual o con otros espacios y mayúsculas), si la clave de Antifaz llega al proveedor, si una respuesta trae alguna clave configurada (hay una petición por ruta en la que el proveedor falso devuelve las cabeceras que recibió), si la pasarela bloquea o da error en una petición que debía pasar (el enmascarador dejó un valor y la guardia lo paró), si un valor no vuelve en la respuesta, o si una petición no llega al proveedor falso exactamente una vez (`NO UPSTREAM`: la pasarela contestó sin preguntar y la prueba no demostraría nada). Las 2 peticiones de claves, además, tienen que acabar en 502.
- Si `ANTIFAZ_MAX_BODY_BYTES` es tan pequeño que algunas peticiones de prueba dan 413, sale con código 2 ("could not verify", súbelo y repite): no es un fallo de privacidad, porque esas peticiones no salieron.
- El informe solo dice **tipos y sitios** (`LEAK ES_DNI, IBAN reached the provider -- openai chat: tool result`), nunca valores ni claves. Si todo va bien, un `PASS` de cinco líneas.
- Usa la misma política que el servidor (hoy la de fábrica). Un tipo que la política deja pasar (`allow`) no se comprueba y aparece como nota.
- No usa la comprobación de claves de la propia pasarela (`api/proxy.py`) para decidir: tiene la suya, para ver lo que la pasarela se deje.
- Tests: `tests/unit/test_verify.py`, también con la pasarela saboteada (enmascarador ciego → `LEAK` y código 1; enmascarador que deja un valor → `BLOCKED`; comprobación de claves desactivada → `KEY`; clave de Antifaz reenviada, en las dos rutas → `LEAK`; respuesta sin restaurar → `RESTORE`; `count_tokens` contestado sin proveedor → `NO UPSTREAM`). En estos tests y en los de contrato, cualquier búsqueda de nombre o conexión de socket que no sea a la propia máquina falla.

Limitaciones de esta primera versión: los valores sembrados son fijos (los mismos que usan los tests), la política no se lee todavía de un fichero, y no revisa lo que se escribe en los logs (eso lo cubren los tests de las invariantes 8 y 13).

## La puerta cerrada por defecto (issue 20)

Decisión en [ADR-0015](adr/0015-puerta-cerrada-por-defecto.md) (propuesta). Todo pasa por un solo middleware (`api/gate.py`) antes de llegar a ninguna ruta, así que una ruta nueva queda protegida sin hacer nada.

**Arranque.** Antifaz se niega a arrancar, y dice qué variable falla sin mostrar su valor, si:

- `ANTIFAZ_API_KEY` falta, tiene menos de 32 caracteres o menos de 8 caracteres distintos, es el valor de `.env.example` (`change-me`, también escrito `change_me` o `ChangeMe`) o no es ASCII imprimible sin espacios. Genera una con `openssl rand -hex 32`;
- `ANTIFAZ_OPENAI_API_KEY` o `ANTIFAZ_ANTHROPIC_API_KEY` están definidas pero vacías, son el valor de ejemplo o no son ASCII imprimible sin espacios;
- `ANTIFAZ_ALLOWED_HOSTS` está vacía, tiene `*` o un comodín con menos de dos etiquetas detrás (`*.com`), o `ANTIFAZ_ALLOWED_ORIGINS` tiene algo que no sea `http(s)://host[:puerto]` (sin ruta ni barra final; `*` y `null` tampoco).

La app ya no se crea al importar el módulo: `uvicorn --factory antifaz.api.app:create_app` (es lo que hace `make dev`).

**Variables nuevas** (listas separadas por comas, o en JSON: `[ "a", "b" ]`):

| Variable | Por defecto | Qué hace |
|---|---|---|
| `ANTIFAZ_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Valores aceptados en la cabecera `Host` (sin el puerto y sin distinguir mayúsculas). Otro → 400. Admite `*.ejemplo.com`, no `*` ni `*.com` |
| `ANTIFAZ_ALLOWED_ORIGINS` | vacía | Orígenes de navegador aceptados, exactos y sin barra final (`https://intranet.ejemplo.com`). Vacía: toda petición con `Origin` → 403 |

**En cada petición**, en este orden:

1. `Host` en la lista (si no, 400), **antes** que la clave. Protege del DNS rebinding: una web ajena que apunta su dominio a la IP de la pasarela.
2. Ruta pública solo `/healthz`, con coincidencia exacta sobre la ruta ya decodificada (`scope["path"]`). Cualquier otra ruta, exista o no, pide la clave: `/healthz/`, `//v1/messages` o `/docs` también.
3. **Clave** en `Authorization: Bearer` o en `x-api-key` (en todas las rutas), con `hmac.compare_digest`. Si la cabecera viene dos veces, o si vienen las dos y no llevan las dos la clave → 401.
4. **`Origin`**: si viene y no está en `ANTIFAZ_ALLOWED_ORIGINS` → 403. No hay CORS: el navegador nunca recibe `Access-Control-Allow-Origin`, así que una web ajena no puede leer la respuesta ni pasar el preflight.
5. **`Content-Type`**: en todo lo que no sea `GET`, `HEAD` u `OPTIONS`, debe ser `application/json` (con `charset=utf-8` como único parámetro opcional). Un formulario o `text/plain`, que el navegador manda sin preflight → 415.
6. Al leer el cuerpo, un objeto con **claves repetidas** → 400. Si solo cambian en mayúsculas o anchura (`content` y `Content`) también, cuando es una clave que Antifaz lee (`READ_KEYS` en `providers/json_walk.py`), porque unos programas las leen como la misma y otros no. Un esquema con `Name` y `name` pasa.
7. Las claves de adjunto (`source`, `data`, `image_url`, `file`…) bloquean también en mayúsculas (`SOURCE`, `Data`).

Además: `X-Request-ID` siempre lo genera la pasarela (el del cliente se ignora); sin `/docs`, `/redoc` ni `/openapi.json`; sin redirecciones de barra final (`/v1/messages/` → 404); los WebSocket se cierran siempre; el log de cada petición solo escribe la ruta si es una ruta registrada (si no, `-`), para que una clave o un DNI pegados en la URL no acaben en el log; y si el proveedor devuelve en su respuesta alguna de las claves configuradas (tal cual o con escapes JSON como `\u0061`), se descarta con un 502 fijo.

**Detrás de Docker o de un proxy inverso** (nginx, Traefik, Caddy):

- Pon en `ANTIFAZ_ALLOWED_HOSTS` el nombre con el que llegan los clientes (`antifaz`, el nombre del servicio en compose, o `antifaz.ejemplo.com`). Si el proxy reescribe `Host`, pon el que reescribe. `X-Forwarded-Host` no se mira.
- El proxy no debe añadir cabeceras CORS ni un `Origin` propio.
- `/healthz` es pública: si el proxy la expone, solo dice la versión.
- `root_path` no está soportado: si la sirves bajo un prefijo (`--root-path`), `/healthz` puede pedir clave, porque la lista pública compara la ruta exacta y, si no coincide, falla cerrada.

**Tests.** `tests/redteam/test_invariants_12_13.py` recorre las rutas que la app registra de verdad (con `iter_route_contexts`, porque en FastAPI `app.routes` guarda los routers incluidos y no sus rutas), cada una con GET, HEAD, POST, PUT, PATCH, DELETE y OPTIONS y con alias (barra final, doble barra, mayúsculas, último carácter codificado): sin clave, 401 salvo `/healthz`. Cada ruta del proxy pasa por la guardia de salida con los mismos bytes que recibe el proveedor, y si la guardia bloquea no sale nada (invariante 12). Otro test usa claves canario y recorre 401, 400, 403, 404, 413, 415, 502 y 504, errores del proveedor y un proveedor que devuelve las cabeceras que recibió, con los loggers en `DEBUG`: ninguna clave aparece en logs, cuerpos ni cabeceras (invariante 13). Los ataques están en `tests/redteam/test_gateway.py`.

## Decisiones técnicas del issue 1

| Decisión | Por qué |
|---|---|
| Acciones de GitHub fijadas por SHA completo y permisos mínimos | Una etiqueta (`v4`) se puede mover a otro código; un SHA no. Incidentes reales: tj-actions (2025) y Trivy (2026) |
| `persist-credentials: false` en cada checkout | El token de GitHub no queda guardado en el disco del runner para pasos posteriores |
| `uv audit` fijado a uv 0.12.20 | Comprueba vulnerabilidades conocidas (OSV) del `uv.lock`. Es un comando experimental de uv: si cambia, se ajusta aquí |
| Comprobación de licencias propia (`scripts/check_licenses.py`) | Regla sencilla y auditable: nada GPL/AGPL/no comercial en lo que se distribuye; copyleft débil solo si está anotado en `docs/licencias.md` |
| Cobertura ≥ 90 % en `detect`, `vault`, `mask`, `guard`, `restore`, `providers` y `api` (`scripts/check_coverage.py`) | Son las piezas de privacidad: un fallo ahí significa datos personales enviados |
| CodeQL solo cuando el repo sea público | En repos privados necesita GitHub Advanced Security (de pago); mientras tanto el job se salta en lugar de fallar |
| Manejador propio de errores 422 | El de FastAPI devuelve el cuerpo recibido, que podría llevar un DNI. El nuestro solo dice qué campo está mal |
| Hooks de Claude Code con tests | Bloquean los casos comunes de leer `.env`, imprimir variables secretas, `--no-verify` y force push. Son **defensa en profundidad, no una barrera**: quien ejecuta código arbitrario puede saltárselos, y la revisión de privacidad encontró varios caminos (ver el modelo de amenazas). La barrera real es gitleaks en CI |
| `uv audit` también sobre las herramientas de desarrollo | A propósito: esas herramientas corren en la CI con acceso al código; una vulnerable también es un riesgo |
| Errores 422 sin claves del cliente y con tamaño máximo | En la ubicación del error solo quedan la parte (`body`, `query`…) y los índices; las claves de un diccionario las elige el cliente y podrían ser un DNI |
| `X-Request-ID` propio siempre (issue 20) | Se escribe en logs y cabeceras: el del cliente se ignora, porque hasta un valor corto y sin símbolos puede ser un DNI |

## Decisiones técnicas del issue 21 (CI y cadena de suministro)

| Decisión | Por qué |
|---|---|
| zizmor 1.30.1 en pre-commit (sin conexión), en `make check` y en la CI (con las comprobaciones en línea), perfil `auditor` | Busca fallos de seguridad en los workflows (inyecciones, permisos, acciones sin fijar). El perfil `auditor` también saca los avisos de baja confianza: cualquier aviso rompe la CI |
| `permissions: {}` arriba en cada workflow y permisos por job, cada uno con su comentario | Un job nuevo empieza sin permisos; solo recibe lo que pide y queda explicado por qué |
| `concurrency` por rama; en un PR, un push nuevo cancela la ejecución anterior (en `main` nunca) | No se acumulan ejecuciones viejas y en `main` siempre termina la de cada commit |
| Dependabot con `cooldown` de 7 días en `uv` y `github-actions` | Una versión secuestrada suele detectarse y retirarse en pocos días; las actualizaciones de seguridad no esperan |
| `CODEOWNERS` con líneas propias para `pyproject.toml`, `uv.lock`, `.github/` y `Dockerfile` | Son los archivos que cambian qué código se ejecuta en la CI o se distribuye |
| Herramientas de la CI con versión exacta (uv 0.12.20, zizmor 1.30.1, acciones por SHA) | La misma entrada da siempre la misma herramienta; se actualizan a propósito |
| Sin topes superiores (`<3`) en las dependencias de `pyproject.toml`; versiones exactas solo en `uv.lock` | Antifaz también es una librería: un tope impide a quien la instala recibir arreglos de seguridad y crea conflictos con otros paquetes. Lo reproducible es el `uv.lock`. Quitar `python-stdnum<3` no cambió ninguna versión bloqueada. `requires-python` mantiene `<3.13` porque la CI solo prueba 3.12 |

## Limitaciones

- El proxy habla OpenAI Chat y Anthropic Messages (con y sin streaming). La prueba con Claude Code de verdad y las respuestas grabadas de los proveedores reales (segunda fase de 5d) están pendientes: los tests de contrato usan respuestas escritas a mano.
- En streaming, los argumentos de las herramientas llegan **de golpe al final de su bloque** (o de la `choice` en OpenAI), no poco a poco: así siempre son JSON válido.
- En streaming, un marcador de esta petición con más de ~60 espacios o tabuladores dentro de `[[ ... ]]` sale sin restaurar (como marcador): es el tope de seguridad de lo retenido. Más de 4 MiB de argumentos de herramientas cortan el stream con `stream_limit_exceeded`; el texto ya restaurado de otras choices se envía antes del error.
- Una llamada a herramienta sin `index` en OpenAI pasa sin tocar (sus argumentos no se pueden unir) y se cuenta como desconocida. En Anthropic, `input_json_delta` solo se restaura en bloques `tool_use`; en otros (herramientas del servidor…) pasa sin tocar y se cuenta.
- Argumentos o `input` que no son JSON válido (cortados, texto suelto): cada valor se inserta escapado como dentro de una cadena JSON (comillas y barras con `\`). Así lo que era JSON sigue siéndolo y un marcador dentro de una cadena no la rompe; en texto suelto el valor sale con esos escapes.
- Si un proveedor manda una clave partida en varios eventos, el stream se corta al completarse, pero el trozo ya enviado llega al cliente (no es la clave entera).
- El tamaño total de un stream no tiene tope (sí cada línea, cada evento, lo retenido y lo acumulado).
- En Anthropic se bloquean las `citations`, los documentos y las herramientas del servidor (búsqueda web…): fallar cerrado es a propósito.
- Las claves de los objetos JSON no se enmascaran: si contienen un dato, se bloquea la petición.
- El detector no decodifica base64, hexadecimal ni otras codificaciones dentro del texto (solo bloquea las URL `data:...;base64,`).
- Los errores del proveedor se devuelven con los marcadores `[[TIPO_N]]` sin restaurar.
- El tamaño de la respuesta del proveedor no tiene tope en v0.1.
- `Bearer` en la cabecera `Authorization` distingue mayúsculas: `bearer` se rechaza.
- Un objeto con dos claves que solo cambian en mayúsculas se rechaza con 400 si es una clave que Antifaz lee (`content` y `Content`); las demás (`id` e `ID` en un esquema) pasan (ADR-0015).
- Si el proveedor devuelve la clave recortada (por ejemplo `sk-...abcd` en su error de clave incorrecta), ese fragmento llega al cliente: solo se detecta la clave completa.
- La pasarela no se puede usar desde una web pública: no hay CORS.
- En `tools`, `functions`, `response_format` y `tool_choice` (esquemas del desarrollador) no se aplica la lista de tipos permitidos; sus textos sí se enmascaran.
- Los nombres de persona no se detectan hasta el issue 6 (NER): hoy pasan en claro.
