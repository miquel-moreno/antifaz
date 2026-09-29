# Detalles técnicos

> Antifaz está en desarrollo (v0.1). Esta página crece con cada issue.

## Ponerlo en marcha (desarrollo)

```bash
make install   # dependencias + hooks de pre-commit
make check     # lint + tipos + tests + gitleaks
make audit     # vulnerabilidades conocidas en las dependencias (uv audit, experimental)
make licenses  # licencias de lo que se distribuye
make dev       # API en http://localhost:8000 (de momento solo /healthz)
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

## Decisiones técnicas del issue 1

| Decisión | Por qué |
|---|---|
| Acciones de GitHub fijadas por SHA completo y permisos mínimos | Una etiqueta (`v4`) se puede mover a otro código; un SHA no. Incidentes reales: tj-actions (2025) y Trivy (2026) |
| `persist-credentials: false` en cada checkout | El token de GitHub no queda guardado en el disco del runner para pasos posteriores |
| `uv audit` fijado a uv 0.12.20 | Comprueba vulnerabilidades conocidas (OSV) del `uv.lock`. Es un comando experimental de uv: si cambia, se ajusta aquí |
| Comprobación de licencias propia (`scripts/check_licenses.py`) | Regla sencilla y auditable: nada GPL/AGPL/no comercial en lo que se distribuye; copyleft débil solo si está anotado en `docs/licencias.md` |
| Cobertura ≥ 90 % en `detect`, `vault`, `mask`, `guard` y `restore` (`scripts/check_coverage.py`) | Son las piezas de privacidad: un fallo ahí significa datos personales enviados |
| CodeQL solo cuando el repo sea público | En repos privados necesita GitHub Advanced Security (de pago); mientras tanto el job se salta en lugar de fallar |
| Manejador propio de errores 422 | El de FastAPI devuelve el cuerpo recibido, que podría llevar un DNI. El nuestro solo dice qué campo está mal |
| Hooks de Claude Code con tests | Bloquean los casos comunes de leer `.env`, imprimir variables secretas, `--no-verify` y force push. Son **defensa en profundidad, no una barrera**: quien ejecuta código arbitrario puede saltárselos, y la revisión de privacidad encontró varios caminos (ver el modelo de amenazas). La barrera real es gitleaks en CI |
| `uv audit` también sobre las herramientas de desarrollo | A propósito: esas herramientas corren en la CI con acceso al código; una vulnerable también es un riesgo |
| Errores 422 sin claves del cliente y con tamaño máximo | En la ubicación del error solo quedan la parte (`body`, `query`…) y los índices; las claves de un diccionario las elige el cliente y podrían ser un DNI |
| `X-Request-ID` del cliente validado | Se escribe en logs y cabeceras: solo se acepta si es corto y sin símbolos; si no, se genera uno nuevo |

## Limitaciones

- Todavía no detecta ni enmascara nada: eso empieza en el issue 2.
