# Headless sync + player daemon — design

**Data:** 2026-04-21
**Scope:** trasformare il prototipo crt-player (TUI monolitica con URL aggiunti a mano) in un sistema headless che sincronizza automaticamente una playlist YouTube dedicata e la casta a un Chromecast, pilotato via HTTP API. La TUI esistente diventa un client remoto opzionale. In futuro si aggancerà anche il bridge Flipper Zero descritto in [2026-04-19-flipper-zero-remote-research.md](./2026-04-19-flipper-zero-remote-research.md), ma il sistema è completamente usabile senza di esso tramite TUI e `curl`.

## Contesto e stato attuale

Il prototipo oggi ([CLAUDE.md](../../../CLAUDE.md)) è una TUI Textual che gira in foreground, gestisce una coda di URL inseriti manualmente, scarica con yt-dlp, transcodifica a 4:3 PAL con ffmpeg, e casta via pychromecast. Tutto in un singolo processo asyncio. Stato persistito in `~/.local/share/crt-player/state.json`. Un FastAPI/uvicorn server gira in thread daemon solo per servire gli MP4 al Chromecast.

Funziona bene come prototipo, ma richiede:
- aggiungere URL manualmente uno alla volta dalla TUI,
- tenere la TUI aperta perché il playback continui,
- girare sul Mac (TUI + ffmpeg + chromecast discovery).

## Obiettivi

1. **Sync automatico** con una playlist YouTube. I video aggiunti/rimossi dalla playlist (da qualsiasi device: telefono, browser, ecc.) appaiono/spariscono dalla library senza intervento manuale.
2. **Headless**: il sistema gira in Docker su un homeserver, senza UI visibile.
3. **Pilotabile via HTTP** da: TUI remota (riusata), bridge Flipper Zero (futuro), `curl`, potenziali altri frontend.
4. **Stabile senza Flipper**: il TUI remoto e l'HTTP API sono sufficienti per usare il sistema a regime. La Flipper è un *add-on* successivo.
5. **Minima manutenzione**: setup OAuth iniziale una volta, poi il daemon si auto-gestisce (refresh token, polling, cleanup cache).
6. **Zero regressione di qualità video**: tutta la pipeline ffmpeg (crop detect, 4:3→16:9 squeeze trick, margini CRT, calibrazione) resta identica.

## Decisioni architetturali chiave

Ricapitolate dalla fase di brainstorming:

- **[A2] OAuth + playlist dedicata**: autenticazione OAuth scope `youtube.readonly`, l'utente aggiunge video a una playlist tua (es. `CRT Queue`) invece che alla Watch Later. Niente cookie, niente rischio captcha.
- **[B1+] Daemon monolito ma modulare**: un singolo processo Python (un container Docker) con moduli interni ben separati. Il control endpoint HTTP è esplicitamente il "confine" di un'eventuale futura separazione in due processi.
- **[C1] YouTube è il master**: la playlist definisce cosa c'è e in che ordine. Sync unidirezionale YT→library. Nessun riordino lato library.
- **[D1] Nessun write-back su YouTube**: il daemon legge soltanto. Gli item `done` restano in library finché l'utente non li rimuove manualmente da YT. Alla rimozione, il daemon cancella i file.
- **[E4] REST endpoints + TUI come client remoto**: l'HTTP API è il primo cittadino. La TUI esistente viene convertita a client HTTP e girerà dove serve (non nel container).
- **[F1] LAN trust, no auth**: binding su `0.0.0.0`, nessun token. Accettato come compromesso tra ergonomia e threat model di una LAN casalinga.

## Architettura generale

