# ADR-0013 · El proxy: qué se enmascara, qué se envía y qué se restaura

- **Estado:** Aceptada (aprobada por Miquel Moreno el 2026-09-30)
- **Fecha:** 2026-09-30
- **Concreta:** el ADR-0006 (campos desconocidos) y el ADR-0012 (escape) para la pasarela HTTP

## Contexto

La librería (`mask`, `restore`, `guard.check`) trabaja con textos. La pasarela recibe JSON de OpenAI o de Anthropic, con texto repartido por muchos campos (mensajes, partes de contenido, argumentos de herramientas, campos nuevos que el proveedor añada mañana). Hay que decidir qué se recorre, cómo se envía y cómo se restaura, sin abrir ninguna vía para que salga un dato sin revisar.

## Decisión

**Petición (cierra por defecto).**

- Un recorrido por formato reúne **todas** las cadenas del cuerpo en un orden estable y llama a `mask()` **una sola vez** por petición. Así la numeración de marcadores es la misma en toda la conversación (invariante 5) y hay una sola tabla por petición.
- Campos conocidos y desconocidos se tratan igual: toda cadena se enmascara de forma genérica (ADR-0006). Los números, booleanos y `null` pasan tal cual.
- Los argumentos de herramientas (`tool_calls[].function.arguments`) son JSON dentro de una cadena: se parsean, se enmascaran los valores de texto y se vuelven a serializar. Las **claves** son el esquema de la herramienta y no se cambian: entran en la misma llamada a `mask()` y, si alguna tuviera algo que ocultar (o un `[[` que escapar), la petición se **bloquea** porque no se puede enmascarar sin romper el formato. Lo mismo vale para las claves de cualquier objeto del cuerpo. Si los argumentos no son JSON válido, se enmascaran como texto.
- Adjuntos, con **lista de permitidos**: un objeto con `type` solo pasa si el tipo es `text`, `refusal` o `function`; cualquier otro (`image`, `document`, `input_image`, uno que salga mañana…) → **bloqueo** con mensaje fijo. Además, las claves `source`, `data`, `image`, `document`, `image_url`, `input_audio`, `file`, `file_id`, `file_data` y `audio` bloquean en cualquier sitio, y también un texto con una URL `data:...;base64,`. No se puede revisar lo que no es texto (invariante 7). Excepción: `tools`, `functions`, `response_format` y `tool_choice` son esquemas del desarrollador (`"type": "object"`, una propiedad llamada `data`…); sus textos se enmascaran igual, pero no pasan por estas dos listas.
- Los números (no los booleanos) pasan por el detector como texto (`str(n)`): no pueden llevar un marcador, así que si esconden un dato (un teléfono como `612345678`) la petición se **bloquea**.
- `NaN` e `Infinity` no son JSON válido: 400. `stream` solo puede ser `true`, `false` o no estar.
- Los bloques de razonamiento (`thinking`, `redacted_thinking` y su `signature`, en Anthropic) se copian **sin tocar**: la firma dejaría de valer (invariante 9).
  - *Enmienda del 2026-09-30 (parte 5b):* solo se aceptan sus claves exactas (`thinking`: `type`, `thinking`, `signature`; `redacted_thinking`: `type`, `data`), todas de texto; otra clave se bloquea. El texto de `thinking` pasa por el detector y bloquea si lleva un dato. `signature` y `data` son **opacos** (firma y razonamiento cifrado por el proveedor) y no se pueden revisar: **riesgo aceptado**. Los marcadores que ya contiene el razonamiento se **reservan** en la numeración de esta petición y no entran en la tabla, para que un dato nuevo no reciba el mismo marcador de otro turno.
  - *Enmienda del 2026-09-30:* la configuración se lee solo de variables `ANTIFAZ_*`, para no mezclarse con las de Claude Code o los SDK (`ANTHROPIC_BASE_URL`, `OPENAI_API_KEY`…) en la misma terminal.
- El cuerpo enmascarado se serializa **una sola vez**, y `guard.check()` revisa **esos mismos bytes** justo antes de enviarlos. No hay una segunda serialización que la guardia no haya visto.
- `stream: true` se rechaza con un 400 fijo hasta la parte 5c.
  - *Enmienda del 2026-09-30 (parte 5c):* `stream: true` se acepta en `/v1/chat/completions` y `/v1/messages`; `count_tokens` lo sigue rechazando. `stream_options` (OpenAI) tiene lista de permitidos: `include_usage` e `include_obfuscation`, solo booleanos.

