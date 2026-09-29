# ADR-0002 · Formato del marcador

- **Estado:** Propuesta (pendiente de aprobar por Miquel Moreno)
- **Fecha:** 2026-09-29

## Contexto

Los datos detectados se cambian por un marcador que el LLM tiene que conservar en su respuesta para poder restaurarlo.

## Decisión

- Formato `[[TIPO_N]]`, con el tipo en inglés: `[[ES_DNI_1]]`, `[[PERSON_2]]`.
- Al restaurar se toleran espacios y mayúsculas distintas (`[[ es_dni_1 ]]`), porque los modelos a veces los alteran.
- Si el usuario ya escribe `[[…]]` en su texto, se escapa antes de enmascarar para que no se confunda con un marcador.

## Consecuencias

- Los marcadores son legibles y el modelo entiende qué tipo de dato había.
- El escape es obligatorio: sin él, un texto con `[[ES_DNI_1]]` podría restaurarse con un dato real (invariante 6).
