"""Router and provider adapters: OpenAI, Anthropic, Ollama and OpenAI-compatible.

Destinations are fixed in the configuration.

Forbidden: Never accept a destination URL from the client (SSRF). Never forward the client's key.
"""
