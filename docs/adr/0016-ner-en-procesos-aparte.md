# ADR-0016 · El NER en procesos aparte, con caché y manifiesto

- **Estado:** Propuesta
- **Fecha:** 2026-09-30
- **Concreta:** el ADR-0003 (pool de procesos con tiempo máximo)
- **Modifica:** la regla de solapamientos parciales del ADR-0010 (el perdedor se recorta, no se descarta)

## Contexto

Los nombres y las direcciones no tienen dígito de control: hacen falta un modelo de reconocimiento de entidades (NER, ADR-0003). Un modelo así es lento, usa mucha memoria, puede colgarse con un texto raro y está escrito en librerías grandes (PyTorch) que no controlamos. Además:

- GLiNER **corta sin avisar** los textos de más de 384 palabras: un nombre en la palabra 400 no se ve.
- Claude Code y los SDK reenvían **todo el historial** en cada turno: sin caché, el mismo texto pasa por el modelo una y otra vez.
- Un modelo descargado de internet es código y datos de terceros: hay que saber que es exactamente el que se revisó.
- Hasta ahora, cuando dos detecciones se pisaban en parte, la perdedora se descartaba entera y el trozo que no se pisaba salía en claro. Con el NER (spans largos y de bordes imprecisos) esto pasaría mucho más.
- El NER no ve siempre todas las apariciones de un nombre. Si ve "Carmen Prueba" en un mensaje y no en otro, la guardia de salida bloquea la petición entera (hace bien: el valor saldría en claro).

## Decisión

**Procesos aparte que se pueden matar.**

- Un pool propio de procesos (`multiprocessing` con `spawn`, igual en Windows y Linux), con `ANTIFAZ_NER_WORKERS` procesos (1 por defecto). Cada proceso carga el modelo **una vez** con una fábrica que se importa por ruta (`paquete.módulo:función`).
- No se usa `ProcessPoolExecutor`: no deja matar un proceso colgado hasta Python 3.14 (`kill_workers`). Con procesos propios, si una llamada pasa de `ANTIFAZ_NER_TIMEOUT_SECONDS`, el proceso se mata (`kill()`), se arranca otro y la petición se bloquea (invariante 7: cierre por defecto). Lo mismo si el proceso muere (`os._exit`, falta de memoria) o devuelve algo mal formado.
- Padre e hijo hablan con **JSON** por un `Pipe` (`send_bytes`/`recv_bytes`, con tamaño máximo), nunca con `pickle`: un modelo comprometido no puede ejecutar código en el proceso principal. El hijo solo devuelve códigos de error fijos, **nunca el texto de una excepción** (podría llevar el dato). El hijo no escribe nada: su `stdout` y `stderr` van a `/dev/null` y el logging está desactivado.
- En la API, el enmascarado corre en un hilo (`anyio.to_thread.run_sync`), así que ni el NER ni los patrones bloquean el bucle de eventos.

**Trozos que se solapan.** El texto se parte en ventanas de 200 palabras (contando cada signo como una) que se solapan 50. Una entidad de menos de 50 palabras en la frontera se ve entera en alguna ventana. Las posiciones se devuelven al texto completo y las detecciones del mismo tipo que se pisan se unen.

**Sobre la vista normalizada.** El NER lee la misma vista del ADR-0014 que los patrones (sin invisibles, con homoglifos pasados a latín), y sus posiciones vuelven al original con el mismo mapa.

**Caché en el proceso principal, delante del pool.**

- Clave: BLAKE2b **con clave aleatoria por proceso** (32 bytes, nueva en cada arranque) sobre el texto, el hash del manifiesto del modelo, las etiquetas, el umbral y la versión del troceador. Cambiar cualquiera de ellos invalida la caché. Sin la clave, un volcado de memoria no permite comprobar si un DNI o un nombre concretos pasaron por aquí.
- Solo guarda **posiciones y tipos**, nunca el texto ni los valores.
- LRU con tamaño máximo (`ANTIFAZ_NER_CACHE_ENTRIES`, 10.000 por defecto; 0 la desactiva).
- En v0.2, con claves virtuales, habrá una caché por clave: con una caché compartida, un cliente podría saber por el tiempo de respuesta si otro envió ya el mismo texto (canal lateral de tiempo). En v0.1 hay una sola clave y el problema no existe.

