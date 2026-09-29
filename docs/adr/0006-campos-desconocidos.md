# ADR-0006 · Campos desconocidos y adjuntos

- **Estado:** Propuesta (pendiente de aprobar por Miquel Moreno)
- **Fecha:** 2026-09-29

## Contexto

Los formatos de OpenAI y Anthropic cambian a menudo. Un campo nuevo con texto no puede ser una vía para que salgan datos sin revisar.

## Decisión

- En la petición: el texto libre en un campo desconocido se enmascara de forma genérica; imágenes, documentos, `file_id` o binarios se bloquean.
- En la respuesta: se restaura en los campos conocidos; los eventos desconocidos pasan sin tocar y se cuentan en métricas.

## Consecuencias

- La petición cierra por defecto (diferencial D2).
- La respuesta no se bloquea: bloquearla no protege nada (los datos ya los tiene el cliente) y rompería clientes como Claude Code con cada novedad del proveedor.
