# CRT Cast Server — Design Spec

## Panoramica

Server locale che riceve URL YouTube, scarica e converte il video in formato 4:3 PAL (768x576, 25fps), e lo trasmette a un Chromecast collegato a un televisore CRT. Interfaccia TUI nel terminale per mantenere un'estetica retro coerente col progetto.

---

## Stack

- **Backend:** Python 3.14, FastAPI (solo media server), asyncio
- **TUI:** Textual (framework TUI asincrono)
- **Chromecast:** pychromecast (libreria Python diretta)
- **Download:** yt-dlp (API Python con progress hooks)
- **Encoding:** ffmpeg (subprocess con `-progress pipe:1`)
- **Dipendenza esterna:** `ffmpeg` (via Homebrew)

---

## Struttura progetto

```
crt-player/
├── main.py              # entry point: avvia TUI + media server
├── config.py            # costanti di configurazione
├── queue_manager.py     # gestione coda (CRUD, ordinamento, stato)
├── pipeline.py          # worker: download → encode → cast
├── chromecast_mgr.py    # discovery, cast, controlli, status listener
├── media_server.py      # FastAPI minimale: solo /media/{filename}
├── ui.py                # app Textual (widget, layout, keybinding)
└── requirements.txt
```

---

## Configurazione (`config.py`)

```python
CHROMECAST_NAME = "Nome del tuo Chromecast"   # nome come appare in Google Home
MAX_VIDEO_HEIGHT = 576                          # qualità massima download
TEMP_DIR = "/tmp/crt_cast"                      # directory file temporanei
FILE_TTL_HOURS = 24                             # ore dopo cui cancellare i file (0 = mai)
SERVER_PORT = 8765                              # porta per il media server
```

---

## Pipeline

Per ogni video in coda, le fasi sono **strettamente sequenziali**:

```
yt-dlp (scarica fino a MAX_VIDEO_HEIGHT)
  → ffmpeg (converti in 4:3 PAL: 768x576, 25fps, pillarbox)
  → file .mp4 salvato in TEMP_DIR
  → pychromecast cast_url(http://server:port/media/filename.mp4)
```

### Download (yt-dlp)

Usa l'API Python di yt-dlp direttamente (no subprocess). Progress hook nativo per ottenere la percentuale di download in tempo reale.

Opzioni principali:
- `format`: migliore combinazione video+audio fino a `MAX_VIDEO_HEIGHT`
- `outtmpl`: salva in `TEMP_DIR` con nome basato sull'ID video

### Encoding (ffmpeg)

Subprocess con flag `-progress pipe:1` per progresso strutturato (coppie chiave=valore).

```bash
ffmpeg -i input.mp4 \
  -vf "scale=768:576:force_original_aspect_ratio=decrease,pad=768:576:(768-iw)/2:(576-ih)/2,setsar=1:1" \
  -r 25 \
  -progress pipe:1 \
  output.mp4
```

Questo gestisce correttamente sia video più larghi (16:9 → pillarbox) che più stretti di 4:3. `force_original_aspect_ratio=decrease` scala per contenere il video in 768x576 senza distorsione, `pad` aggiunge bande nere su entrambi gli assi se necessario, `setsar=1:1` garantisce pixel quadrati.

Il progresso si calcola da `out_time_us` diviso la durata totale del video (ottenuta da yt-dlp).

### Cast

Il media server FastAPI serve il file su `http://<local_ip>:SERVER_PORT/media/{filename}`. pychromecast avvia la riproduzione puntando a questo URL.

---

## Chromecast Manager (`chromecast_mgr.py`)

### Responsabilità

1. **Discovery:** trova il Chromecast per nome (`CHROMECAST_NAME`) all'avvio. Se non trovato, ritenta periodicamente.
2. **Cast:** avvia riproduzione di un URL via `MediaController.play_media()`.
3. **Controlli:** stop, pause, resume, volume (delta convertito a 0.0-1.0).
4. **Status listener:** `MediaStatusListener` registrato sul media controller, riceve callback ad ogni cambio stato.

### Stato esposto

- `connected: bool` — Chromecast trovato e raggiungibile
- `device_name: str` — nome del dispositivo
- `player_state: str` — PLAYING, PAUSED, IDLE, BUFFERING
- `current_time: float` — posizione corrente in secondi
- `duration: float` — durata totale in secondi
- `volume: float` — volume corrente 0.0-1.0

### Rilevamento fine video

Quando `player_state` diventa IDLE dopo uno stato PLAYING, il worker lo interpreta come "video finito" e passa al prossimo item in coda.

---

## Gestione coda (`queue_manager.py`)

### Struttura item

| Campo | Tipo | Valori / Note |
|-------|------|---------------|
| `id` | str | uuid4 |
| `url` | str | URL YouTube |
| `title` | str | Recuperato da yt-dlp prima del download |
| `status` | str | `queued` · `downloading` · `encoding` · `casting` · `playing` · `done` · `error` |
| `progress` | float | 0-100, per downloading e encoding |
| `error` | str \| None | Messaggio di errore se status è `error` |
| `filename` | str \| None | Nome del file MP4 in TEMP_DIR |

