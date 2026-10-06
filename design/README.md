# Diseño del panel (prototipo)

Prototipo estático del panel de Antifaz (issue #30, sección 9.1 de la especificación). Es la **fuente de verdad visual**: el panel de la v0.2 se construye para que se vea igual que esto.

**Estado: pendiente de aprobación.** Aprobado por Miquel el: \_\_\_ / \_\_\_ / 2026. Hasta que esta línea tenga fecha, no se construye nada del panel.

## La dirección visual

Miquel eligió la muestra de estilo de «Prueba un texto» y el resto del panel se ha pasado a ese lenguaje:

- Una **ventana flotante** al estilo de macOS: barra lateral translúcida y vista principal, sobre un fondo con un brillo índigo suave.
- **Casi monocromo.** El índigo solo marca las acciones, el foco, los marcadores `[[...]]` y el propio antifaz. Los colores de estado (verde, ámbar, rojo) aparecen solo cuando hay algo que decir y siempre con icono y palabra.
- **Superficies con líneas finas**: las tres columnas del Playground, los contadores de «En directo», las filas de políticas y la tabla de claves son una sola superficie dividida por líneas, no tarjetas sueltas.
- **Controles en píldora** con un indicador que se desliza con un pequeño rebote (política, acción por tipo de dato, columnas en el móvil).
- **Tipografía**: Inter con tamaño óptico (los títulos usan su corte Display) y Geist Mono para marcadores, códigos, ids y el YAML.

## Cómo abrirlo

Desde la raíz del repo:

```
python -m http.server 8765 --bind 127.0.0.1
```

y abre <http://127.0.0.1:8765/design/prototype/antifaz-panel.html>. Hace falta el servidor porque la página carga `design/tokens.css` y el icono por ruta relativa.

## Archivos

| Archivo | Qué es |
|---|---|
| `prototype/antifaz-panel.html` | La página: las cinco secciones del panel, los estados y el apartado del logo |
| `tokens.css` | Colores, tipos de letra, tamaños, espacios, radios, sombras y movimiento, en claro y oscuro. Es la única fuente de colores |
| `logo.svg` | Logo principal propuesto: el antifaz blanco en el cuadro con degradado índigo |
| `logo-mark.svg` | Solo el antifaz, en un color (`currentColor`: toma el color del texto donde se use) |
| `logo-option-a.svg`, `logo-option-b.svg` | Las dos propuestas anteriores. Se guardan como referencia; el apartado Logo ya no las enseña |
| `captures/` | **Pendiente**: capturas de referencia de cada estado, que se harán con Playwright cuando Miquel apruebe el prototipo |

## Controles del prototipo

La fila de arriba, fuera de la ventana, **no es parte del panel**: sirve para enseñar cada caso.

- **Estado**: vacío, con datos, bloqueo, error y cadena rota.
- **Móvil (390 px)**: estrecha la ventana a 390 px. Un móvil de verdad ve lo mismo, porque el diseño se adapta al ancho de la ventana (container queries) y no al de la pantalla.
- **Menos movimiento**: enseña lo que ve quien tiene activado «reducir movimiento» en su sistema. Si el sistema ya lo tiene, el interruptor sale activado y bloqueado.
- El botón de tema (claro u oscuro) está en la cabecera del panel, porque sí es parte del producto. Al abrir, sigue el tema del sistema.
- Al abrir la página hay una entrada animada corta (la ventana sube y aparecen las piezas en orden). Solo una vez, y nunca con «reducir movimiento».

## Las secciones

1. **Prueba un texto** (Playground). Es la muestra elegida, casi sin cambios: cuadro de texto con selector de política (`default`/`rrhh`), «Usar el ejemplo» y «Probar» (también con Ctrl + Enter); la animación «Así viaja tu texto»; las tres columnas (lo que escribes, lo que recibe la IA, lo que te vuelve) como una sola superficie; la línea de datos encontrados y el resumen. En el móvil, las tres columnas pasan a un selector de columna para no hacer scroll.
2. **En directo**. Cabecera con «Conectado» y la fecha. Una superficie con los contadores del día: datos protegidos (número grande y barras por tipo), bloqueos, envíos a IA local y la cadena de evidencias (con una cadena dibujada que marca el eslabón roto). Debajo, «Últimas peticiones»: hora, clave y programa, qué ha pasado y una etiqueta (ocultados N, bloqueado, IA local, sin datos, error, alerta). **Solo tipos y cantidades: nunca un valor ni un marcador.** En el prototipo llega una petición inventada cada 6 segundos mientras se mira la vista y los contadores suben; al salir de la vista o esconder la pestaña, se para. Al pie, el aviso de que si nadie mira no se gasta CPU (0 %).
3. **Claves**. Botón «Crear clave» y una tabla en una superficie: nombre e id, qué puede usar, política, límites, último uso, estado (punto y palabra) y «Revocar». Una clave revocada sale tachada y en gris. **La clave nunca aparece, ni un trozo**: el pie lo explica. En el móvil, cada fila pasa a ser una tarjeta con sus etiquetas.
4. **Políticas**. Selector de la política que se edita (`default`/`rrhh`) con su revisión, huella y cuántas claves la usan; la explicación de las cuatro acciones; una fila por tipo de dato con el control de cuatro opciones **Ocultar / Permitir / Bloquear / IA local**; las reglas de seguridad fijas (con candado) y la de categorías especiales (IA local o bloquear); y la vista previa del YAML, que cambia al tocar una opción y resalta en índigo las líneas cambiadas. «Guardar como revisión N» solo se activa si hay cambios, y un mensaje dice cuántos hay.
5. **Estado**. Cuatro tarjetas: pasarela (versión, `/healthz`, dirección, base de datos), detector de nombres (NER) con sus cuatro estados explicados y el actual marcado, proveedores de IA con su latencia (barra y milisegundos) y evidencias. Se comprueba solo al pulsar «Comprobar ahora»: no hay sondeos en segundo plano.

Debajo de la ventana, fuera del panel, el apartado **Logo** (ver más abajo).

## Los estados

| Estado | Qué se ve |
|---|---|
| Vacío | Playground sin texto y con una explicación; en directo a cero y sin peticiones; sin claves (con «Crear la primera clave»); aviso en Políticas de que se usa la política por defecto |
| Con datos | El caso normal, con la animación al abrir el Playground |
| Bloqueo | El texto habla de salud y la política `rrhh` lo bloquea: «no se ha enviado nada», con el motivo fijo (`special_category`); el evento aparece arriba en directo y los bloqueos suben a 4 |
| Error | Anthropic no responde: aviso ámbar arriba en todas las vistas, la tercera columna sin respuesta, el evento «Error 504», un punto en la pestaña Estado y Anthropic en rojo en proveedores |
| Cadena rota | Aviso rojo arriba con qué ha pasado y qué hacer, el estado de la cabecera en rojo, la cadena rota en En directo (eslabón marcado), el evento «Alerta» y Evidencias en rojo en Estado |
| Móvil | Cualquiera de los anteriores con «Móvil»: pestañas en fila arriba, una columna, selector de columna en el Playground y la tabla de claves en tarjetas |

## La animación del antifaz (F3)

El antifaz pasa por encima de la frase como una línea de luz; a su paso, cada dato (nombre, DNI, email e IBAN) se convierte en su marcador justo cuando el antifaz lo alcanza. Tres puntos viajan hacia la IA, aparece la respuesta con marcadores, el antifaz vuelve a pasar y los datos vuelven a su sitio con un subrayado verde. Dura unos 7 segundos y se repite con «Ver otra vez». Está hecha con CSS (dos capas de texto que se recortan al ritmo del antifaz); el JavaScript solo calcula cuándo llega el antifaz a cada dato. En reposo enseña el final.

Con «reducir movimiento» no se anima nada: la animación se oculta y quedan las tres columnas, que son el antes y el después estático. Para lectores de pantalla, la animación está oculta y la sustituye una frase que cuenta lo mismo.

## Reglas

- **Nunca se muestra un dato personal ni un marcador ligado a un valor fuera del Playground** (invariante 15). En el Playground solo hay datos inventados. En directo, Claves, Políticas y Estado solo hay tipos, cantidades, nombres de clave e ids.
- Colores solo desde `tokens.css`. Las excepciones son el propio logo (su degradado es la marca y no cambia con el tema) y los fondos fijos claro y oscuro del apartado Logo, que existen para probarlo.
- `--color-text-faint` (texto terciario) nunca va sobre `--color-surface-3`: ahí no llega a 4,5:1.
- Contraste WCAG AA en los dos temas (tabla abajo).
- Nada se comunica solo con color: cada estado lleva icono o punto **y** palabra; los datos resaltados llevan su tipo escrito; el punto de aviso en la pestaña Estado lleva texto para lectores de pantalla.
- Los controles tienen un borde de 3:1 (`--color-control`): el indicador de los controles de píldora, los interruptores apagados y el cuadro de texto del Playground. Es un poco más marcado que en la muestra, a propósito.
- Teclado: las pestañas usan tabindex itinerante (flechas, Inicio y Fin) y las teclas 1 a 5 saltan a cada sección; todo tiene foco visible (anillo índigo); hay enlace «Saltar al contenido»; cada control tiene su etiqueta. La lista en directo es `role="log"` con `aria-live="polite"`; los mensajes de «Comprobar ahora» y de cambios sin guardar son `role="status"`.
- Movimiento: todo se apaga con `prefers-reduced-motion` y con el interruptor del prototipo.
- En el panel real el JavaScript y los estilos van en archivos aparte (CSP estricta, sin JS en línea); en el prototipo están dentro de la página para que sea un solo archivo.
- Coste cero si nadie mira: el flujo en directo se abre al entrar en la sección y se cierra al salir o al esconder la pestaña del navegador.

## Tipos de letra

- **Inter** (con tamaño óptico) y **Geist Mono**. Las dos tienen licencia **SIL Open Font License 1.1**, que permite incluirlas en el proyecto.
- En el **prototipo** se cargan de Google Fonts, con una lista completa de fuentes del sistema por si no cargan (`--font-sans` y `--font-mono` en `tokens.css`).
- En el **panel real** no: Antifaz no envía nada a ningún sitio, así que los archivos `woff2` irán dentro del proyecto y se servirán desde la propia pasarela, sin peticiones externas. `docs/licencias.md` las listará (OFL-1.1).
- **Pendiente del OK de Miquel** para añadir los archivos de fuentes (unos 0,5 MB). No se han descargado todavía.

## Contraste (WCAG 2.x)

AA pide 4,5:1 para texto normal y 3:1 para bordes de controles e indicadores. Calculado con la fórmula de luminancia relativa de WCAG; los colores con transparencia se han mezclado antes con el fondo sobre el que van.

| Par | Claro | Oscuro |
|---|---|---|
| Texto / ventana | 16,83 | 17,70 |
| Texto / tarjeta | 16,83 | 16,26 |
| Texto / fondo de pistas (controles de píldora) | 14,16 | 13,10 |
| Texto secundario / ventana | 6,33 | 7,52 |
| Texto secundario / fondo 2 | 5,82 | 6,48 |
| Texto secundario / fondo de pistas | 5,33 | 5,57 |
| Texto secundario / barra lateral | 5,92 | 7,24 |
| Texto terciario / barra lateral | 4,87 | 5,71 |
| Texto terciario / tarjeta | 5,21 | 5,45 |
| Texto terciario / fondo 2 | 4,79 | 5,11 |
| Acento como texto (enlaces, marcadores) / tarjeta | 8,29 | 10,54 |
| Acento (iconos activos) / tarjeta | 6,19 | 6,79 |
| Texto del botón principal / acento | 6,19 | 7,38 |
| Marcador y aviso índigo (texto / fondo suave) | 7,16 | 8,29 |
| Correcto / tarjeta | 5,08 | 9,24 |
| Correcto / fondo suave | 4,54 | 7,08 |
| Aviso / fondo suave | 6,14 | 8,39 |
| Peligro / tarjeta | 6,10 | 6,35 |
| Peligro / fondo suave | 5,32 | 5,80 |
| Texto / aviso ámbar | 15,10 | 13,74 |
| Texto / aviso rojo | 14,68 | 14,84 |
| Texto / resaltado de dato | 14,62 | 10,93 |
| Tipo del dato / resaltado de dato | 5,95 | 8,01 |
| Borde de controles / tarjeta (3:1) | 4,20 | 4,29 |
| Borde de controles / fondo de pistas (3:1) | 3,53 | 3,45 |
| Foco / ventana (3:1) | 6,19 | 7,39 |
| Interruptor encendido / su mando (3:1) | 5,08 | 4,40 |
| Interruptor encendido / ventana (3:1) | 5,08 | 4,38 |

Para cumplir AA se han tocado cuatro colores de la muestra: el texto secundario y el terciario (un poco más oscuros en claro y más claros en oscuro), el verde, el ámbar y el rojo del tema claro (un poco más oscuros) y el interruptor encendido del tema oscuro (un verde más oscuro, para que el mando blanco se distinga).

## Logo

**Propuesta principal** (`logo.svg`): el antifaz blanco en un cuadro redondeado con degradado índigo (155°, de `#7363f7` a `#3b2db0`) y un borde interior muy suave, el mismo que lleva el panel. Los ojos son un poco más grandes que en la muestra para que se lean a 16 px. La versión de un color (`logo-mark.svg`) es solo el antifaz con los ojos recortados y toma el color del texto (`currentColor`).

El apartado Logo del prototipo enseña la propuesta a 64, 32 y 16 px sobre claro y sobre oscuro, la versión de un color sobre los dos fondos y, debajo, el logo publicado ahora (`docs/images/logo.svg`) para comparar.

**`docs/images/logo.svg` no se ha cambiado**: se sustituye solo cuando Miquel lo apruebe. Entonces queda cubierto por `LICENSE-ASSETS.md` («y cualquier versión posterior»). Las propuestas A y B anteriores se guardan en esta carpeta como referencia.

## Pendiente de decidir (Miquel)

- Aprobar el prototipo (la línea de arriba).
- Logo: aprobar la propuesta principal para sustituir `docs/images/logo.svg`.
- OK para añadir los archivos de Inter y Geist Mono (~0,5 MB, OFL-1.1) al proyecto.
- Si «Prueba un texto» llama a la IA de verdad (gasta) o usa una respuesta de prueba local. El prototipo enseña una respuesta de ejemplo y el destino real.
- Qué nombre de cliente se enseña en directo (`Claude Code`, `Codex`…): el prototipo pone el nombre de la clave y, si se sabe, el del programa.
- Qué hace la pasarela cuando la cadena de evidencias se rompe (seguir sirviendo o parar). El prototipo solo avisa.
