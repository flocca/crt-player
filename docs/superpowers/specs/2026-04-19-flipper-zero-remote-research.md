# Telecomando Flipper Zero — ricerca & direzione

**Data:** 2026-04-19
**Scope:** studio di fattibilità per un telecomando fisico tramite Flipper Zero che permetta controlli base di playback (prev/next, pause/play, stop). Documento di ricerca, nessuna implementazione al momento.

## Aggiornamento 2026-04-21 — stato dei prerequisiti

Dopo questa ricerca è stato implementato il daemon headless (vedi [2026-04-21-headless-sync-daemon-design.md](./2026-04-21-headless-sync-daemon-design.md)). Il **componente #1** (control endpoint HTTP) è **già costruito** in [crt/api.py](../../../crt/api.py). I componenti #2 (bridge BLE→HTTP) e #3 (FAP Flipper) restano da implementare.

**Decisioni risolte rispetto a questa ricerca**:
- **Bind & auth**: il daemon ascolta su `0.0.0.0:8765` (LAN-accessible) **senza autenticazione** — scelta esplicita "trust the LAN" per uso domestico. La nota "127.0.0.1 only" più sotto è superata.
- **Topologia**: il daemon gira in Docker su homeserver Linux (`--network host` per mDNS). Il bridge BLE deve girare sullo stesso host (per `/var/run/dbus`), ma può puntare a qualsiasi `CRT_DAEMON_URL` raggiungibile in LAN.
- **Endpoint disponibili** (più di quanto la spec originale richiedeva):
  - `POST /control/next` — avanza cursor di 1 (wrap se loop_mode)
  - `POST /control/prev` — arretra di 1 (no-op a inizio lista)
  - `POST /control/toggle` — play/pause; se idle parte dal cursor o da items[0]
  - `POST /control/stop` — stop, cursor non avanza
  - `POST /control/play/{video_id}` — bonus: salta a un item specifico
  - `POST /control/loop/toggle` — bonus: inverte loop_mode runtime
  - `POST /control/sync` — bonus: forza sync immediato della playlist YT
  - `POST /control/calibrate` — bonus: cast pattern di calibrazione CRT
- **Risposte**: tutte 200 (o 202 per `/sync`) con body `{"ok": true, ...}`. Non-2xx = errore. Per il bridge basta guardare lo status code.
- **Componenti interni che gli endpoint invocano** (i nomi nella ricerca originale sono datati): non più `PipelineWorker / QueueManager`, ma `PlayerCore / LibraryStore` (più `PipelineWorker` per i download). I dettagli non interessano il bridge — è un layer che si limita a fare POST.

**Cosa resta dal piano originale, invariato**: la separazione bridge-on-host / FAP-on-Flipper, la scelta BLE GATT custom, i pro/contro analizzati di seguito.

## Obiettivo

Pilotare crt-player da Flipper Zero senza dover interagire con la TUI. Comandi target minimi:

- next / prev (avanza / arretra nella playlist)
- pause / play (toggle)
- stop
- eventuali extra in futuro: volume, calibrazione, loop on/off

Scenari d'uso previsti:

1. **Oggi** — app desktop con TUI aperta su Mac.
2. **Domani** — crt-player headless in un container Docker (tipicamente su un homeserver), senza TUI visibile.

## Opzioni valutate

### 1. BLE HID — app "Remote" integrata del Flipper

Il Flipper si accoppia a macOS come tastiera / telecomando BLE. Nessun codice sul Mac, nessun bridge. Si mappano i pulsanti del Flipper sulle scorciatoie definite in [tui_client/ui.py](../../../tui_client/ui.py) — al 2026-04-21 sono: `Ctrl+Space` (toggle), `Ctrl+S` (stop), `Ctrl+N` (next), `Ctrl+B` (prev), `Ctrl+T` (calibrate), `Ctrl+R` (loop toggle), `Ctrl+Y` (sync immediato). Vedi [CLAUDE.md](../../../CLAUDE.md) per i vincoli Textual sui bind con lettere singole.

