# Capturas de referencia del prototipo

Fotos fijas de `design/prototype/antifaz-panel.html` para comparar el panel de verdad con el prototipo. Solo salen los datos de ejemplo inventados del prototipo (nombres, DNI, email e IBAN de prueba); ningún dato real.

Cómo se hacen (las 24 a la vez, en menos de un minuto; unos 2,6 MB en total):

```
make design-captures
```

Usa el mismo script que `make design-check` (`scripts/check_design.py --captures`, Playwright y Pillow en sus metadatos; la primera vez, `uv run --with playwright==1.63.0 playwright install chromium`). Para que salgan siempre iguales: movimiento reducido activado (imagen final, sin animaciones), los temporizadores parados antes de cada foto (en «En directo» no entra ninguna petición nueva) y el reloj de ejemplo es texto fijo. Se fotografía solo la ventana del panel, sin los controles del prototipo de arriba.

- **Escritorio**: pantalla de 1280 × 800 a 1x; la ventana mide 1148 px de ancho. JPEG calidad 85 (el codificador de Chromium), de 65 a 155 KB cada una.
- **Móvil**: pantalla de 390 × 844 a 2x (la imagen mide 716 px de ancho por el margen de la página). WebP calidad 90, hecho con Pillow a partir del PNG sin pérdida que da Playwright: de 84 a 137 KB cada una (en PNG eran de 280 a 730 KB, sobre todo por el grano del fondo en oscuro). Al 100 % no se distingue del PNG; con JPEG, para bajar de 150 KB hacía falta calidad 70 y se notaba en el texto. GitHub y los navegadores actuales muestran WebP.
- La foto es de la ventana entera, de arriba abajo, aunque sea más alta que la pantalla.
- **Sin metadatos**: el JPEG solo lleva la cabecera JFIF (el script quita cualquier segmento APP1-APP15 y comentario: EXIF, XMP, ICC...) y el WebP solo los bloques de la imagen (el script falla si aparece otro).
- **Siempre los mismos bytes**: dos ejecuciones seguidas dan archivos idénticos (comprobado con `md5sum` en las 24 el 7 de octubre de 2026). Los dos codificadores son deterministas, así que una captura solo cambia en git si cambia el prototipo.

## Las cinco vistas, estado «Con datos»

| Vista | Claro escritorio | Claro móvil | Oscuro escritorio | Oscuro móvil |
|---|---|---|---|---|
| Prueba un texto: el ejemplo, lo que recibe la IA y lo que vuelve | `playground-light-desktop.jpg` | `playground-light-mobile.webp` | `playground-dark-desktop.jpg` | `playground-dark-mobile.webp` |
| En directo: contadores, cadena de evidencias y últimas peticiones | `live-light-desktop.jpg` | `live-light-mobile.webp` | `live-dark-desktop.jpg` | `live-dark-mobile.webp` |
| Claves: las claves virtuales (en el móvil, como tarjetas) | `keys-light-desktop.jpg` | `keys-light-mobile.webp` | `keys-dark-desktop.jpg` | `keys-dark-mobile.webp` |
| Políticas: la política `default`, las cuatro acciones y los tipos de dato | `policies-light-desktop.jpg` | `policies-light-mobile.webp` | `policies-dark-desktop.jpg` | `policies-dark-mobile.webp` |
| Estado: comprobaciones de la pasarela | `status-light-desktop.jpg` | `status-light-mobile.webp` | `status-dark-desktop.jpg` | `status-dark-mobile.webp` |

## Los otros estados de «Prueba un texto» (claro, escritorio)

| Archivo | Qué enseña |
|---|---|
| `playground-empty-light-desktop.jpg` | Vacío: todavía no se ha probado nada; invita a usar el ejemplo |
| `playground-blocked-light-desktop.jpg` | Bloqueo: con la política `rrhh`, un texto que habla de salud no se envía a la IA |
| `playground-error-light-desktop.jpg` | Error: Anthropic no responde (aviso arriba y estado en ámbar) |
| `playground-broken-light-desktop.jpg` | Cadena rota: aviso rojo con los pasos a seguir y el punto rojo en «Estado» |
