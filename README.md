# Orquesta de laptops: Keyboard Instruments

> **T1: Propuesta de obra *Order*** → [`T1-propuesta-de-obra/`](T1-propuesta-de-obra/README.md): el PDF de la tarea, los referentes (CliX y Freesia), la propuesta y un render de 75 s hecho con `render_tema.py`.

App para tocar instrumentos **en vivo con el teclado del computador**, pensada para una orquesta de laptops:
cada persona toca una parte de un tema (sacada de un MIDI) y la app muestra las notas que vienen cayendo.
Funciona en **Windows 10** y **Linux Mint / Ubuntu** (Python 3.10+, Tkinter, numpy, sounddevice, mido).

## Descargar (sin instalar Python)

| Sistema | Descarga | Cómo abrirla |
| --- | --- | --- |
| **Windows 10/11** | [KeyboardInstruments-Windows.zip](https://github.com/Ikilly0/ORQUESTRA-DE-LAPTOS/releases/latest/download/KeyboardInstruments-Windows.zip) | Descomprime la carpeta y abre **`KeyboardInstruments.exe`**. Si aparece «Windows protegió su PC»: *Más información* → *Ejecutar de todas formas*. |
| **macOS (Apple Silicon)** | [KeyboardInstruments-macOS.zip](https://github.com/Ikilly0/ORQUESTRA-DE-LAPTOS/releases/latest/download/KeyboardInstruments-macOS.zip) | Descomprime y abre la app con clic derecho → *Abrir*, solo la primera vez. |
| **Linux** | [KeyboardInstruments-Linux.tar.gz](https://github.com/Ikilly0/ORQUESTRA-DE-LAPTOS/releases/latest/download/KeyboardInstruments-Linux.tar.gz) | `tar xzf` y abre `KeyboardInstruments/KeyboardInstruments` (necesita `libportaudio2`). |

GitHub los construye solo (`.github/workflows/build.yml`) cada vez que se sube un cambio a `main`; tarda unos 10 minutos. Todas las versiones están en [Releases](https://github.com/Ikilly0/ORQUESTRA-DE-LAPTOS/releases).

**Dónde guarda tus cosas:**
- **Windows y Linux:** junto al programa (`profiles.json`, `settings.json` y la carpeta `songs/`, donde puedes agregar tus `.mid`).
- **Mac:** en `~/Library/Application Support/Keyboard Instruments`.

Para construirlo tú mismo: `pip install pyinstaller -r requirements.txt` y `python build_app.py`. El resultado queda en `dist/`; cada sistema construye solo su propia versión.

## Cómo abrirla desde el código

- **Windows 10:** doble clic en `run_windows.bat` (usa Python 3.12 de python.org; la primera vez instala todo).
- **Linux Mint:** una vez `sudo apt install python3-venv python3-tk libportaudio2`, después `./run_linux.sh`.
- **macOS 11 o más nuevo (Intel o Apple Silicon):** ver abajo.
- A mano: `pip install -r requirements.txt` y `python app.py`.

La ventana se abre maximizada; **F11** alterna pantalla completa (en Mac también **Cmd+Ctrl+F**).

### macOS

1. **Python con Tk:** instala **Python 3.12 desde [python.org](https://www.python.org/downloads/macos/)**, que ya incluye Tk 8.6. También sirve Homebrew: `brew install python@3.12 python-tk@3.12`. El `python3` que trae macOS (Xcode) no sirve, porque usa Tk 8.5; el lanzador lo detecta y te avisa.
2. **Abrir:** doble clic en **`run_macos.command`**. La primera vez crea `.venv-macos` e instala numpy, sounddevice y mido, lo que tarda 1 a 2 minutos y muestra un aviso. Las siguientes veces abre directo. Todo queda anotado en `run.log`.
3. **Si macOS dice que no puede abrirlo** (archivo bajado de internet): clic derecho → **Abrir** → **Abrir**, solo la primera vez. Si dice que no tiene permiso, en Terminal: `chmod +x run_macos.command make_macos_app.sh`.
4. **Ícono en el Dock (opcional):** en Terminal, dentro de la carpeta, `./make_macos_app.sh`. Crea **Keyboard Instruments.app**, que puedes arrastrar al Dock o a Aplicaciones. La app abre esta carpeta, así que no la muevas (si la mueves, vuelve a correr el script).

**Detalles de Mac:**

- **Teclas mantenidas:** macOS muestra el menú de acentos (é, è, ê…) al mantener una letra y deja de mandar la tecla. Eso cortaría las notas largas y el redoble, así que el lanzador lo apaga **solo para las apps de Python** (`defaults write org.python.python ApplePressAndHoldEnabled -bool false`). Para volver atrás: `defaults delete org.python.python ApplePressAndHoldEnabled`.
- **Audio:** sale por el dispositivo de salida que elegiste en Configuración del Sistema → Sonido (CoreAudio, baja latencia). No pide permiso de micrófono.

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

### Percusión por patrones
En **Tocar instrumento**, con un instrumento de batería, el modo **Percusión por patrones** convierte la batería
del tema en **10 patrones estándar de 2 compases** sacados del MIDI (`drum_patterns.py`): silencio, ritmos con
ride, fills de toms, redobles de caja, hi-hat rápido, ráfaga de platillos, ráfaga de doble bombo, etc.
Le das **Empezar** y eliges con **1…0** qué patrón suena; el cambio entra en el compás siguiente. La guía de
abajo muestra qué patrón usa el tema en cada momento; si eliges ese, suena el compás exacto del tema (y sube
"Acertados"). Las letras A…L dan golpes sueltos encima (mantener = redoble). La banda suena de fondo.

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
