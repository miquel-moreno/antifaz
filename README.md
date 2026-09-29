<p align="center"><img src="docs/images/logo.svg" alt="Antifaz" width="120"></p>

# Antifaz

**Use any LLM with your customers' data, without sending it.**

[Leer en español](README.es.md)

> 🚧 **In development.** First release (v0.1) planned for October 2026. Nothing here is ready to use yet.

Antifaz is a self-hosted gateway that sits between your apps and ChatGPT, Claude or Ollama. Before a text leaves, it replaces the personal data it detects (with a focus on Spanish and EU identifiers: DNI, NIE, CIF, NSS, IBAN…) with placeholders like `[[ES_DNI_1]]`, and puts the values back in the answer. It helps minimise the personal data sent to LLM providers and keeps evidence without personal data.

## What Antifaz is not

- It does not anonymise: it **pseudonymises**. It only protects what it detects, and no detector is perfect.
- It does not stop a model from inferring things from context.
- It does not process images or files: it blocks them.
- Installing it does not make anyone GDPR compliant.

## My role

I designed and built it end to end. AI-assisted development under my specification and review.

[Technical details (Spanish) →](docs/TECNICO.md) · [LinkedIn](https://www.linkedin.com/in/miquel-moreno-martinez) · License: [Apache-2.0](LICENSE)
