# Detalles técnicos

> Antifaz está en desarrollo (v0.1). Esta página crece con cada issue.

## Ponerlo en marcha (desarrollo)

```bash
make install   # dependencias + hooks de pre-commit
make check     # lint + tipos + tests + gitleaks
make audit     # vulnerabilidades conocidas en las dependencias (uv audit, experimental)
make licenses  # licencias de lo que se distribuye
make dev       # API en http://localhost:8000 (/healthz y /v1/chat/completions)
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

**En el texto:** cada patrón solo propone candidatos; un span se devuelve si su validador lo acepta. Los patrones siguen el ADR-0008 (repeticiones acotadas, cuantificadores posesivos, límites que impiden encontrar un DNI dentro de una palabra más larga) y hay pruebas con textos maliciosos de 50.000 caracteres. El IBAN se corta a la longitud de su país, sacada del registro IBAN, y se busca avanzando carácter a carácter tras un candidato falso, para que ningún candidato tape a otro IBAN. Se aceptan mayúsculas y minúsculas y los separadores habituales (espacio, punto, guion; barra en el NSS). La resolución de solapamientos es O(n log n): un texto con 20.000 DNI se resuelve en menos de 2 s.

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

**Limitaciones conocidas:**
- Cifras Unicode (de ancho completo, árabes…) y separadores raros (tabulador, espacio duro NBSP, espacio de ancho cero) no se detectan todavía: haría falta normalizar el texto conservando las posiciones (issue aparte).
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
```

Si no puede leer el fichero como UTF-8 o el detector falla: mensaje genérico en stderr y código 2, sin repetir el contenido.

## Proxy compatible con OpenAI Chat (issue 5, PR 5a)

Configura `.env` a partir de `.env.example`: `ANTIFAZ_API_KEY` (la clave que usan tus clientes), `OPENAI_API_KEY` y, si quieres otro servidor compatible, `OPENAI_BASE_URL`. Sin las dos claves, el proxy responde 503.

```bash
curl http://localhost:8000/v1/chat/completions   -H "Authorization: Bearer $ANTIFAZ_API_KEY" -H "Content-Type: application/json"   -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Mi DNI es 12345678Z"}]}'
```

Con el SDK de OpenAI basta con `base_url="http://localhost:8000/v1"` y `api_key=<ANTIFAZ_API_KEY>`.

Qué hace con cada petición ([ADR-0013](adr/0013-proxy.md), propuesta):

1. Comprueba la clave de Antifaz con `hmac.compare_digest`. Esa clave **nunca** llega al proveedor; tampoco ninguna otra cabecera del cliente.
2. Lee el cuerpo con un tope (`MAX_BODY_BYTES`, 4 MiB por defecto): más grande → 413; no es un objeto JSON en UTF-8 → 400.
3. Reúne **todas** las cadenas del cuerpo (mensajes, partes de texto, `name`, resultados de herramientas y cualquier campo nuevo) y llama a `mask()` una sola vez. Los `arguments` de las llamadas a herramientas se parsean como JSON y se enmascaran sus valores. Las claves de los objetos no se cambian: si alguna contiene un dato, la petición se bloquea.
4. Solo pasan partes de tipo `text`, `refusal` o `function`: imágenes, documentos, audio, ficheros, `file_id`, claves como `source` o `data`, o un texto con una URL `data:...;base64,` → 400 `antifaz_blocked`. Los números pasan por el detector: si uno es un dato (un teléfono escrito como número), se bloquea. `NaN`/`Infinity` o un `stream` que no sea booleano → 400.
5. Serializa una vez y la guardia de salida revisa **esos mismos bytes** justo antes de enviarlos.
6. Envía a `OPENAI_BASE_URL` con `OPENAI_API_KEY`. La URL nunca sale del cliente.
7. Restaura `content`, `refusal` y los `arguments` de las herramientas (parseando el JSON, así siguen siendo JSON válido aunque el dato tenga comillas). El resto de campos pasa sin tocar.

Errores:

| Caso | Respuesta |
|---|---|
| Sin clave o clave incorrecta | 401 `unauthorized` |
| Dato en un sitio que no se puede enmascarar, adjunto, detector roto o guardia | 400 `antifaz_blocked`, mensaje fijo |
| `stream: true` | 400 `streaming_not_supported` (llega en la parte 5c) |
| El proveedor tarda más de `UPSTREAM_TIMEOUT_SECONDS` | 504 `upstream_timeout` |
| No se puede conectar | 502 `upstream_unavailable` |
| El proveedor responde con una redirección (3xx) | 502 `upstream_redirect` (no se sigue) |
| El proveedor responde con error (4xx/5xx) | Su cuerpo **tal cual**: solo vio texto enmascarado, así que puede mostrar marcadores `[[TIPO_N]]` |

Ningún log escribe cuerpos, cabeceras ni claves; `httpx` y `httpcore` quedan en `WARNING`. Un test (invariante 8) pasa un DNI centinela por respuestas, errores del proveedor, tiempos agotados y bloqueos de la guardia, con todos los loggers en `DEBUG`, y comprueba que no aparece ni en los logs ni en los cuerpos de error. Otro (invariante 2) quita la guardia **solo en el test** y comprueba que el proveedor falso no recibe ningún valor oculto, tampoco en argumentos ni resultados de herramientas.

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
| `X-Request-ID` del cliente validado | Se escribe en logs y cabeceras: solo se acepta si es corto y sin símbolos; si no, se genera uno nuevo |

## Limitaciones

- El proxy solo habla OpenAI Chat y sin streaming; Anthropic Messages y el streaming llegan en el resto del issue 5.
- Las claves de los objetos JSON no se enmascaran: si contienen un dato, se bloquea la petición.
- El detector no decodifica base64, hexadecimal ni otras codificaciones dentro del texto (solo bloquea las URL `data:...;base64,`).
- Los errores del proveedor se devuelven con los marcadores `[[TIPO_N]]` sin restaurar.
- El tamaño de la respuesta del proveedor no tiene tope en v0.1.
- `Bearer` en la cabecera `Authorization` distingue mayúsculas: `bearer` se rechaza.
- En `tools`, `functions`, `response_format` y `tool_choice` (esquemas del desarrollador) no se aplica la lista de tipos permitidos; sus textos sí se enmascaran.
- Los nombres de persona no se detectan hasta el issue 6 (NER): hoy pasan en claro.
