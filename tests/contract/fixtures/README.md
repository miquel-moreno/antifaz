# Respuestas de proveedor para los tests de contrato

Estas son las respuestas que el **proveedor falso** devuelve a Antifaz en `tests/contract/`. Los SDK oficiales de OpenAI y Anthropic hablan con Antifaz dentro del proceso, y Antifaz habla con este proveedor falso. Nunca se llama a un proveedor de verdad.

## Cómo se hicieron

- **Escritas a mano** (2026-09-30, parte 5d, sin coste). Copian la forma de las respuestas oficiales de OpenAI Chat Completions y Anthropic Messages (con y sin streaming, herramientas, razonamiento, `count_tokens` y errores 400, 401, 429 y 502) según su documentación pública.
- Solo llevan **marcadores** (`[[ES_DNI_1]]`, `[[EMAIL_1]]`, `[[IBAN_1]]`) y datos inventados: ids como `msg_contract_text_0001`, una firma de razonamiento falsa (`fake-signature-for-contract-tests` en base64) y cifras de uso inventadas.
- Cada una dice de dónde sale:
  - `.json`: campo `"provenance"` (junto a `"status"` y `"body"`);
  - `.sse`: primera línea de comentario SSE, `: provenance: ...` (los clientes SSE ignoran los comentarios).
  - El valor es `hand-written` o `recorded AAAA-MM-DD model <modelo>`.
- Los `.sse` se guardan con un solo salto de línea al final (lo exige el hook `end-of-file-fixer`); el test añade la línea en blanco que cierra el último evento.

## Grabaciones reales (parte 5d, segunda fase)

Cuando Miquel lo apruebe (cuesta céntimos), se grabarán respuestas reales con los SDK. Antes de guardarlas aquí:

1. se quitan cabeceras (`Authorization`, `x-api-key`, cookies, ids de organización);
2. los datos se cambian por marcadores o por los valores sintéticos de los tests;
3. las firmas de razonamiento se cambian por la falsa;
4. se pone `provenance` como `recorded AAAA-MM-DD model <modelo>`.

Pueden sustituir a las escritas a mano o añadirse a ellas.

## Lo que se comprueba siempre

`tests/contract/test_fixtures_sanitised.py` recorre **todos** los archivos de esta carpeta y falla si encuentra:

- algo que parece una clave (`sk-`, `sk-ant-`, `Bearer `, o una cadena larga de alta entropía que no esté en la lista de valores falsos conocidos);
- cabeceras `Authorization`, `x-api-key`, `Cookie` o `Set-Cookie`;
- un dato personal (con el detector de Antifaz y patrones de email y teléfono) que no esté en la lista de ejemplos sintéticos;
- un archivo sin `provenance` válido.