**Nota post-implementazione**: dopo Phase 5 la TUI è un client HTTP, non più la primary UI. Il keystroke arriva alla finestra TUI sulla macchina paired col Flipper, e la TUI traduce in HTTP verso il daemon. Funziona ancora *se* la TUI è aperta e in foreground sulla macchina paired (di solito il Mac), ma è tortuoso quando potresti POST direttamente al daemon (Opzione 2).

**Pro**
- Zero codice da scrivere sul lato crt-player.
- Funziona subito come prototipo.

**Contro**
- Il terminale con Textual deve essere in foreground per ricevere i tasti — se l'utente cambia finestra, il telecomando smette di funzionare.
- I tasti multimediali (Play/Pause/Next) di macOS sono dirottati a Music/Spotify, non arrivano a Textual. Serve usare `Ctrl+`*qualcosa*.
- **Inutilizzabile in scenario headless**: senza TUI in foreground non c'è chi riceva i keystroke.

### 2. BLE GATT custom + bridge Python sull'host

Architettura a due pezzi:

```
Flipper (FAP custom + GATT server) ──BLE──> host daemon (bleak) ──HTTP──> crt-player /control/*
```

- **Sul Flipper:** una FAP (app nativa in C) che espone una caratteristica BLE con comandi tipo `next` / `prev` / `toggle` / `stop`.
- **Sull'host (o accanto al container):** un piccolo demone Python con [`bleak`](https://github.com/hbldh/bleak) che legge la caratteristica e inoltra a un endpoint HTTP di crt-player.
- **In crt-player:** ✅ **costruito al 2026-04-21**. Le route `POST /control/*` sono in [crt/api.py](../../../crt/api.py) e invocano `PlayerCore` (cursor + cast) / `LibraryStore` / `SyncEngine`. Stesso server uvicorn/FastAPI che serve gli MP4 al Chromecast.

