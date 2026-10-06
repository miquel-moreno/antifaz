# Diseño del panel (prototipo)

Prototipo estático del panel de Antifaz (issue #30, sección 9.1 de la especificación). Es la **fuente de verdad visual**: el panel de la v0.2 se construye para que se vea igual que esto.

**Estado: pendiente de aprobación.** Aprobado por Miquel el: \_\_\_ / \_\_\_ / 2026. Hasta que esta línea tenga fecha, no se construye nada del panel.

## Cómo abrirlo

Desde la raíz del repo:

```
python -m http.server 8765 --bind 127.0.0.1
```

y abre <http://127.0.0.1:8765/design/prototype/antifaz-panel.html>. Hace falta el servidor porque la página carga `design/tokens.css` por ruta relativa; abierta con doble clic también funciona en la mayoría de navegadores. No usa nada de internet: ni fuentes web, ni CDN, ni librerías.

## Archivos

| Archivo | Qué es |
|---|---|
| `prototype/antifaz-panel.html` | La página: las cinco secciones del panel, los estados y el apartado del logo |
| `tokens.css` | Colores, tipos de letra, tamaños, espacios, radios, sombras y duraciones, en claro y oscuro |
| `logo-option-a.svg`, `logo-option-b.svg` | Dos propuestas de logo (el actual sigue en `docs/images/logo.svg`) |
| `captures/` | **Pendiente**: capturas de referencia de cada estado, que se harán con Playwright cuando Miquel apruebe el prototipo |

## Controles del prototipo

La barra de arriba (con borde discontinuo) **no es parte del panel**: sirve para enseñar cada caso.

- **Estado**: vacío, con datos, bloqueo, error y cadena rota.
- **Simular móvil**: limita el panel a 390 px. Un móvil de verdad ve lo mismo, porque el diseño se adapta al ancho del panel y no al de la pantalla.
- **Reducir movimiento**: enseña lo que ve quien tiene activado «reducir movimiento» en su sistema.
- El botón de tema (claro u oscuro) está en la cabecera del panel, porque sí es parte del producto. Al abrir, sigue el tema del sistema.

## Las secciones

1. **Prueba un texto** (Playground). Un texto de ejemplo con datos inventados (nombre, DNI, email e IBAN). Debajo, la animación del antifaz y tres columnas: lo que escribes (datos resaltados con su tipo), lo que recibe la IA (marcadores como `[[ES_DNI_1]]`) y lo que te vuelve (datos devueltos a su sitio). Al final, la leyenda de tipos encontrados y una línea con el resumen.
2. **En directo**. Contadores del día (datos protegidos por tipo, bloqueos, envíos a IA local), estado de la cadena de evidencias y la lista de últimas peticiones. Solo tipos y cantidades: nunca un valor ni un marcador. Aviso de que, si nadie mira, no se gasta CPU.
3. **Claves**. Una fila por equipo: nombre, id, qué puede usar, política, límites, último uso y estado. La clave nunca aparece (ni siquiera un trozo).
4. **Políticas**. Qué hacer con cada tipo de dato (ocultar, permitir, bloquear o IA local), las reglas de seguridad que no se pueden cambiar y la vista previa del YAML, que cambia al tocar las opciones.
5. **Estado**. Pasarela (`/healthz`), detector de nombres (NER, con sus cuatro estados explicados), proveedores de IA, evidencias y base de datos. Sin sondeos en segundo plano: se comprueba al pulsar.

Más abajo, fuera del panel, el apartado **Logo** enseña el actual y las dos propuestas a 64, 32 y 16 px, sobre claro, sobre oscuro y en un solo color.

## Los estados

| Estado | Qué se ve |
|---|---|
| Vacío | Playground sin texto y con una explicación; en directo a cero; sin claves; aviso de que se usa la política por defecto |
| Con datos | El caso normal, con la animación al abrir el Playground |
| Bloqueo | El texto habla de salud y la política `rrhh` lo bloquea: «no se ha enviado nada», con el motivo fijo (`special_category`); el evento aparece arriba en directo |
| Error | Anthropic no responde: aviso arriba, la tercera columna sin respuesta, el evento con error 504 y el proveedor en rojo en Estado |
| Cadena rota | Aviso rojo arriba con qué ha pasado y qué hacer, el contador de la cadena en rojo y el aviso en la lista y en Estado |
| Móvil | Cualquiera de los anteriores con «Simular móvil»: pestañas arriba, una sola columna y la tabla de claves en tarjetas |

## La animación del antifaz (F3)

El antifaz del logo pasa por encima de la frase y, a su paso, el nombre, el DNI, el email y el IBAN se convierten en marcadores. Debajo aparece la respuesta de la IA con marcadores, el antifaz vuelve a pasar y los datos aparecen otra vez. Dura 7 segundos, se repite con «Ver otra vez» y está hecha solo con CSS (dos capas de texto que se recortan al mismo ritmo que se mueve el antifaz). En reposo enseña el final: lo que recibe la IA y lo que te llega.

Con «reducir movimiento» no se anima nada: la animación se oculta y quedan las tres columnas, que son el antes y el después estático. Para lectores de pantalla, la animación está oculta y la sustituye una frase que cuenta lo mismo.

## Reglas

- **Nunca se muestra un dato personal ni un marcador ligado a un valor fuera del Playground** (invariante 15). En el Playground solo hay datos inventados.
- Colores solo desde `tokens.css`. Las excepciones son el propio logo y los fondos fijos del apartado Logo, que existen para probarlo sobre claro y oscuro.
- Contraste WCAG AA en los dos temas (tabla abajo).
- Nada se comunica solo con color: cada estado lleva icono y palabra, y los datos resaltados llevan su tipo escrito.
- Teclado: las pestañas usan tabindex itinerante (flechas, Inicio y Fin), todo tiene foco visible, hay enlace «Saltar al contenido» y cada control tiene su etiqueta. La lista en directo es `role="log"` con `aria-live="polite"`.
- Sin fuentes web ni librerías. En el panel real el JavaScript y los estilos van en archivos aparte (CSP estricta, sin JS en línea); en el prototipo están dentro de la página para que sea un solo archivo.
- Coste cero si nadie mira: el flujo en directo se abre al entrar en la sección y se cierra al salir.

## Contraste (WCAG 2.x)

AA pide 4,5:1 para texto normal y 3:1 para bordes de controles. Calculado con la fórmula de luminancia relativa de WCAG.

| Par | Claro | Oscuro |
|---|---|---|
| Texto / fondo de página | 15,50 | 15,85 |
| Texto / tarjeta | 16,86 | 14,69 |
| Texto / fondo hundido | 14,47 | 13,20 |
| Texto secundario / fondo de página | 6,50 | 7,81 |
| Texto secundario / tarjeta | 7,07 | 7,24 |
| Texto secundario / fondo hundido | 6,07 | 6,50 |
| Acento (enlaces, foco) / tarjeta | 7,77 | 7,71 |
| Texto del botón principal / acento | 7,77 | 8,00 |
| Marcador y pestaña activa (texto / fondo) | 8,45 | 8,92 |
| Correcto (texto / fondo suave) | 5,28 | 7,85 |
| Aviso (texto / fondo suave) | 5,64 | 8,96 |
| Peligro (texto / fondo suave) | 5,59 | 7,22 |
| Texto / resaltado de dato | 14,98 | 10,66 |
| Tipo del dato / resaltado de dato | 5,71 | 7,73 |
| Texto / aviso rojo | 14,41 | 13,47 |
| Texto / aviso ámbar | 14,79 | 12,36 |
| Borde de controles / tarjeta (3:1) | 3,20 | 3,53 |

## Logo: dos propuestas

El actual (`docs/images/logo.svg`) es un antifaz redondeado blanco sobre un cuadro índigo. Funciona, pero a 16 px los ojos casi desaparecen.

- **A · antifaz geométrico** (`logo-option-a.svg`): el mismo gesto, más simétrico y con los ojos más grandes. Es una sola forma con los ojos recortados, así que la versión de un color es la misma forma. Mantiene la continuidad con lo ya publicado.
- **B · ojos de tachado** (`logo-option-b.svg`): un antifaz anguloso sin fondo cuyos ojos son dos barras de tachado, el signo de «dato oculto». Más propio y menos de carnaval. Sin fondo, necesita un color claro sobre oscuro: el SVG lo cambia solo con el tema del sistema.

Las dos se leen a 16 px y en un solo color. Si se elige una, sustituye a `docs/images/logo.svg` y queda cubierta por `LICENSE-ASSETS.md` («y cualquier versión posterior»).

## Pendiente de decidir (Miquel)

- Logo: actual, A o B.
- Si «Prueba un texto» llama a la IA de verdad (gasta) o usa una respuesta de prueba local. El prototipo enseña una respuesta de ejemplo y el destino real.
- Qué nombre de cliente se enseña en directo (`Claude Code`, `Codex`…): el prototipo pone el nombre de la clave y, si se sabe, el del programa.
- Qué hace la pasarela cuando la cadena de evidencias se rompe (seguir sirviendo o parar). El prototipo solo avisa.
