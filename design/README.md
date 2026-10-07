# Diseño del panel (prototipo)

Prototipo estático del panel de Antifaz (issue #30, sección 9.1 de la especificación). Es la **fuente de verdad visual**: el panel de la v0.2 se construye para que se vea igual que esto.

**Estado: pendiente de aprobación.** Aprobado por Miquel el: \_\_\_ / \_\_\_ / 2026. Hasta que esta línea tenga fecha, no se construye nada del panel.

**Rediseño en curso** (encargo de Miquel, octubre de 2026): un estilo más cercano a Apple, casi monocromo, con la ventana de vidrio esmerillado sobre una luz ambiental muy suave. Se hace en seis pasos, cada uno con el visto bueno de Miquel:

1. Tokens nuevos (claro y oscuro), fuentes locales y contraste. **Hecho** (aprobado por Miquel).
2. Ventana, barra lateral, cabecera y móvil. **Hecho** (aprobado por Miquel, con los ajustes de abajo).
3. Vista «Prueba un texto» con sus cinco estados. **Hecho** (aprobado por Miquel, con los ajustes de abajo).
4. Animación del antifaz y entrada. **Hecho** (aprobado por Miquel, con los ajustes de abajo).
5. Las otras cuatro vistas con el mismo lenguaje. **Hecho** (esta versión).
6. Comprobación final.

Desde el paso 5, las cinco vistas usan el lenguaje nuevo. Queda la comprobación final (paso 6).

### Paso 2: la ventana

- **Ventana de vidrio esmerillado**: el color de la ventana al 80 % con un desenfoque de 40 px, sobre la luz ambiental (tres manchas muy suaves, **quietas**: ver «Ajustes tras la revisión del paso 4») y un grano finísimo. Esquinas de 24 px y la sombra `--shadow-window`.
- **Barra lateral translúcida**: la pestaña activa es una píldora blanca con sombra (`--shadow-pop`) y el icono en índigo. Atajos `1`–`5`; flechas, Inicio y Fin con tabindex itinerante. La marca lleva el logo, «Antifaz» y la versión.
- **Cabecera**: fija arriba y translúcida, con la ruta (Antifaz · vista), el estado y el botón de tema. El estado es un punto con un anillo que late **y** una palabra: verde «Todo en orden» (vacío, con datos y bloqueo: un bloqueo es la pasarela haciendo su trabajo), ámbar «Anthropic no responde» (error) y rojo «Cadena de evidencias rota». Se esconde al bajar y vuelve al subir (solo `transform` y `opacity`); no se esconde con «reducir movimiento» ni mientras el foco está dentro.
- **Móvil** (ventana de 640 px o menos, con container queries): una barra fija con la marca, el estado corto y el tema, y las pestañas en una fila con scroll horizontal debajo; la pestaña elegida se mantiene a la vista. La barra se esconde al bajar igual que la cabecera. Sin scroll horizontal de la página (comprobado a 390, 768 y 1280 px, en los cinco estados y las cinco vistas).
- **Nombres de clase propios** para cada pieza de la ventana (`app-*`, `ambient-*`, `proto-*`): en la referencia, las puertas de la entrada se llamaban `.top` como la cabecera y heredaban su `z-index`.
- **Logo corregido**: el símbolo del antifaz tiene `viewBox="10 22 52 26"`, así que cada `<svg>` que lo usa con `<use>` lleva `viewBox="0 0 52 26"` (antes salía cortado).

### Paso 2: ajustes tras la revisión de Miquel

- **Bloqueo**: la cabecera sigue en verde, «Todo en orden». Bloquear es la pasarela funcionando.
- **Barra lateral fija en escritorio**: al bajar por una vista larga, la barra lateral se queda quieta (`position: sticky`, como mucho la altura de la pantalla). La columna mantiene su tono y su línea en toda la ventana. En el móvil no cambia: sigue siendo la barra de arriba que se esconde al bajar.
- **La fila del prototipo** puede ocupar dos líneas a 1280 px: se deja así (no es parte del panel).
- **Logo nuevo adoptado**: ver «Logo» más abajo.
- **Control segmentado** (política `default`/`rrhh`, acción por tipo de dato, columnas en el móvil) y **pestañas en píldora** (las del prototipo y la pestaña activa de la barra lateral): la opción elegida es la píldora blanca con sombra de la referencia (`--shadow-pop`), sin el borde oscuro. Ver «Control segmentado y WCAG 1.4.11» en las reglas.

### Paso 3: «Prueba un texto»

- **Compositor**: un solo bloque con el texto y una barra abajo: la política (`default`/`rrhh`, control segmentado con la píldora que se desliza con rebote), «Usar el ejemplo» («Ejemplo» en el móvil) y «Probar». `Ctrl` + `Intro` prueba (`⌘` + `Intro` en Mac; la pista cambia sola). El anillo índigo suave sale solo cuando el foco está en el texto; los botones de la barra llevan su propio anillo. En el móvil la etiqueta «Texto de prueba» no se ve, pero sigue ahí para el lector de pantalla.
- **«Así viaja tu texto»**: tarjeta con el borde en degradado y un brillo arriba. En este paso enseña **su estado de reposo**, que es el último fotograma de la animación: lo que recibe la IA (con marcadores) y la respuesta con los datos de vuelta (subrayado verde). La animación y «Ver otra vez» llegan en el paso 4; hasta entonces el botón está oculto. Con «reducir movimiento» la tarjeta no sale y queda la nota de siempre.
- **Las tres columnas** (escribes, recibe la IA, te vuelve): en escritorio, una sola superficie con líneas finas; en tableta, una debajo de otra; **en el móvil, un control segmentado que enseña una columna cada vez** (botones con `aria-pressed`, que controlan las columnas). Se abre siempre en «Recibe la IA», también en el bloqueo, porque ahí está el aviso de «no se ha enviado nada».
- **Datos encontrados**: chips con el número en un círculo. En el móvil pasan a la línea siguiente si no caben (antes hacían scroll de lado sin que se notara ni se pudiera usar con el teclado).
- **Metadatos**: una línea gris, separada solo por espacio.
- **Avisos**: bloques suaves del color del estado, con icono, título y pasos (cadena rota) o explicación (error). El bloqueo es una nota roja dentro de la columna 2. El estado vacío explica las tres cosas que se verán y ofrece «Usar el ejemplo».
- **Lector de pantalla**: un mensaje `role="status"` dice el resultado cada vez que cambia (al probar, al usar el ejemplo o al cambiar de estado), por ejemplo «Bloqueado: no se ha enviado nada…». No se dice al abrir la página.
- **Privacidad**: los resultados solo enseñan el ejemplo inventado. Lo que alguien escriba en el cuadro no se copia a ningún sitio de la página: «Probar» enseña el resultado del ejemplo.
- Sin scroll horizontal ni textos cortados a 390, 768 y 1280 px, en los cinco estados y en los dos temas; consola limpia.

### Paso 3: ajustes tras la revisión de Miquel

- **Compositor sin el borde gris de 3:1**: el bloque del texto lleva solo una línea finísima (`--color-line`) y la sombra suave de las tarjetas (`--shadow-card`), como la referencia. Ver «El cuadro de texto y WCAG 1.4.11» en las reglas.
- **Se quedan como están**: en el móvil, el bloqueo abre en «Recibe la IA»; la pestaña activa de la barra lateral con peso 600; «Probar» con el cuadro vacío carga el ejemplo.
- **Un solo archivo del logo**: se borra `design/logo.svg` (era una copia de `docs/images/logo.svg`) y las dos propuestas antiguas (`logo-option-a.svg`, `logo-option-b.svg`), que nada usaba. Los favicons de las dos páginas, el apartado Logo y los comentarios apuntan a `docs/images/logo.svg`; `design/logo-mark.svg` (un color) se queda.
- **Texto pequeño sin espaciado negativo**: el texto de 11 a 13 px (pies, chips, metadatos, etiquetas, `kbd`, la etiqueta «Panel», insignias) usa el token nuevo `--tracking-small` (0). El −0,011em del cuerpo va bien de 15 px para arriba, pero en letra pequeña junta demasiado las letras. Las insignias en mayúsculas y mono mantienen su espaciado.

### Paso 4: la animación del antifaz y la entrada

Decisiones de Miquel: **Web Animations API** del navegador (`element.animate`, sin GSAP ni ninguna dependencia nueva); la entrada **solo la primera vez por sesión**; y solo se animan `transform` y `opacity`, con una única excepción: el ancho de cada dato mientras se convierte en su marcador (y al revés), que es corto y pequeño.

- **Entrada (≈ 2 s, solo la primera vez por sesión)**: el antifaz aparece, se ilumina en índigo de izquierda a derecha y sale «ANTIFAZ»; la pantalla se abre como dos puertas (arriba y abajo); la interfaz entra escalonada y el título sube palabra a palabra. Se decide en el `<head>`, antes de pintar nada, con `sessionStorage` dentro de un `try/catch`: si el navegador no deja usarlo, no hay entrada (mejor que repetirla en cada carga) y la página se ve bien igual. Las puertas tienen nombres propios (`.intro-door-upper`, `.intro-door-lower`). Es decoración: `aria-hidden`, no recibe clics ni atrapa el foco, y si el script fallara se desvanece sola a los 4 s. La animación del antifaz empieza 0,2 s después de que se abran las puertas, atada a ellas y no al reloj (una página abierta en una pestaña de fondo no anima hasta que se ve).
- **Animación del antifaz (7,5 s, medido en el navegador)**, en este orden: los datos destellan en ámbar uno tras otro; el antifaz cruza el texto con una estela de luz (se mueve con `transform: translateX`, nunca con `left`); **cada dato cambia en su sitio justo cuando el antifaz lo alcanza** (se desenfoca y se va, su hueco se estrecha hasta el ancho del marcador y el marcador entra con un pequeño rebote); el rótulo pasa de «Tú escribes» a «La IA recibe»; tres puntos viajan hacia Anthropic; sube la respuesta y el antifaz vuelve: cada marcador se abre hasta el dato real, con el subrayado verde y un brillo; y el rótulo pasa a «Te llega, con tus datos». «Ver otra vez» la repite (su icono gira) tantas veces como se quiera.
- **Cómo se calcula cuándo llega el antifaz a cada dato.** El antifaz recorre la línea con una curva (`--ease-sweep`): en cada fracción del tiempo ha avanzado una fracción de la línea. Para saber *cuándo* llega a un dato que está a cierta distancia, se invierte la curva (se busca por bisección el punto de la curva con esa distancia y se lee su tiempo). Como al cambiar de ancho los datos pueden mover las líneas, el cálculo avanza en el tiempo: en cada momento coloca la línea como estará entonces, invierte la curva para los datos que faltan y salta al siguiente. Medido en el navegador: el antifaz está a 0–5 px del punto previsto de cada dato a 390, 768, 1024 y 1280 px (el cálculo avanza a saltos de 40 ms como mucho; 5 px son unos 10 ms).
- **Sin saltos de diseño.** Antes de animar se miden las dos versiones de cada línea (con datos y con marcadores) y la línea se queda con la altura de la mayor. Así la tarjeta no cambia de altura y nada de lo que hay debajo se mueve (medido: 0 px en los cinco estados, a 390, 768, 1024 y 1280 px y con «Móvil»). A cambio, en reposo puede quedar un poco de aire bajo la primera frase cuando la versión con datos ocupa una línea más.
- **En reposo** la tarjeta enseña el último fotograma. Al terminar, las animaciones se cancelan y queda el CSS de reposo, que es la misma imagen: no hay salto final.
- **Rendimiento**: solo `transform` y `opacity` (más el ancho de los datos). El desenfoque es una copia del dato con un desenfoque fijo que solo cambia de opacidad; el destello ámbar y el brillo verde son capas con una sombra fija que solo cambian de opacidad. Las manchas de luz del fondo no se mueven (ver los ajustes de abajo). La animación se **pausa** si se esconde la pestaña (y sigue donde estaba al volver) y se **cancela** al cambiar de vista, de estado o de ancho; vuelve a empezar al volver a «Prueba un texto». Prepararla cuesta unos 30 ms, una vez por reproducción.
- **Detalles solo con ratón** (`(hover: hover) and (pointer: fine)`): «Probar» es magnético y lo cruza un brillo; las tarjetas tienen una luz que sigue al cursor (movida con `transform`); la tarjeta de la animación se inclina unos 2°; el icono de «Ver otra vez» gira. El control segmentado ya se deslizaba con rebote desde el paso 2.
- **Accesibilidad**: nada importante va solo en el movimiento (las tres columnas dicen lo mismo, siempre). La animación es `aria-hidden` y la sustituye una frase para lectores de pantalla. El texto se puede seleccionar. Con «reducir movimiento» del sistema **o** con el interruptor «Menos movimiento» del prototipo no se anima nada: ni entrada, ni antifaz, ni detalles; se ve el antes y el después estáticos (las tres columnas) con la nota de siempre. Si se activa a mitad, todo se para y queda el estado final.
- **La subida de las piezas (paso 2) ya no usa desenfoque**: anima solo `transform` y `opacity`.
- Curvas nuevas en `tokens.css`: `--ease-pop` (rebote pequeño), `--ease-in-out` (ancho de los datos), `--ease-sweep` (el recorrido del antifaz), `--ease-door` (puertas) y `--ease-expo-out` (título).

### Paso 4: ajustes tras la revisión de Miquel

- **Se quedan como están**: la altura reservada de cada línea de la animación (sin saltos; vale que quede algo de aire bajo la primera frase) y la precisión del antifaz, que llega a **0–5 px** del punto previsto de cada dato (medido en el navegador; no 1–2 px).
- **Fondo quieto**: las tres manchas de luz ya no se mueven (antes derivaban sin parar con `transform`). Con la ventana de vidrio esmerillado encima, un fondo que se mueve obliga al navegador a recalcular el desenfoque de 40 px en cada fotograma mientras la pestaña está a la vista, y eso choca con «si nadie mira, 0 % de CPU».
- **Excepción en «solo `transform` y `opacity`»**: las transiciones de color y de sombra al pasar el ratón (botones, pestañas, filas) se permiten, porque son cortas, las provoca quien usa el panel y no mueven el diseño. Nada más anima color, sombra, fondo, ancho ni posición (aparte del ancho de los datos en la animación del antifaz).

### Paso 5: las otras cuatro vistas

Mismo lenguaje que «Prueba un texto»: superficies blancas con la sombra suave de las tarjetas, esquinas de 18 px, líneas finas solo donde separan y casi sin color. El color solo sale para decir un estado (punto o icono, siempre con su palabra). Se mantienen los textos, el contenido y las reglas de cada vista.

- **Etiquetas tranquilas** (`.tag`) en lugar de las pastillas de color: icono y palabra sobre un gris neutro; el color va solo en el icono (índigo para «Ocultar», ámbar para el error, rojo para bloqueo y alerta). Así una lista llena de etiquetas no se convierte en un arcoíris.
- **En directo**: los contadores del día son una sola superficie dividida por líneas (datos protegidos con sus barras por tipo; bloqueos; IA local; cadena de evidencias). La cadena dibujada inclina el eslabón roto y apaga los de después, que ya no sirven como prueba. Las barras se escalan con `transform: scaleX()` desde la izquierda (nunca con el ancho). **Llega una petición nueva**: la fila entra desde arriba, las demás le hacen sitio (con `transform`), el total sube con un pequeño salto y la fila se ilumina con una **capa propia que solo cambia de opacidad** (no se anima el fondo). Solo tipos y cantidades. La simulación solo corre si la vista está abierta **y** la pestaña del navegador se ve (`visibilitychange`); si no, no hay ningún temporizador. El vacío explica qué se verá.
- **Claves**: tabla con cabecera gris suave y título «Claves virtuales» con el recuento. «Revocar» es un botón neutro (una acción destructiva no va en índigo). La tabla necesita unos 780 px: cuando la superficie es más estrecha (tableta con la barra lateral, móvil), cada fila pasa a ser una tarjeta compacta (nombre, estado y «Revocar» arriba; los cuatro datos debajo con su etiqueta). Lo decide el ancho de la propia superficie (container query), no el de la pantalla. La clave nunca aparece.
- **Políticas**: una barra de herramientas arriba con la política que se edita, su revisión y huella, el mensaje de cambios y «Guardar como revisión N» (antes estaba debajo del YAML; así se ve siempre). Una fila cambiada lleva un velo índigo suave, una barrita a la izquierda (capas que solo cambian de opacidad) **y la palabra «cambiado»**, para no depender del color. En el YAML, cada línea cambiada lleva su resaltado como capa aparte, que aparece con un fundido solo cuando la línea acaba de cambiar. Las reglas fijas van en columnas (qué, acción, candado). **En el móvil**, un control segmentado como el de «Prueba un texto» enseña una parte cada vez: Tipos, Reglas o Archivo.
- **Estado**: las cuatro tarjetas con el estado en la cabecera (punto con anillo y palabra). Las barras de latencia se escalan con `transform: scaleX()` desde la izquierda y crecen al entrar en la vista y al pulsar «Comprobar ahora» (nunca solas). Con Anthropic caído, su barra queda vacía y el estado en rojo con icono.
- **Luz que sigue al cursor** (solo con ratón) también en las superficies de estas vistas.
- **Accesibilidad**: la lista en directo sigue siendo `role="log"` con `aria-live="polite"`; el resaltado de la fila nueva es `aria-hidden`. Las pestañas siguen con tabindex itinerante; los controles segmentados son botones de opción o botones con `aria-pressed`; el NER marca el estado actual con `aria-current` y la palabra «Ahora». Texto pequeño sin espaciado negativo. Sin contraste nuevo: todas las parejas de color nuevas ya están en la tabla (por ejemplo, texto terciario sobre fondo 2, 4,52 y 4,53).
- **Movimiento**: solo `transform` y `opacity` (más los cambios de color o sombra al pasar el ratón). Con «reducir movimiento» o «Menos movimiento» no se anima nada: las filas nuevas aparecen sin más y, si se activa a mitad, lo que estaba en marcha salta a su final.
- **Antes**, sin que lo pidiera nadie, se animaban algunas cosas que no eran `transform` ni `opacity` y que no eran al pasar el ratón: el color del control segmentado y de los chips del prototipo, el fondo de los interruptores, la sombra del cuadro de texto al enfocarlo, el ancho y las esquinas de la ventana al pulsar «Móvil» y la posición del enlace «Saltar al contenido». Ahora cambian sin transición (el enlace se mueve con `transform`).
- Sin scroll horizontal, sin textos cortados ni piezas montadas a 390, 768 y 1280 px y con «Móvil», en los cinco estados, las cinco vistas y los dos temas; consola limpia.

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
| `logo-mark.svg` | Solo el antifaz, en un color (`currentColor`: toma el color del texto donde se use). El logo con el cuadro está en `docs/images/logo.svg` |
| `captures/` | **Pendiente**: capturas de referencia de cada estado, que se harán con Playwright cuando Miquel apruebe el prototipo |

## Controles del prototipo

La fila de arriba, fuera de la ventana y con el borde discontinuo y la etiqueta «Solo prototipo», **no es parte del panel**: sirve para enseñar cada caso.

- **Estado**: vacío, con datos, bloqueo, error y cadena rota.
- **Móvil (390 px)**: estrecha la ventana a 390 px. Un móvil de verdad ve lo mismo, porque el diseño se adapta al ancho de la ventana (container queries) y no al de la pantalla.
- **Menos movimiento**: enseña lo que ve quien tiene activado «reducir movimiento» en su sistema. Si el sistema ya lo tiene, el interruptor sale activado y bloqueado.
- El botón de tema (claro u oscuro) está en la cabecera del panel, porque sí es parte del producto. Al abrir, sigue el tema del sistema.
- Al abrir la página por primera vez en la sesión hay una entrada animada de unos 2 s (el antifaz, las puertas y las piezas en orden). Nunca con «reducir movimiento». Para volver a verla, abre la página en una pestaña nueva.

## Las secciones

1. **Prueba un texto** (Playground). Cuadro de texto con selector de política (`default`/`rrhh`), «Usar el ejemplo» y «Probar» (también con Ctrl o ⌘ + Intro); la tarjeta «Así viaja tu texto»; las tres columnas (lo que escribes, lo que recibe la IA, lo que te vuelve) como una sola superficie; la línea de datos encontrados y los metadatos. En el móvil, las tres columnas pasan a un selector de columna para no hacer scroll. Detalle en «Paso 3».
2. **En directo**. Cabecera con «Conectado» y la fecha. Una superficie con los contadores del día: datos protegidos (número grande y barras por tipo), bloqueos, envíos a IA local y la cadena de evidencias (con una cadena dibujada que marca el eslabón roto). Debajo, «Últimas peticiones»: hora, clave y programa, qué ha pasado y una etiqueta tranquila (ocultados N, bloqueado, IA local, sin datos, error, alerta). **Solo tipos y cantidades: nunca un valor ni un marcador.** En el prototipo llega una petición inventada cada 6 segundos mientras se mira la vista y los contadores suben; al salir de la vista o esconder la pestaña, se para. Al pie, el aviso de que si nadie mira no se gasta CPU (0 %).
3. **Claves**. Botón «Crear clave» y una tabla en una superficie: nombre e id, qué puede usar, política, límites, último uso, estado (punto y palabra) y «Revocar». Una clave revocada sale tachada y en gris. **La clave nunca aparece, ni un trozo**: el pie lo explica. Cuando no cabe (tableta con la barra lateral, móvil), cada fila pasa a ser una tarjeta compacta con sus etiquetas.
4. **Políticas**. Selector de la política que se edita (`default`/`rrhh`) con su revisión, huella y cuántas claves la usan; la explicación de las cuatro acciones; una fila por tipo de dato con el control de cuatro opciones **Ocultar / Permitir / Bloquear / IA local**; las reglas de seguridad fijas (con candado) y la de categorías especiales (IA local o bloquear); y la vista previa del YAML, que cambia al tocar una opción y resalta en índigo las líneas cambiadas. «Guardar como revisión N» (en la barra de arriba) solo se activa si hay cambios, y un mensaje dice cuántos hay. Las filas cambiadas dicen «cambiado». En el móvil, un selector enseña Tipos, Reglas o Archivo.
5. **Estado**. Cuatro tarjetas: pasarela (versión, `/healthz`, dirección, base de datos), detector de nombres (NER) con sus cuatro estados explicados y el actual marcado, proveedores de IA con su latencia (barra escalada con `transform` y milisegundos) y evidencias. Se comprueba solo al pulsar «Comprobar ahora»: no hay sondeos en segundo plano.

Debajo de la ventana, fuera del panel, el apartado **Logo** (ver más abajo).

## Los estados

| Estado | Qué se ve |
|---|---|
| Vacío | Playground sin texto y con una explicación; en directo a cero y sin peticiones; sin claves (con «Crear la primera clave»); aviso en Políticas de que se usa la política por defecto |
| Con datos | El caso normal: la tarjeta «Así viaja tu texto» con la animación del antifaz, las tres columnas y los datos encontrados |
| Bloqueo | El texto habla de salud y la política `rrhh` lo bloquea: «no se ha enviado nada», con el motivo fijo (`special_category`); el evento aparece arriba en directo y los bloqueos suben a 4 |
| Error | Anthropic no responde: aviso ámbar arriba en todas las vistas, la tercera columna sin respuesta, el evento «Error 504», un punto en la pestaña Estado y Anthropic en rojo en proveedores |
| Cadena rota | Aviso rojo arriba con qué ha pasado y qué hacer, el estado de la cabecera en rojo, la cadena rota en En directo (eslabón marcado), el evento «Alerta» y Evidencias en rojo en Estado |
| Móvil | Cualquiera de los anteriores con «Móvil»: pestañas en fila arriba, una columna, selector de columna en el Playground, selector de parte en Políticas y la tabla de claves en tarjetas |

## La animación del antifaz (F3)

Detalle en «Paso 4». Solo sale en los estados con datos (con datos y cadena rota): en vacío, bloqueo y error no hay nada que animar. Se reproduce al abrir la página, al pulsar «Probar» o «Usar el ejemplo», al cambiar a un estado con datos, al volver a la vista y con «Ver otra vez». En reposo enseña el final.

Con «reducir movimiento» no se anima nada: la animación se oculta y quedan las tres columnas, que son el antes y el después estático. Para lectores de pantalla, la animación está oculta y la sustituye una frase que cuenta lo mismo.

## Reglas

- **Nunca se muestra un dato personal ni un marcador ligado a un valor fuera del Playground** (invariante 15). En el Playground solo hay datos inventados. En directo, Claves, Políticas y Estado solo hay tipos, cantidades, nombres de clave e ids.
- Colores solo desde `tokens.css`, también el degradado del logo (`--logo-from`, `--logo-to`) y los brillos de la animación (`--color-scan-glow`, `--color-restore-glow`). Las únicas excepciones son los archivos SVG del logo (y sus copias en el apartado Logo) y los fondos fijos claro y oscuro de ese apartado, que existen para probarlo.
- `--color-text-faint` (texto terciario) nunca va sobre `--color-surface-3`: ahí no llega a 4,5:1.
- Contraste WCAG AA en los dos temas (tabla abajo).
- Nada se comunica solo con color: cada estado lleva icono o punto **y** palabra; los datos resaltados llevan su tipo escrito; el punto de aviso en la pestaña Estado lleva texto para lectores de pantalla.
- Los interruptores apagados tienen un borde de 3:1 (`--color-control`).
- **El cuadro de texto y WCAG 1.4.11.** Antes el bloque del texto llevaba el borde de 3:1; Miquel pidió quitarlo y dejar la línea finísima y la sombra de la referencia. 1.4.11 pide 3:1 para lo que hace falta para **identificar** un control, y aquí el campo se identifica sin el borde: tiene su etiqueta visible «Texto de prueba» (en el móvil la etiqueta no se ve, pero está para el lector de pantalla), el texto de ejemplo dentro («Por ejemplo: Hola, soy…», que pasa 4,5:1), y es una tarjeta blanca propia con su barra de botones debajo. Al enfocarlo sale el anillo índigo, que sí pasa 3:1. Es la misma lectura que se hizo con el control segmentado (abajo): el borde sería un refuerzo, no lo único que lo identifica.
- **Control segmentado y WCAG 1.4.11.** Antes, la píldora de la opción elegida llevaba un borde oscuro de 3:1 para cumplir 1.4.11 (contraste de lo que no es texto), porque la píldora blanca sobre el carril gris no llega a 3:1. Miquel pidió quitarlo. Ahora la píldora es la de la referencia (blanca con `--shadow-pop`, sin borde) y **la opción elegida se reconoce sin mirar la píldora**: su texto va en `--color-text` y peso 600, y el de las demás en `--color-text-muted` y peso 500. Los dos textos pasan 4,5:1 sobre su fondo (texto / tarjeta 16,71 y 16,41; texto secundario / fondo de pistas 5,33 y 5,30). Como el estado ya lo dice el propio texto, que cumple 1.4.3, la píldora es un refuerzo y no tiene que llegar a 3:1. No se depende solo del color (1.4.1): entre los dos grises hay 2,7:1 en claro y 2,5:1 en oscuro, y el cambio de peso es la señal que no es de color. Además siguen siendo botones de opción de verdad (`input type="radio"`, que el lector de pantalla anuncia como «marcado») o botones con `aria-pressed` (las columnas en el móvil), así que el lector de pantalla dice cuál está elegida, y el anillo de foco no cambia. Lo mismo vale para las pestañas en píldora.
- Teclado: las pestañas usan tabindex itinerante (flechas, Inicio y Fin) y las teclas 1 a 5 saltan a cada sección; todo tiene foco visible (anillo índigo); hay enlace «Saltar al contenido»; cada control tiene su etiqueta. La lista en directo es `role="log"` con `aria-live="polite"`; los mensajes de «Comprobar ahora» y de cambios sin guardar son `role="status"`.
- Movimiento: solo se animan `transform` y `opacity`. Excepciones: el ancho de cada dato en la animación del antifaz y las transiciones de color y sombra al pasar el ratón (cortas, provocadas por quien usa el panel y sin mover el diseño). El fondo no se mueve. Todo se apaga con `prefers-reduced-motion` y con el interruptor del prototipo. Las animaciones de JavaScript (Web Animations API) lo comprueban también, porque el CSS de «reducir movimiento» solo para las animaciones y transiciones de CSS.
- En el panel real el JavaScript y los estilos van en archivos aparte (CSP estricta, sin JS en línea); en el prototipo están dentro de la página para que sea un solo archivo.
- Coste cero si nadie mira: el flujo en directo se abre al entrar en la sección y se cierra al salir o al esconder la pestaña del navegador.

## Tipos de letra

- **Inter** para la interfaz y los títulos (variable, con eje de tamaño óptico: con `font-optical-sizing: auto` los títulos grandes usan su corte Display) y **Geist Mono** para marcadores, códigos y el YAML. Las dos tienen licencia **SIL Open Font License 1.1**.
- **Van dentro del proyecto**, en `design/fonts/`, y se cargan con `design/fonts/fonts.css` (`font-display: swap`). Nada de Google Fonts: Antifaz no hace peticiones a terceros. Si no cargan, se usa la lista de fuentes del sistema de `--font-sans` y `--font-mono` (`tokens.css`).
- Ajustes de Inter en los tokens: `--font-features` (`"cv05", "cv08"`), `--tracking-body` (−0,011em), `--tracking-small` (0, para el texto de 11 a 13 px), `--font-numeric` (`tabular-nums`, también la clase `.tabular-nums`) y la escala de títulos `--text-title` (`clamp(30px, 4.4cqi, 50px)`, peso 650, −0,034em).
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

**Logo del proyecto** (aprobado por Miquel el 7 de octubre de 2026): el antifaz blanco en un cuadro redondeado con degradado índigo (155°, de `#7363f7` a `#3b2db0`) y un borde interior muy suave, el mismo que lleva el panel. Los ojos son un poco más grandes que en la muestra para que se lean a 16 px. La versión de un color (`logo-mark.svg`) es solo el antifaz con los ojos recortados y toma el color del texto (`currentColor`).

Ya es el logo publicado y vive en un solo sitio: **`docs/images/logo.svg`** (mismo nombre que el anterior, así que los README y `LICENSE-ASSETS.md` siguen enlazando bien). Ya no hay copia en `design/`. Mide 128 px y los README lo enseñan a 120 px. Como el cuadro tiene su propio fondo, se ve igual con el tema claro y con el oscuro de GitHub. `LICENSE-ASSETS.md` ya lo cubre: habla de `docs/images/logo.svg` «and any later version of it» («y sus versiones futuras»), así que no hace falta cambiarlo. El favicon de las dos páginas del prototipo es el logo publicado.

El apartado Logo del prototipo lo enseña a 64, 32 y 16 px sobre claro y sobre oscuro, y la versión de un color sobre los dos fondos.

## Pendiente de decidir (Miquel)

- Aprobar el prototipo (la línea de arriba).
- Si «Prueba un texto» llama a la IA de verdad (gasta) o usa una respuesta de prueba local. El prototipo enseña una respuesta de ejemplo y el destino real.
- Qué nombre de cliente se enseña en directo (`Claude Code`, `Codex`…): el prototipo pone el nombre de la clave y, si se sabe, el del programa.
- Qué hace la pasarela cuando la cadena de evidencias se rompe (seguir sirviendo o parar). El prototipo solo avisa.