```
                                                              YouTube Data API v3
                                                                      ▲
                                                                      │ poll periodico
                                                                      │ (youtube.readonly)
┌─────────────────────── crt-daemon (Docker, sull'homeserver) ────────┼────────────┐
│                                                                      │            │
│   ┌──────────────┐     ┌─────────────────┐     ┌────────────────┐  │            │
│   │ SyncEngine   │────>│  LibraryStore   │<────│ PipelineWorker │  │            │
│   │ (polling YT, │     │ (items, status, │     │ (yt-dlp +      │  │            │
│   │  diff add/rm)│     │  cache index,   │     │  ffmpeg)       │  │            │
│   │              │     │  cursor)        │     │                │  │            │
│   └──────────────┘     └────────┬────────┘     └────────────────┘  │            │
│           ▲                     │                                    │            │
│           │                     │ letture/comandi                    │            │
│           │                     ▼                                    │            │
│   ┌───────┴──────────────────────────────────┐    ┌──────────────┐ │            │
│   │  FastAPI (uvicorn)                        │    │ PlayerCore   │ │            │
│   │  /library/*   /control/*   /status        │───>│ (chromecast) │ │            │
│   │  /media/<id>.mp4                          │<───│              │ │            │
│   └──────┬──────────┬────────┬────────────────┘    └──────┬───────┘ │            │
│          │          │        │                             │         │            │
└──────────┼──────────┼────────┼─────────────────────────────┼─────────┘            │
           │ LAN      │ LAN    │ LAN                         │ LAN (mDNS)           │
           ▼          ▼        ▼                             ▼                      │
       Chromecast   TUI    Flipper                       Chromecast                 │
       (MP4 pull)  remota   bridge                       (commands)                 │
                            (BLE → HTTP, futuro)                                    │
```

### Moduli interni del daemon

- **`SyncEngine`**: polling YouTube, diff della playlist, applica add/remove al `LibraryStore`.
- **`LibraryStore`**: evoluzione di `QueueManager`. Fonte di verità per gli item in library e per il cursor.
- **`PipelineWorker`**: download yt-dlp + encode ffmpeg. Invariato nella sostanza rispetto al prototipo.
- **`PlayerCore`**: loop di casting, gestione stato playback, consuma il `LibraryStore`.
- **`ChromecastManager`**: invariato rispetto al prototipo.
- **`YouTubeClient`**: wrapper sottile su `googleapiclient`, gestisce OAuth e paginazione.
- **`api.py`**: router FastAPI che espone library, control, status, media. Un unico uvicorn.

### Cosa scompare rispetto al prototipo

- La TUI non è più l'entry point: diventa un client HTTP remoto (`crt-tui`).
- L'aggiunta manuale di URL via TUI non esiste più nel flusso principale (si aggiunge a YT, il daemon pesca).
- `media_server.py` standalone viene integrato in `api.py`.

### Cosa viene riutilizzato

- `pipeline.py` (yt-dlp, `_detect_crop`, `_build_video_filter`, cache encoded).
- `chromecast_mgr.py` e tutta la gestione pychromecast.
- `calibration.py` (esposto via `/control/calibrate`).
- La pipeline di qualità video (crop, margini, 4:3→16:9 squeeze trick) senza modifiche.

## Sync engine e integrazione YouTube

### OAuth: scope e bootstrap

Scope: `https://www.googleapis.com/auth/youtube.readonly`.

Setup una volta sola:
1. L'utente crea un progetto in Google Cloud Console, abilita la YouTube Data API v3, scarica `client_secrets.json` (tipo "Desktop app").
2. Lancia `docker compose run --rm crt-daemon crt-bootstrap`. Il daemon:
   - Costruisce l'URL di consenso con `redirect_uri=http://localhost/` (porta non importa, nessun server in ascolto).
   - Logga l'URL e attende input su stdin.
3. L'utente apre l'URL dal browser del proprio laptop, consente, il browser redirige su `http://localhost/?code=...` e va in errore di connessione (nessun listener). L'utente copia l'URL completo dalla barra indirizzi.
4. L'utente incolla l'URL nel prompt del daemon. Il daemon estrae il parametro `code`, lo scambia con Google per access + refresh token, e scrive `oauth_token.json`.
5. Il sottocomando termina. Da qui in poi `docker compose up -d` è sufficiente.

Refresh automatico: `googleapiclient` rinnova l'access token dal refresh token a ogni chiamata. Il refresh token non scade finché l'utente non revoca l'accesso.

### Polling e diff

- Cadenza: `CRT_SYNC_INTERVAL_S` (default `300` = 5 min).
- Trigger manuale: `POST /control/sync` forza un sync immediato.
- Modello: full-snapshot + diff a ogni ciclo. Per playlist personali (<500 item) il costo è trascurabile.

Algoritmo (pseudo):