**Manifiesto del modelo.** `detect/ner/manifest.json` lista cada archivo del modelo con su tamaño y SHA-256. Antes de cargar se comprueba: falta un archivo, sobra uno o no coincide el tamaño o el hash → no arranca. Los pesos se cargan con `weights_only` y sin red (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`). La descarga es un comando aparte y explícito (issue 6b), nunca automática al arrancar. Si `ANTIFAZ_NER_ENABLED=true` y el modelo falta o no coincide, Antifaz **se niega a arrancar** (`UnsafeConfigError`): no arranca "sin NER" en silencio.

**Solapamientos: el perdedor se recorta.** Cuando dos detecciones se pisan **en parte**, la ganadora sigue siendo la del ADR-0010 (validador, patrón y después NER; luego la más larga…), pero la perdedora ya no se descarta: sus trozos que no pisa nadie se quedan como detecciones del mismo tipo. Los trozos sin ninguna letra ni cifra (un espacio, un guion) se descartan. Así, todo carácter alfanumérico que alguna capa detectó queda enmascarado (test de propiedad). El NER va el último en prioridad y **nunca cambia** lo que encuentran validadores y patrones: la regla del contenedor del ADR-0010 solo se aplica entre validadores y patrones, y entre spans del NER. Un "nombre" que contiene un DNI ("Carmen 12345678Z") se recorta a "Carmen" y el DNI sigue siendo un DNI; si no, una política que permite nombres enviaría el DNI en claro (test de propiedad: con o sin spans del NER, los de validadores y patrones salen iguales).

**Propagación de los valores del NER.** Después de detectar, cada valor que el NER encontró (y la política manda ocultar) se busca en **todos** los textos de la petición y se enmascara también allí, con la misma regla que la guardia: normalización (NFKC, sin invisibles ni acentos, sin mayúsculas); valores de 6 o más letras y cifras, sin límites de palabra; más cortos, con límites ("Ana" no se busca dentro de "semana").

## Alternativas descartadas

- **`ProcessPoolExecutor`**: no mata un proceso colgado en Python 3.12; un timeout solo abandona el resultado y el proceso sigue ocupado.
- **Hilos**: no se puede matar un hilo, y el GIL frenaría el resto de la pasarela.
- **Caché con SHA-256 sin clave**: un hash sin clave de un dato de baja entropía (un DNI) se puede romper probando todos.
- **Guardar el texto en la caché**: sería una copia de los datos personales en memoria sin fecha de caducidad.
- **Arrancar sin NER si el modelo falta**: el usuario creería que los nombres se ocultan.
- **Descartar al perdedor de un solapamiento parcial** (regla anterior): deja en claro el trozo que no se pisa.

## Consecuencias

- Un NER colgado o roto bloquea esa petición (400 `antifaz_blocked`), nunca la pasarela: el proceso se sustituye y la siguiente petición funciona.
- Cada proceso ocupa la memoria de un modelo (unos cientos de MB en 6b): `ANTIFAZ_NER_WORKERS` controla el coste.
- Una petición enorme puede no caber en el tiempo máximo y se bloquea: el fallo seguro.
- Enmascarar de más: una palabra corriente que coincide con un nombre detectado ("Mar") se enmascara en toda la petición; un nombre de 6 letras o más se enmascara también dentro de otra palabra ("Marina" en "submarina"). Son falsos positivos aceptados: sin la propagación, la guardia bloquearía la petición entera.
- Un trozo recortado puede generar un marcador más (`[[PHONE_2]]` para el resto de un número), que se restaura igual.
- El cambio de regla de solapamiento puede añadir detecciones en el benchmark (nunca quitar texto enmascarado).
- En 6a solo existe el backend falso de los tests; el modelo real (GLiNER), su descarga y su manifiesto llegan en 6b.