**Destino y claves.**

- La URL del proveedor y su clave salen **solo** de `.env`. Nada del cliente (cuerpo, cabeceras, query) elige el destino (SSRF).
- La clave de Antifaz se compara con `hmac.compare_digest` y **nunca** se reenvía. Al proveedor solo le llega la clave de `.env`.

**Respuesta (no bloquea, ADR-0006).**

- Se restauran solo los campos conocidos con texto (en OpenAI Chat: `message.content`, `message.refusal` y `tool_calls[].function.arguments`). Lo demás pasa sin tocar.
- Argumentos de herramientas: se parsean, se restaura cada valor de texto y se vuelven a serializar, así el resultado es JSON válido aunque el dato tenga comillas o barras (invariante 4). Si el proveedor devuelve algo que no es JSON, se restaura como texto.
- Streaming (5c): se restaura el texto al vuelo reteniendo solo el final del trozo que **podría** ser el principio de un marcador o de un escape, con el mismo patrón que `restore()` y un tope de tamaño (invariante 3). Los argumentos de herramientas se acumulan por índice y se emiten restaurados al cerrar el bloque: siempre son JSON válido, a cambio de perder el streaming parcial de esos argumentos.
  - *Enmienda del 2026-09-30 (parte 5c), concreta cómo se hace:* solo se retiene lo que aún puede ser un marcador **de esta petición** (un `[[Enlace_Wiki]]` o `[[EMAIL_1` sin emails en la petición sale al momento, porque `restore()` los dejaría igual). El tope de lo retenido es de 64 caracteres y solo se alcanza con muchos espacios dentro de `[[ ... ]]`: entonces se suelta el texto tal cual (sale como marcador, nunca como otro valor), sin cortar. Pasar 4 MiB de argumentos acumulados, una línea SSE de 1 MiB o un evento de 4 MiB **corta el stream con un error**; el texto que otras choices ya restauraron se envía antes. En OpenAI "cerrar el bloque" es el chunk con `finish_reason` de esa choice (o `[DONE]`). Si el stream falla a mitad (tiempo, conexión, SSE mal formado, un tope, una clave en la respuesta o un final que no llega), se envía el texto ya seguro (un marcador a medias sale tal cual), **un** evento de error con el formato del proveedor y mensaje fijo, y se cierra; los argumentos a medias no se envían. Se revisa en busca de claves lo que llega y **lo que se envía** tras restaurar, también sin streaming (invariante 13): tal cual, decodificado hasta tres capas y, en streaming, con un final por campo (índice de choice o bloque + nombre) para claves partidas. Argumentos que no son JSON válido reciben cada valor escapado como en una cadena JSON (invariante 4 en todo lo que sí era JSON). Los eventos que no se tocan salen byte a byte. Si el proveedor responde JSON a una petición con `stream`, se restaura como respuesta normal.

**Errores.**

- Los errores del proveedor (4xx/5xx) se devuelven tal cual: el proveedor solo vio texto enmascarado, así que no contienen datos en claro. No se restauran.
- Una redirección (3xx) del proveedor no se sigue: 502.
- Los errores propios llevan mensajes fijos (sin valores ni cuerpo) y se lanzan `from None`. Tiempo agotado → 504; fallo de conexión → 502.
- Nunca se escriben en logs cuerpos, cabeceras ni claves. Los loggers de `httpx` y `httpcore` quedan en `WARNING` (invariante 8).

## Alternativas descartadas

- **Parser JSON incremental** para emitir los argumentos de herramientas restaurados trozo a trozo: más código delicado en la pieza más sensible y difícil de probar. Acumular por índice es simple y siempre da JSON válido.
- **Dependencias nuevas** (`respx` para simular el proveedor, librerías de SSE): `httpx.MockTransport` ya cubre los tests y el formato SSE es sencillo. Menos cadena de suministro.
- **Solo campos conocidos** en la petición: cada novedad del proveedor sería una fuga. Por eso todo texto se enmascara.

## Consecuencias

- Invariantes que cubre esta decisión: 2, 3, 4, 7, 8 y 9.
- Un campo nuevo del proveedor con texto se enmascara sin tocar el código; un tipo de parte nuevo se bloquea hasta que se añada a la lista de permitidos.
- Si una clave de un objeto contiene un dato personal, la petición se bloquea aunque sea legítima: falla cerrada a propósito.
- Los errores del proveedor pueden mostrar marcadores `[[TIPO_N]]` en lugar de los datos.
