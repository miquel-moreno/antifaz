# ADR-0004 · Sin estado entre peticiones

- **Estado:** Propuesta (pendiente de aprobar por Miquel Moreno)
- **Fecha:** 2026-09-29

## Contexto

Las APIs de chat reenvían todo el historial en cada petición. El mismo dato tiene que tener el mismo marcador en toda la conversación, y ningún usuario debe poder sacar datos de otro.

## Decisión

- En cada petición se enmascara la conversación entera, numerando los marcadores por orden de primera aparición. El mismo dato recibe el mismo marcador sin guardar nada entre peticiones.
- La tabla de marcadores vive solo durante la petición.
- Excepción (v1.0): Responses API con `previous_response_id`, donde la historia vive en el proveedor. Ahí hace falta una tabla por cadena de respuestas, cifrada con AES-GCM, con TTL y ligada a la clave, en Valkey si hay más de un worker (se comprueba al arrancar).

## Consecuencias

- No hay bóveda compartida que atacar ni que filtrar.
- Funciona igual con uno o varios workers.
- Coste: se vuelve a detectar todo el historial en cada turno; lo mitiga la caché de spans por hash del contenido.
