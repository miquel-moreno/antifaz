<p align="center"><img src="docs/images/logo.svg" alt="Antifaz" width="120"></p>

# Antifaz

**Usa la IA con datos de clientes sin enviarle sus datos.**

[Read in English](README.md)

> 🚧 **En desarrollo.** Primera versión (v0.1) prevista para octubre de 2026. Todavía no está lista para usarse.

Antifaz es una pasarela autoalojada que se pone entre tus aplicaciones y ChatGPT, Claude u Ollama. Antes de que salga un texto, cambia los datos personales que detecta (con foco en los españoles y europeos: DNI, NIE, CIF, NSS, IBAN…) por marcadores como `[[ES_DNI_1]]`, y los vuelve a poner en la respuesta. Ayuda a minimizar los datos personales que se envían a los proveedores de IA y guarda pruebas sin datos personales.

## Lo que Antifaz no es

- No anonimiza: **seudonimiza**. Solo protege lo que detecta, y ningún detector es perfecto.
- No evita que la IA deduzca cosas por el contexto.
- No procesa imágenes ni archivos: los bloquea.
- Instalarlo no convierte a nadie en "cumplidor del RGPD".

## Mi papel

Lo he diseñado y desarrollado de principio a fin. Desarrollo asistido por IA bajo mi especificación y revisión.

[Detalles técnicos →](docs/TECNICO.md) · [LinkedIn](https://www.linkedin.com/in/miquel-moreno-martinez) · Licencia: [Apache-2.0](LICENSE)
