# Diseño del panel (prototipo)

Prototipo estático del panel de Antifaz (issue #30, sección 9.1 de la especificación). Es la **fuente de verdad visual**: el panel de la v0.2 se construye para que se vea igual que esto.

**Estado: pendiente de aprobación.** Aprobado por Miquel el: \_\_\_ / \_\_\_ / 2026. Hasta que esta línea tenga fecha, no se construye nada del panel.

**Rediseño en curso** (encargo de Miquel, octubre de 2026): un estilo más cercano a Apple, casi monocromo, con la ventana de vidrio esmerillado sobre una luz ambiental muy suave. Se hace en seis pasos, cada uno con el visto bueno de Miquel:

1. Tokens nuevos (claro y oscuro), fuentes locales y contraste. **Hecho** (esta versión).
2. Ventana, barra lateral, cabecera y móvil.
3. Vista «Prueba un texto» con sus cinco estados.
4. Animación del antifaz y entrada.
5. Las otras cuatro vistas con el mismo lenguaje.
6. Comprobación final.

Hasta el paso 5, el prototipo ya usa los tokens y las fuentes nuevas, pero la ventana, las vistas y la animación son todavía las anteriores: es normal que se vea a medias.

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

y abre <http://127.0.0.1:8765/design/prototype/antifaz-panel.html> (el panel) o <http://127.0.0.1:8765/design/prototype/tokens-preview.html> (los tokens). Hace falta el servidor porque las páginas cargan `design/tokens.css`, las fuentes de `design/fonts/` y el icono por ruta relativa. No se pide nada fuera de tu equipo.

## Archivos

| Archivo | Qué es |
|---|---|
| `prototype/antifaz-panel.html` | La página: las cinco secciones del panel, los estados y el apartado del logo |
| `prototype/tokens-preview.html` | Página de revisión de los tokens: colores de los dos temas lado a lado, tipografía, radios, sombras, estados y la tabla de contraste calculada en vivo. No es parte del panel |
| `tokens.css` | Colores, tipos de letra, tamaños, espacios, radios, sombras, vidrio y movimiento, en claro y oscuro. Es la única fuente de colores |
| `fonts/` | Inter y Geist Mono (woff2 oficiales, sin modificar), sus licencias OFL y `fonts.css` con los `@font-face` |
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
- Colores solo desde `tokens.css`, también el degradado del logo (`--logo-from`, `--logo-to`) y los brillos de la animación (`--color-scan-glow`, `--color-restore-glow`). Las únicas excepciones son los archivos SVG del logo (y sus copias en el apartado Logo) y los fondos fijos claro y oscuro de ese apartado, que existen para probarlo.
- `--color-text-faint` (texto terciario) nunca va sobre `--color-surface-3`: ahí no llega a 4,5:1.
- Contraste WCAG AA en los dos temas (tabla abajo).
- Nada se comunica solo con color: cada estado lleva icono o punto **y** palabra; los datos resaltados llevan su tipo escrito; el punto de aviso en la pestaña Estado lleva texto para lectores de pantalla.
- Los controles tienen un borde de 3:1 (`--color-control`): el indicador de los controles de píldora, los interruptores apagados y el cuadro de texto del Playground. Es un poco más marcado que en la muestra, a propósito.
- Teclado: las pestañas usan tabindex itinerante (flechas, Inicio y Fin) y las teclas 1 a 5 saltan a cada sección; todo tiene foco visible (anillo índigo); hay enlace «Saltar al contenido»; cada control tiene su etiqueta. La lista en directo es `role="log"` con `aria-live="polite"`; los mensajes de «Comprobar ahora» y de cambios sin guardar son `role="status"`.
- Movimiento: todo se apaga con `prefers-reduced-motion` y con el interruptor del prototipo.
- En el panel real el JavaScript y los estilos van en archivos aparte (CSP estricta, sin JS en línea); en el prototipo están dentro de la página para que sea un solo archivo.
- Coste cero si nadie mira: el flujo en directo se abre al entrar en la sección y se cierra al salir o al esconder la pestaña del navegador.

## Tipos de letra

- **Inter** para la interfaz y los títulos (variable, con eje de tamaño óptico: con `font-optical-sizing: auto` los títulos grandes usan su corte Display) y **Geist Mono** para marcadores, códigos y el YAML. Las dos tienen licencia **SIL Open Font License 1.1**.
- **Van dentro del proyecto**, en `design/fonts/`, y se cargan con `design/fonts/fonts.css` (`font-display: swap`). Nada de Google Fonts: Antifaz no hace peticiones a terceros. Si no cargan, se usa la lista de fuentes del sistema de `--font-sans` y `--font-mono` (`tokens.css`).
- Ajustes de Inter en los tokens: `--font-features` (`"cv05", "cv08"`), `--tracking-body` (−0,011em), `--font-numeric` (`tabular-nums`, también la clase `.tabular-nums`) y la escala de títulos `--text-title` (`clamp(30px, 4.4cqi, 50px)`, peso 650, −0,034em).
- **Por qué los archivos oficiales enteros y no los recortes por alfabeto** (aprobado por Miquel al revisar el paso 1). Los recortes de Google Fonts / Fontsource (latín y latín extendido por separado, 238 KB) **quitan las variantes `cv05` y `cv08`** de Inter, que pide el diseño. Los archivos oficiales las tienen todas, también `tnum` y el eje `opsz`, y caben de sobra en el límite (414 KB los dos). Cubren latín, latín extendido y más, así que no hace falta `unicode-range`.

| Archivo | Fuente | Versión | Tamaño | SHA-256 |
|---|---|---|---|---|
| `InterVariable.woff2` | [rsms/inter, release v4.1](https://github.com/rsms/inter/releases/tag/v4.1) (`Inter-4.1.zip` → `web/InterVariable.woff2`) | 4.1 (la fuente dice «Version 4.001») | 352 240 B | `693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3` |
| `Inter-OFL.txt` | el mismo zip (`LICENSE.txt`) | 4.1 | 4 380 B | `262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a` |
| `GeistMono-Variable.woff2` | [vercel/geist-font, release v1.7.2](https://github.com/vercel/geist-font/releases/tag/v1.7.2) (`geist-font-v1.7.2.zip` → `geist-font/GeistMono/webfonts/GeistMono[wght].woff2`, renombrado) | 1.7.2 (la fuente dice «Version 1.700») | 71 368 B | `fba8f577f38a2bbcbe818efa6348dd58f36303a10b8737c42fefad275be563ab` |
| `GeistMono-OFL.txt` | el mismo zip (`geist-font/OFL.txt`) | 1.7.2 | 4 383 B | `c683bfbcc7e087f5d37a54ef628f10387c451a83ddc459b151403a164ac46c90` |

SHA-256 de los zips descargados: `Inter-4.1.zip` `9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e`; `geist-font-v1.7.2.zip` `7fc800d2ac6b92844895196e5041aca55d814c15db70c44f79b3b83ab82b04e2` (coincide con el que publica GitHub en la release). Las dos licencias no declaran ningún «Reserved Font Name».

## Contraste (WCAG 2.x)

AA pide 4,5:1 para texto normal y 3:1 para bordes de controles, iconos e indicadores. Calculado con la fórmula de luminancia relativa de WCAG. Los fondos con transparencia se mezclan como en la referencia y en el peor caso: la luz ambiental (`--color-ambient-1`) entera sobre el fondo, la ventana al 80 % encima, la barra lateral al 60 % sobre la ventana, las tarjetas al 88 % y el escenario de la animación al 90 %. La página `prototype/tokens-preview.html` calcula esta misma tabla en vivo con los tokens.

| Par | Claro | Oscuro |
|---|---|---|
| Texto / ventana | 15,82 | 17,39 |
| Texto / barra lateral | 15,77 | 17,18 |
| Texto / tarjeta | 16,71 | 16,41 |
| Texto / fondo 2 | 15,47 | 15,24 |
| Texto / fondo de pistas | 14,16 | 13,10 |
| Texto / escenario de la animación | 16,24 | 17,32 |
| Texto secundario / ventana | 5,96 | 7,04 |
| Texto secundario / barra lateral | 5,93 | 6,95 |
| Texto secundario / tarjeta | 6,29 | 6,64 |
| Texto secundario / fondo 2 | 5,82 | 6,17 |
| Texto secundario / fondo de pistas | 5,33 | 5,30 |
| Texto secundario / escenario de la animación | 6,11 | 7,01 |
| Texto terciario / ventana | 4,62 | 5,17 |
| Texto terciario / barra lateral | 4,60 | 5,11 |
| Texto terciario / tarjeta | 4,88 | 4,87 |
| Texto terciario / fondo 2 | 4,52 | 4,53 |
| Texto terciario / escenario de la animación | 4,74 | 5,15 |
| Acento como texto / tarjeta | 8,23 | 10,63 |
| Marcador (acento como texto / fondo suave, tarjeta) | 7,10 | 8,34 |
| Marcador en la animación (fondo suave / escenario) | 6,91 | 8,96 |
| Acento (icono de la pestaña activa) / tarjeta (3:1) | 6,14 | 6,85 |
| Texto del botón principal / acento | 6,19 | 7,38 |
| Foco (acento) / ventana (3:1) | 5,82 | 7,26 |
| Correcto / ventana (estado de la cabecera) | 4,85 | 9,88 |
| Correcto / barra lateral | 4,83 | 9,76 |
| Correcto / fondo suave | 4,57 | 7,19 |
| Aviso / ventana (estado de la cabecera) | 5,75 | 10,62 |
| Aviso / fondo suave (aviso arriba) | 5,18 | 8,19 |
| Aviso / fondo suave sobre tarjeta (nota) | 5,44 | 7,55 |
| Peligro / ventana (estado de la cabecera) | 5,51 | 6,79 |
| Peligro / fondo suave (aviso arriba) | 4,83 | 5,67 |
| Peligro / fondo suave sobre tarjeta (nota) | 5,09 | 5,27 |
| Texto / aviso ámbar | 14,26 | 13,41 |
| Texto / aviso rojo | 13,89 | 14,50 |
| Texto secundario / nota ámbar | 5,63 | 5,00 |
| Texto secundario / nota roja | 5,50 | 5,46 |
| Texto / resaltado de dato | 14,50 | 11,10 |
| Tipo del dato / resaltado de dato | 5,27 | 8,13 |
| Subrayado verde (dato devuelto) / tarjeta (3:1) | 5,12 | 9,32 |
| Interruptor encendido / su mando (3:1) | 5,15 | 3,02 |
| Interruptor encendido / ventana (3:1) | 4,85 | 6,26 |
| Borde de controles / tarjeta (3:1) | 4,17 | 4,32 |
| Borde de controles / fondo de pistas (3:1) | 3,53 | 3,45 |

### Cambios respecto a la referencia

Con los valores exactos de la referencia, estos pares no llegaban a AA. Se ha cambiado lo mínimo (solo la luminosidad, mismo tono) para pasar:

| Token | Tema | Referencia | Ahora | Peor par antes → ahora |
|---|---|---|---|---|
| `--color-text-muted` | claro | `#6b6b72` | `#5f5f66` | sobre fondo de pistas: 4,45 → 5,33 (más oscuro de lo justo, para que se note el escalón con el terciario) |
| `--color-text-faint` | claro | `#8b8b92` | `#707077` | sobre fondo 2: 3,11 → 4,52 (sobre ventana: 3,18 → 4,62) |
| `--color-text-faint` | oscuro | `#77777f` | `#85858c` | sobre fondo 2: 3,74 → 4,53 (sobre tarjeta: 4,02 → 4,87) |
| `--color-ok` | claro | `#1b8738` | `#197e34` | sobre fondo suave: 4,07 → 4,57 (sobre ventana: 4,32 → 4,85) |
| `--color-switch-on` | claro | `#1b8738` (el verde) | `#197e34` | sigue al verde: 4,60 → 5,15 contra el mando |
| `--color-switch-on` | oscuro | `#32d74b` (el verde) | `#21ab36` | contra el mando blanco: 1,92 → 3,02 |

El resto de colores de la referencia pasan tal cual: acento, marcadores, ámbar, rojo, resaltado de dato y todos los de texto principal. `--color-control` (borde de 3:1 de los controles) no está en la referencia y se mantiene del prototipo anterior.

**Escalón entre los grises en claro**: para llegar a 4,5:1 el terciario tuvo que oscurecerse hasta `#707077`, casi igual que el secundario. Por eso el secundario se ha oscurecido a `#5f5f66` (decisión de Miquel al revisar el paso 1): entre los dos hay ahora un contraste de 1,29:1 (antes 1,09:1), así que se distinguen a simple vista. En oscuro ya había escalón (`#9d9da6` frente a `#85858c`).

## Logo

**Propuesta principal** (`logo.svg`): el antifaz blanco en un cuadro redondeado con degradado índigo (155°, de `#7363f7` a `#3b2db0`) y un borde interior muy suave, el mismo que lleva el panel. Los ojos son un poco más grandes que en la muestra para que se lean a 16 px. La versión de un color (`logo-mark.svg`) es solo el antifaz con los ojos recortados y toma el color del texto (`currentColor`).

El apartado Logo del prototipo enseña la propuesta a 64, 32 y 16 px sobre claro y sobre oscuro, la versión de un color sobre los dos fondos y, debajo, el logo publicado ahora (`docs/images/logo.svg`) para comparar.

**`docs/images/logo.svg` no se ha cambiado**: se sustituye solo cuando Miquel lo apruebe. Entonces queda cubierto por `LICENSE-ASSETS.md` («y cualquier versión posterior»). Las propuestas A y B anteriores se guardan en esta carpeta como referencia.

## Pendiente de decidir (Miquel)

- Aprobar el prototipo (la línea de arriba).
- Logo: aprobar la propuesta principal para sustituir `docs/images/logo.svg`.
- Rediseño, pasos 2 a 6: GSAP o Web Animations API, y si la entrada con puertas sale solo la primera vez por sesión (recomendado).
- Si «Prueba un texto» llama a la IA de verdad (gasta) o usa una respuesta de prueba local. El prototipo enseña una respuesta de ejemplo y el destino real.
- Qué nombre de cliente se enseña en directo (`Claude Code`, `Codex`…): el prototipo pone el nombre de la clave y, si se sabe, el del programa.
- Qué hace la pasarela cuando la cadena de evidencias se rompe (seguir sirviendo o parar). El prototipo solo avisa.
