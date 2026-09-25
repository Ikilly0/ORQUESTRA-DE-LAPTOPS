# Orquesta de laptops: Keyboard Instruments

> **T1: Propuesta de obra *Order*** → [`T1-propuesta-de-obra/`](T1-propuesta-de-obra/README.md): el PDF de la tarea, los referentes (CliX y Freesia), la propuesta y un render de 75 s hecho con `render_tema.py`.

App para tocar instrumentos **en vivo con el teclado del computador**, pensada para una orquesta de laptops:
cada persona toca una parte de un tema (sacada de un MIDI) y la app muestra las notas que vienen cayendo.
Funciona en **Windows 10** y **Linux Mint / Ubuntu** (Python 3.10+, Tkinter, numpy, sounddevice, mido).

## Cómo abrirla

- **Windows 10:** doble clic en `run_windows.bat` (usa Python 3.12 de python.org; la primera vez instala todo).
- **Linux Mint:** una vez `sudo apt install python3-venv python3-tk libportaudio2`, después `./run_linux.sh`.
- A mano: `pip install -r requirements.txt` y `python app.py`.

La ventana se abre maximizada; **F11** alterna pantalla completa.

## Uso

1. **Elegir tema:** lista de canciones (cada `.mid` de `songs/`, por ejemplo *Order - Ultrakill* y
   *Freesia - Guilty Gear*) o *Sin tema* para tocar libre. La primera vez que se abre un tema, la app crea
   **un instrumento por cada pista del MIDI**, con el timbre de su instrumento General MIDI y las notas que esa
   pista más usa (teclas A…L y luego Q…P, de grave a agudo).
2. **Menú del tema:**
   - **Tocar instrumento:** eliges instrumento y modo.
     - *Manual:* tú tocas; las notas de la pista caen hacia la línea y el teclado dibujado abajo muestra qué tecla es.
     - *Automático (práctica):* la app toca la pista sola, al tempo que elijas; puedes tocar encima.
     - Mientras tocas: **+ / −** a la izquierda o derecha agregan o quitan notas (graves desde M, N, B…;
       agudas en las teclas libres). Espacio = palm mute, Enter = power chords, flechas ↑/↓ = octava.
   - **Configurar instrumentos:** nombre, sonido, cantidad de teclas, notas, y *Configurar teclado*.
   - **♫ Escuchar tema completo:** la app toca todos los instrumentos del tema a la vez, como mesa de mezcla:
     una columna por instrumento con *Suena* y un fader de volumen (se guarda por tema), BPM, *Desde* y barra
     para saltar.

### Batería
La pista de batería se divide en dos instrumentos: **tambores** (bombo, bombo 2, caja, toms) y
**platillos** (hi-hat, hi-hat abierto, ride, campana del ride, crash). Para usar grabaciones reales en vez de
los sonidos sintetizados, pon `samples/<pieza>.wav` (ver `samples/LEEME.txt`).

### Pistas juntas
Pistas que una sola persona puede tocar porque se turnan se juntan en un instrumento (en Order:
**Guitarra 2 + solista**). Se configura en `THEME_MERGES` de `profiles.py`.

### Agregar un tema
Copia el `.mid` a `songs/`. Tempo y cambios de tempo del MIDI se respetan.

## Archivos

| Archivo | Qué hace |
| --- | --- |
| `app.py` | Interfaz: menús, editor, ventana de tocar (notas que caen), escuchar tema completo |
| `profiles.py` | Instrumentos guardados (`profiles.json`), distribuciones de teclado, instrumentos por pista |
| `instruments.py` | Síntesis: piano, guitarras (Karplus-Strong + distorsión), cuerdas, coro, bajo, batería, etc. |
| `audio.py` | Motor de audio: mezcla de voces, reloj de la canción, notas programadas al sample |
| `song.py` | Lectura del MIDI: tempo, compás, acordes, pistas y notas |

`profiles.json` y `settings.json` se crean al usar la app y guardan tus instrumentos y volúmenes (no se suben al repo).

## Latencia
48 kHz, bloques de 512 muestras. En Windows intenta primero WASAPI (menor latencia). El dispositivo y la
latencia aparecen abajo en el menú.