**Pro**
- Non dipende dal focus del terminale: i comandi arrivano via asyncio al pipeline worker, stesso flusso di `_safe_call`.
- Si adatta senza modifiche sia al desktop sia al headless: cambia solo *dove* gira il daemon bridge (sempre sull'host, mai nel container).
- Il control endpoint è riutilizzabile: CLI, web UI, Home Assistant, shortcut iOS, secondi frontend futuri.
- Il Flipper diventa un client "generico", non accoppiato a Textual.

**Contro**
- Richiede una FAP custom (C, toolchain `ufbt`) + un daemon bridge + le route HTTP.
- Superficie di codice più grande; va pensato il layer di auth / binding sull'interfaccia. *(Risolto al 2026-04-21: scelta F1 — LAN trust, no auth. Vedi nota in cima al documento.)*

### 3. Infrarossi + ricevitore lato PC (es. FLIRC)

Il Flipper emette IR nativamente, ma il Mac non ha ricevitore IR. Serve hardware aggiuntivo: un [FLIRC](https://flirc.tv/) USB (che mappa IR → keystroke), oppure un Pi Pico / ESP8266 con KY-022 + driver Python.

**Pro**
- Nessun pairing, latenza bassa, molto "da telecomando TV".

**Contro**
- Hardware USB in più, stesso problema di focus della soluzione 1 (FLIRC emette keystroke).
- Nello scenario container servirebbe USB passthrough.
- Nessun vantaggio sul BLE nel nostro contesto.

## Decisione

**Andiamo sulla strada 2 (BLE GATT custom + bridge + control endpoint in crt-player).**

Ragionamento:

1. È l'unica delle tre che funziona **sia** desktop **sia** headless senza riscrittura.
2. Nel caso headless su Docker il taglio è pulito: il Bluetooth vive sull'host (dove i driver esistono davvero), il container resta minimale e portabile. Docker Desktop su macOS non espone BLE ai container, e anche su Linux richiederebbe `--net=host` + bind di `/var/run/dbus` — meglio non entrarci.
3. L'endpoint HTTP è un investimento "a fondo perduto" utile anche fuori dal telecomando.
4. La 1 resta comunque disponibile come **fallback "plug&play"** in scenario desktop senza che si debba costruire nulla.

## Schema finale (per il futuro)

```
┌──────────────┐     BLE GATT     ┌──────────────────┐    HTTP LAN        ┌────────────────┐
│   Flipper    │ ───────────────> │  host daemon     │ ─────────────────> │   crt-daemon   │
│  (FAP custom)│   next/prev/...  │  (bleak, Python) │  POST /control/*   │  (FastAPI)     │
└──────────────┘                  └──────────────────┘                    └────────────────┘
                                                                                 │
                                                                                 v
                                                                  PlayerCore / LibraryStore
                                                                  (in crt/, già scritti)
```

Componenti da costruire, in ordine di dipendenza:

1. **~~Control endpoint in crt-player.~~** ✅ Costruito al 2026-04-21. Vedi [crt/api.py](../../../crt/api.py). Bind `0.0.0.0:8765`, no auth. Endpoint reali: `next`, `prev`, `toggle`, `stop`, `play/{video_id}`, `loop/toggle`, `sync`, `calibrate`. Tutti POST, risposta JSON `{"ok": true}`.

2. **Daemon bridge BLE → HTTP.** ⏳ Da costruire. Eseguibile Python standalone con `bleak`. Sta sempre sull'host (mai nel container Docker del crt-daemon). Configurabile via env:
   - `FLIPPER_MAC` (pairing address)
   - `CRT_DAEMON_URL` (default `http://localhost:8765` — punta al daemon; in deploy reali tipicamente `http://homeserver.local:8765` o l'IP)
   - Nessun token di auth da gestire (F1).
   
   Mapping comando → endpoint suggerito:
   | byte BLE | endpoint POST |
   |---|---|
   | `0x01` | `/control/next` |
   | `0x02` | `/control/prev` |
   | `0x03` | `/control/toggle` |
   | `0x04` | `/control/stop` |
   | `0x05` | `/control/loop/toggle` |
   | `0x06` | `/control/sync` |
   | `0x07` | `/control/calibrate` |
   
   Il bridge ignora il body della risposta — basta verificare lo status code (200 → OK, altro → log e procedi). Timeout consigliato: 5s. In caso di errore di rete, retry singolo dopo 1s, poi giù.

3. **FAP Flipper.** ⏳ Da costruire. App nativa che espone un GATT server con una characteristic "command" scrivibile (1 byte secondo la tabella sopra). Ogni pressione pulsante → write BLE → bridge → HTTP → daemon.

## Open questions (da risolvere quando si implementa)

- ~~Autenticazione del control endpoint~~ — **risolto**: F1 (LAN trust, no auth). Vedi [2026-04-21-headless-sync-daemon-design.md](./2026-04-21-headless-sync-daemon-design.md).
- Pairing e riconnessione BLE: gestione del caso "Flipper fuori portata / batteria scarica" nel daemon.
- Packaging del daemon bridge: servizio `launchd` su macOS, systemd unit su Linux. Su homeserver Linux dove gira già `docker compose`, sarebbe naturale un altro servizio compose accanto, ma il bridge **non può girare nel container** del daemon perché il container non ha BLE. Soluzione: un secondo `docker-compose.yml` con `--privileged` + bind di `/var/run/dbus` *solo per il bridge*, oppure un systemd unit bare metal sull'host. Quest'ultima è più semplice.
- Feedback visivo sul Flipper dopo un comando: il bridge potrebbe leggere `/status` dopo ogni comando e scrivere lo stato del player (idle/playing/paused) su una seconda characteristic BLE che la FAP rilegge per aggiornare il display. Costo modesto, valore concreto.
- Come e dove persistere il MAC del Flipper: env var `FLIPPER_MAC` è il default più semplice. Per setup multi-Flipper o pairing guidato al primo avvio, un piccolo file di config in `~/.config/crt-flipper-bridge/`.
- **Discovery del daemon**: il bridge deve sapere a quale host:porta puntare. Tre opzioni: env var (`CRT_DAEMON_URL`, semplice), mDNS (`_crt._tcp.local`, più "automagico" ma serve registrare il servizio lato daemon — non è fatto oggi), o configurazione hardcoded nel bridge. Suggerito: env var per ora.