```
ogni CRT_SYNC_INTERVAL_S:
    snapshot = youtube_client.list_playlist_items(playlist_id)
      # list of (video_id, title, position), in ordine playlist

    current_ids  = { item.video_id for item in library.items }
    snapshot_ids = { entry.video_id for entry in snapshot }

    added    = [e for e in snapshot if e.video_id not in current_ids]
    removed  = current_ids - snapshot_ids
    kept     = [e for e in snapshot if e.video_id in current_ids]

    for entry in added:
        library.add(entry.video_id, entry.title)   # status=queued

    for video_id in removed:
        library.remove(video_id)
          # Stops playback if that item is playing/casting.
          # Cancels the download if in flight.
          # Deletes downloaded_path and encoded file.
          # Advances cursor if removed item was the cursor.

    library.reorder(kept_in_snapshot_order)

    library.save_state()
```

### Rimozione di un item in playback

Se il sync rileva che l'item attualmente `playing` o `casting` è stato tolto da YouTube:

1. Il `SyncEngine` chiama `PlayerCore.stop_and_remove(video_id)`.
2. `PlayerCore` invia un `quit_app()` al Chromecast, attende la transizione a idle.
3. Il `PipelineWorker`, se stava lavorando su quell'item, viene interrotto tramite un flag di cancellazione (task asyncio `.cancel()` del download; il processo ffmpeg viene terminato con `SIGTERM`).
4. I file (`downloaded_path`, encoded MP4) vengono cancellati.
5. Il `cursor_video_id` viene aggiornato al prossimo item in ordine playlist (senza auto-play — l'utente deve premere next/toggle).

Questo è una scelta consapevole: preferiamo un taglio netto a una "grace period" complicata.

### Fallimenti e backoff

- **Auth failure** (refresh token rejected, grant revocato): `status.youtube = "degraded"` con messaggio. Backoff esponenziale 1m → 5m → 30m. Nessuna interruzione del playback su ciò che è già in library.
- **Transient network / 5xx**: backoff 30s → 1m → 5m → 30m.
- **Quota exceeded** (molto improbabile con 5 min polling): attesa 1h.

Il `/status` endpoint riflette sempre lo stato corrente così la TUI remota mostra un indicatore visibile.

## Data model

### `QueueItem` (evoluzione)

```python
@dataclass
class QueueItem:
    video_id: str                    # NEW: primary key, l'11-char ID di YouTube
    id: str                          # uuid interno, stabile per correlare con i file cache
    title: str = ""
    status: str = "queued"           # queued|downloading|encoding|ready|casting|playing|done|error
    progress: float = 0.0
    error: str | None = None
    filename: str | None = None      # nome del file encoded in TEMP_DIR
    downloaded_path: str | None = None
    playback_position: float = 0.0
```

Differenze rispetto a [queue_manager.py](../../../queue_manager.py):
- **Aggiunto** `video_id`.
- **Rimosso** `url` (derivabile: `f"https://www.youtube.com/watch?v={video_id}"`).

### `LibraryStore`

Evoluzione di `QueueManager`. Oltre a `items: list[QueueItem]` e `history: list[QueueItem]` (invariati), aggiunge:

```python
cursor_video_id: str | None   # quale item sta suonando o è stato ultimo a suonare
loop_mode: bool               # runtime override di CRT_LOOP
```

### Invarianti

- L'ordine di `items` è **sempre** l'ordine della playlist YouTube.
- Solo il `SyncEngine` modifica la membership (add/remove) e l'ordine.
- Solo il `PipelineWorker` modifica `status` / `progress` / `filename` / `downloaded_path` / `error` per transizioni di pipeline (`queued` → `downloading` → `encoding` → `ready`|`error`).
- Solo il `PlayerCore` modifica `status` per transizioni di playback (`ready` → `casting` → `playing` → `done`) e `cursor_video_id`.
- Ogni modifica invoca `save_state()` con atomic replace (come il prototipo).

Tutto gira in singolo event loop asyncio, non servono lock espliciti — la disciplina di "chi può toccare cosa" è documentata via commenti e test.

### Cursor: esplicito, non derivato

Il prototipo calcola il cursor dinamicamente dai `status`. Con D1 (item `done` che si accumulano) questa deduzione diventa ambigua: il nuovo modello usa un campo esplicito `cursor_video_id`, persistito.

Transizioni:
- Library vuota all'avvio: `cursor_video_id = None`.
- Primo comando `/control/next` o `/control/toggle` con cursor=None: cursor diventa `items[0].video_id`.
- Fine naturale di un item (`idle_reason == "FINISHED"`): cursor avanza di 1 in ordine playlist, autoplay.
- `/control/next`: cursor +1 (ri-suona anche item `done`; wrap se loop mode).
- `/control/prev`: cursor -1 (ri-suona anche item `done`; no-op a inizio lista).
- `/control/play/{video_id}`: cursor salta a quello specifico item.
- Rimozione da YT dell'item cursor-corrente: cursor avanza al successivo, nessun auto-play.
- Playlist diventa vuota: `cursor_video_id = None`.

### Autoplay, stop, loop

- **Autoplay**: `idle_reason == "FINISHED"` → cursor +1 → cast immediato del successivo. Come oggi.
- **Stop manuale** (Flipper stop, TUI stop, `/control/stop`): termina il cast, il cursor **non** avanza, `playback_position` dell'item preservato. Una successiva `/control/toggle` riprende.
- **Loop mode**: `CRT_LOOP` env letta solo come default iniziale. Il flag vive in `LibraryStore.loop_mode`, togglabile via `POST /control/loop/toggle`. Se on: wrap a fine lista. Se off: stop.

### `state.json` v2

```json
{
  "version": 2,
  "cursor_video_id": "dQw4w9WgXcQ",
  "loop_mode": false,
  "items": [
    {
      "video_id": "dQw4w9WgXcQ",
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "title": "Rick Astley - Never Gonna Give You Up",
      "status": "ready",
      "progress": 100.0,
      "filename": "dQw4w9WgXcQ_pal_crop.mp4",
      "downloaded_path": "/tmp/crt_cast/dQw4w9WgXcQ.mp4",
      "playback_position": 0.0,
      "error": null
    }
  ],
  "history": []
}
```

**Migrazione da v1**: al primo avvio del daemon v2, se `state.json` ha `version != 2` (o manca), il file viene rinominato `state.json.v1.bak` e la library riparte vuota. Il primo sync la ripopola. Non scriviamo logica di migrazione (prototipo in uso solo dall'autore, effort non giustificato).

## HTTP API

Un unico FastAPI/uvicorn, bind `0.0.0.0:CRT_SERVER_PORT` (default `8765`), nessuna autenticazione (F1, LAN trust).

### Endpoint di lettura

```
GET /library/items
  → {
      "cursor_video_id": "dQw4w9WgXcQ" | null,
      "loop_mode": false,
      "items": [
        {
          "video_id": "...",
          "id": "<uuid>",
          "title": "...",
          "status": "ready",
          "progress": 100.0,
          "error": null,
          "is_cursor": true
        }
      ]
    }

GET /status
  → {
      "youtube": {
        "state": "ok" | "degraded",
        "last_sync_at": "2026-04-21T12:34:56Z",
        "last_error": null | "token refresh failed: ...",
        "playlist_id": "PLxxxx",
        "playlist_size": 42
      },
      "pipeline": {
        "state": "idle" | "downloading" | "encoding",
        "current_video_id": "..." | null,
        "queue_depth": 3
      },
      "player": {
        "state": "idle" | "casting" | "playing" | "paused",
        "current_video_id": "..." | null,
        "current_time_s": 145.2 | null,
        "duration_s": 423.0 | null,
        "chromecast": "connected" | "disconnected"
      }
    }
```

### Endpoint di controllo

```
POST /control/next               → 200 {"ok": true, "cursor_video_id": "..."}
POST /control/prev               → 200 {"ok": true, "cursor_video_id": "..."}
POST /control/toggle             → 200 {"ok": true, "state": "playing" | "paused"}
POST /control/stop               → 200 {"ok": true}
POST /control/play/{video_id}    → 200 {"ok": true, "cursor_video_id": "..."}
                                   404 {"error": "video_id not in library"}
POST /control/loop/toggle        → 200 {"ok": true, "loop_mode": true}
POST /control/sync               → 202 {"ok": true}
POST /control/calibrate          → 200 {"ok": true}
```

Semantica precisa:
- `/control/toggle`: se playing → pause. Se paused → resume. Se idle e cursor è settato → cast di quell'item da posizione salvata. Se idle e cursor=None → cast di `items[0]`.
- Tutti gli endpoint sono idempotenti nel comportamento osservabile.
- `/control/sync` ritorna `202` perché il sync vero gira in background; si può pollare `/status.youtube.last_sync_at` per sapere quando finisce.

### Endpoint media

```
GET /media/{filename}   → FileResponse MP4 da TEMP_DIR
```

Invariato rispetto a [media_server.py](../../../media_server.py), solo spostato nel router unico. Path sanitization esistente.

### OpenAPI / Swagger

`/docs` resta abilitato (autogen FastAPI). Comodo per testing manuale ed esplorazione del sistema.

## Deploy e configurazione

### Struttura del repo (dopo refactor)

```
crt-player/
├── crt/                        # package principale
│   ├── __init__.py
│   ├── config.py
│   ├── sync_engine.py          # NEW
│   ├── youtube_client.py       # NEW
│   ├── library_store.py        # evoluzione di queue_manager.py
│   ├── pipeline.py
│   ├── chromecast_mgr.py
│   ├── player_core.py          # NEW
│   ├── calibration.py
│   ├── api.py                  # NEW (include le route media)
│   ├── daemon.py               # NEW, entry point `crt-daemon`
│   └── bootstrap.py            # NEW, entry point `crt-bootstrap`
├── tui_client/                 # NEW, TUI come client HTTP
│   ├── __init__.py
│   ├── ui.py
│   └── main.py                 # entry point `crt-tui`
├── tests/
│   ├── test_sync_engine.py        # NEW
│   ├── test_youtube_client.py     # NEW
│   ├── test_library_store.py      # evoluzione di test_queue_manager.py
│   ├── test_player_core.py        # NEW
│   ├── test_api.py                # NEW
│   ├── test_pipeline.py
│   ├── test_chromecast_mgr.py
│   └── test_integration.py        # adattato
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── pyproject.toml              # [project.scripts]: crt-daemon, crt-bootstrap, crt-tui
├── requirements.txt
└── run.sh                      # aggiornato al nuovo entry point (`python -m crt.daemon`)
```

### Entry points

- `crt-daemon` → `crt.daemon:main` — server principale, per Docker.
- `crt-bootstrap` → `crt.bootstrap:main` — OAuth flow interattivo, sottocomando una tantum.
- `crt-tui` → `tui_client.main:main` — TUI remota, gira fuori dal container.

### Variabili d'ambiente

Defaults sensati per tutte tranne `CRT_YT_PLAYLIST_ID`. Quelle **NEW** sono nuove rispetto al prototipo; le altre sono riprese tali quali.

| Env var | Default | Descrizione |
|---|---|---|
| `CRT_CHROMECAST_NAME` | `Living Room TV` | Nome del Chromecast da usare. |
| `CRT_MAX_VIDEO_HEIGHT` | `576` | Altezza massima yt-dlp. |
| `CRT_TEMP_DIR` | `/tmp/crt_cast` | Dir cache. In Docker: `/data/cache`. |
| `CRT_FILE_TTL_HOURS` | `24` | TTL cache file. Ora applicato solo a file orfani. |
| `CRT_SCALE_MODE` | `crop` | `crop` o `pad`. |
| `CRT_AUTO_CROP` | `1` | Auto crop detect pre-encode. |
| `CRT_SERVER_PORT` | `8765` | Porta FastAPI. |
| `CRT_STATE_FILE` | `~/.local/share/crt-player/state.json` | In Docker: `/data/state/state.json`. |
| `CRT_LOOP` | `0` | Default iniziale di `loop_mode` runtime. |
| `CRT_MARGIN_TOP/BOTTOM/LEFT/RIGHT` | `0` | Margini CRT. |
| **`CRT_YT_PLAYLIST_ID`** | *(required)* | ID playlist YouTube (`PLxxxx...`). |
| **`CRT_YT_CLIENT_SECRETS`** | `~/.local/share/crt-player/client_secrets.json` | In Docker: `/data/secrets/client_secrets.json`. |
| **`CRT_YT_TOKEN_FILE`** | `~/.local/share/crt-player/oauth_token.json` | In Docker: `/data/secrets/oauth_token.json`. |
| **`CRT_SYNC_INTERVAL_S`** | `300` | Polling YouTube, secondi. |
| **`CRT_LOG_LEVEL`** | `INFO` | Log level globale. |
| **`CRT_DAEMON_URL`** | `http://localhost:8765` | Solo per `crt-tui`: URL del daemon remoto. |

### Docker

`Dockerfile` base:
- `python:3.12-slim`
- `apt install ffmpeg` (requisito di sistema, non pip)
- `pip install` di `requirements.txt`
- Copia del package `crt/`
- `ENTRYPOINT ["crt-daemon"]`

`docker-compose.yml` (Linux con mDNS):

```yaml
services:
  crt-daemon:
    build: .
    restart: unless-stopped
    network_mode: host
    environment:
      CRT_YT_PLAYLIST_ID: ${CRT_YT_PLAYLIST_ID}
      CRT_CHROMECAST_NAME: ${CRT_CHROMECAST_NAME}
      CRT_TEMP_DIR: /data/cache
      CRT_STATE_FILE: /data/state/state.json
      CRT_YT_CLIENT_SECRETS: /data/secrets/client_secrets.json
      CRT_YT_TOKEN_FILE: /data/secrets/oauth_token.json
    volumes:
      - ./data/cache:/data/cache
      - ./data/state:/data/state
      - ./data/secrets:/data/secrets:ro
```

Tre volumi separati:
- `cache` — file MP4 encoded (grossi, ricostruibili).
- `state` — `state.json` (piccolo, non ricostruibile senza perdita di progress).
- `secrets` — `client_secrets.json` + `oauth_token.json` (riservati, read-only mount).

### Sviluppo bare metal

Per sviluppo sul Mac, `./run.sh` continua a funzionare come oggi (sourcing di `.env`, daemon bare metal), con una modifica triviale: invece di `python main.py` invoca `python -m crt.daemon`. Docker è per il deploy sul homeserver Linux. Docker Desktop su macOS non supporta `--network host` in modo compatibile con mDNS — non tentiamo quel percorso.

### Lifecycle del daemon

```
crt-daemon main():
  1. load config, init LibraryStore from state.json (migrate v1 if present)
  2. init YouTubeClient (load token, fail-fast se missing)
  3. init ChromecastManager (async discovery, non-blocking)
  4. start FastAPI server (uvicorn) in task asyncio
  5. start PipelineWorker loop in task asyncio
  6. start SyncEngine loop in task asyncio (first sync entro 10s)
  7. start PlayerCore loop in task asyncio
  8. install signal handlers (SIGINT/SIGTERM)
  9. run forever; on signal, graceful shutdown:
     - stop SyncEngine (no more polls)
     - let PipelineWorker finish current chunk (no interrupt of ffmpeg mid-encode)
     - detach chromecast callbacks (gotcha documentato in CLAUDE.md)
     - cast.quit_app() se connesso
     - save_state()
     - close server
```

## Testing

### Per modulo

| Modulo | Strategia |
|---|---|
| `YouTubeClient` | Mock `googleapiclient.discovery.build()`. Testa parsing risposta, paginazione (>50), refresh failed → eccezione tipizzata, network error → eccezione tipizzata. |
| `SyncEngine` | `YouTubeClient` mockato, `LibraryStore` reale in-memory. Testa diff add/remove/reorder, item in `ACTIVE_STATUSES` non rimossi, rimozione di item cursor-corrente → avanza cursor + stop playback, idempotenza. |
| `LibraryStore` | Evoluzione di `test_queue_manager.py`. Invarianti: cursor sempre valido o None, ordine mirror dell'input, save/load v2, migrazione v1 → backup + empty. |
| `PipelineWorker` | Riusa `test_pipeline.py`. Fixture `_restore_config` autouse (gotcha in CLAUDE.md). |
| `ChromecastManager` | Invariato. |
| `PlayerCore` | Mock `ChromecastManager`. Testa transizioni cursor per next/prev/play, autoplay, loop mode, stop, toggle da idle. |
| `api.py` | FastAPI `TestClient`. Daemon inizializzato in fixture con mocks integrati. Testa shape risposta, status codes, side effects di ogni endpoint. |
| `tui_client` | Pilot API (come `test_ui.py`) adattato. Mock `httpx`/`requests` invece di oggetti in-process. |

### Integration tests

`tests/test_integration.py` (opt-in `pytest -m integration`) evoluti:
- Nuova env var `TEST_YT_PLAYLIST_ID`: playlist YouTube di test dell'autore, con 2-3 video brevi.
- Flusso end-to-end: daemon → sync → download → encode → cast → fine naturale → autoplay del successivo.
- Scenario "rimozione durante playback": il test manipola manualmente la playlist (fuori banda), verifica stop + cleanup.
- Gotcha esistenti preservati (`asyncio.Event` ricreato per test, teardown di `cast.stop()`, settle pause dopo transizioni).

### Performance attese

Unit suite completa: <30s (oggi 50 test in <10s, ne aggiungeremo ~40). Integration: 5-10 min per ciclo completo.

## Rollout

Branch separato dal `main` del prototipo, sviluppo incrementale in fasi autocontenute. A fine di ogni fase i test passano e il sistema nella modalità della fase corrente funziona — ci si può fermare in qualunque momento senza half-baked state.

1. **Ossatura**: package `crt/`, `state.json` v2, `LibraryStore` con `video_id`, migrazione v1→backup, test.
2. **YouTube**: `YouTubeClient` con OAuth bootstrap, `SyncEngine` con diff engine. Test offline con playlist mockata.
3. **PlayerCore**: estrae dalla TUI la logica di cast loop. Daemon minimo che accetta `/control/*` e fa girare playback via oggetti in-process.
4. **FastAPI unificato**: `api.py` con tutti gli endpoint, media server integrato, `/status`.
5. **TUI client**: porting a HTTP. Durante la fase, la vecchia TUI in-process resta via `CRT_MODE=legacy` per debug. A fase completa, la modalità legacy viene rimossa.
6. **Docker**: Dockerfile, compose, test sul homeserver.
7. **Integration test refresh**.

## Rapporto con la spec Flipper

Questo design è complementare a [2026-04-19-flipper-zero-remote-research.md](./2026-04-19-flipper-zero-remote-research.md), con queste precisazioni:

- **Questo spec definisce** il control endpoint HTTP (`/control/next`, `/prev`, `/toggle`, `/stop`, più `/play/{id}`, `/loop/toggle`, `/sync`, `/calibrate`) che la spec Flipper richiedeva come prerequisito. Il bridge Flipper potrà consumarlo così com'è.
- **Questo spec supersede** la spec Flipper su due punti tecnici:
  - **Binding**: LAN (`0.0.0.0`), non `127.0.0.1`. Necessario perché la TUI remota e il bridge Flipper possono girare su host diversi dal daemon.
  - **Auth**: nessuna (F1). La spec Flipper lasciava aperta la scelta, qui decidiamo.
- **Restano di competenza della spec Flipper** (da affrontare quando quel progetto parte): pairing e persistenza del MAC del Flipper, riconnessione BLE, packaging del bridge (launchd/systemd), feedback visivo sul Flipper.
- **La spec Flipper andrà aggiornata** all'inizio della sua implementazione: oggi parla di "scenario desktop oggi / headless domani" come due casi, ma dopo questo progetto esiste solo lo scenario headless.

**Indipendenza operativa**: il sistema costruito da questo spec è pienamente utilizzabile senza il bridge Flipper. I frontend di controllo disponibili da subito sono: TUI remota (`crt-tui`), `curl` / script / Home Assistant HTTP, Swagger UI su `/docs`.

## Questioni aperte (per la fase di implementazione)

Cose che non decidiamo qui perché sono dettagli che emergeranno scrivendo il codice:

- Format esatto del logging (formatter, prefissi di modulo).
- Retry policy precisa dei download yt-dlp (per ora ereditiamo i default).
- Cleanup di file orfani in `TEMP_DIR` (encoded MP4 che non corrispondono a nessun item): probabilmente task housekeeping nel SyncEngine, frequenza da decidere.
- TTL degli encoded (`CRT_FILE_TTL_HOURS`): nel nuovo modello gli item `done` restano con il loro file. Il TTL ha senso solo per file orfani (rimossi da YT) o per item in `error`. Da rifinire.
- Strategia esatta di prefetch del `PipelineWorker`: oggi prefecha il primo `queued` dopo il cursor. Lo stesso algoritmo funziona ma va allineato all'uso del `cursor_video_id` esplicito invece del cursor derivato.