### Transizioni di stato

```
queued → downloading → encoding → casting → playing → done
                                                       ↗
qualsiasi stato intermedio ─────────────────────→ error
```

### Modalità di inserimento

- **queue** — aggiunge in fondo alla coda
- **next** — inserisce subito dopo l'item attualmente in riproduzione/processing
- **now** — interrompe il cast corrente, inserisce in testa, il worker riparte da quello

### Azioni su item

- **Rimuovi** — solo su item con status `queued`
- **Sposta su/giù** — solo tra item con status `queued`

---

## Media Server (`media_server.py`)

FastAPI minimale con un solo endpoint:

| Method | Path | Descrizione |
|--------|------|-------------|
| `GET` | `/media/{filename}` | Serve file MP4 da `TEMP_DIR` |

Gira in un thread separato via uvicorn, avviato da `main.py`. L'unico consumatore è il Chromecast.

---

## Pipeline Worker (`pipeline.py`)

Loop asincrono che processa la coda un item alla volta:

1. Prende il prossimo item con status `queued`
2. Recupera il titolo via yt-dlp (senza scaricare)
3. Download con progress hooks → aggiorna UI
4. Encoding con `-progress pipe:1` → aggiorna UI
5. Cast via chromecast_mgr → aggiorna UI
6. Attende fine riproduzione (listener IDLE) → item diventa `done`
7. Passa al prossimo

Download e encoding girano in thread separati via `asyncio.to_thread()` per non bloccare l'event loop di Textual.

### Gestione "now"

Quando arriva un item con mode `now`:
1. Cancella l'operazione corrente (a seconda dello stato):
   - `downloading`: annulla il download yt-dlp
   - `encoding`: termina il processo ffmpeg
   - `casting`/`playing`: stop al cast via chromecast_mgr
2. L'item corrente viene marcato `done`
3. Il nuovo item viene inserito in testa
4. Il worker riparte dal nuovo item

---

## Interfaccia TUI (`ui.py`)

### Layout

```
┌─ CRT Cast ──────────────────────── Chromecast: NomeTV ●─┐
│                                                          │
│  URL: [____________________________________] [mode: ▾]   │
│                                                          │
│  IN RIPRODUZIONE                                         │
│  "Titolo video corrente"                                 │
│  ████████░░░░ Encoding 64%                               │
│  ▶ 2:34 / 5:12  ██████████░░░░░░                         │
│  [■ Stop]  [⏸ Pause]  [🔊-]  [🔊+]                      │
│                                                          │
│  CODA                                                    │
│  1. Titolo video A                                       │
│  2. Titolo video B                                       │
│  3. Titolo video C                                       │
│                                                          │
│  [↑ Su] [↓ Giù] [✕ Rimuovi]                             │
└──────────────────────────────────────────────────────────┘
```

### Sezioni

- **Header:** nome app + stato connessione Chromecast (verde=connesso, rosso=disconnesso)
- **Input URL:** campo testo + selettore mode (queue/next/now), invio per aggiungere
- **In riproduzione:** titolo, progress bar pipeline (downloading/encoding %), progress bar playback (posizione/durata), controlli
- **Coda:** lista item con status `queued`, item selezionabile con frecce, azioni su item selezionato

### Hotkey

| Tasto | Azione |
|-------|--------|
| `Enter` | Aggiunge URL dalla barra input |
| `s` | Stop riproduzione |
| `p` | Pause/Resume |
| `+` / `-` | Volume su/giù |
| `d` | Rimuovi item selezionato |
| `k` / `j` | Sposta item selezionato su/giù |
| `↑` / `↓` | Naviga nella coda |
| `q` | Esci dall'app |

### Aggiornamento UI

La TUI comunica direttamente con `queue_manager`, `pipeline` e `chromecast_mgr` in-process. Gli aggiornamenti avvengono tramite il sistema reattivo di Textual (reactive attributes + watchers), senza WebSocket o HTTP.

---

## Avvio (`main.py`)

```bash
python main.py
```

1. Crea la directory `TEMP_DIR` se non esiste
2. Avvia il media server FastAPI/uvicorn in un thread separato
3. Avvia il discovery del Chromecast
4. Avvia il pipeline worker
5. Avvia la TUI Textual (che prende il controllo del terminale)

All'uscita (q o Ctrl+C): stop al cast, cleanup, shutdown.

---

## Cleanup file temporanei

Se `FILE_TTL_HOURS > 0`, all'avvio si cancellano i file in `TEMP_DIR` più vecchi del TTL. Non c'è cleanup periodico durante l'esecuzione — solo all'avvio.

---

## Dipendenze

### requirements.txt

```
textual
fastapi
uvicorn
yt-dlp
pychromecast
```

### Esterne

```bash
brew install ffmpeg
```
