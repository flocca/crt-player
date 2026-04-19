# Telecomando Flipper Zero — ricerca & direzione

**Data:** 2026-04-19
**Scope:** studio di fattibilità per un telecomando fisico tramite Flipper Zero che permetta controlli base di playback (prev/next, pause/play, stop). Documento di ricerca, nessuna implementazione al momento.

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

Il Flipper si accoppia a macOS come tastiera / telecomando BLE. Nessun codice sul Mac, nessun bridge. Si mappano i pulsanti del Flipper sulle scorciatoie già definite in [ui.py](../../../ui.py) (pattern `Ctrl+T`, `Ctrl+R`, ecc. — vedi [CLAUDE.md](../../../CLAUDE.md) per i vincoli Textual sui bind con lettere singole).

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
- **In crt-player:** un control server HTTP che invoca i metodi esistenti di `PipelineWorker` / `QueueManager` / `ChromecastManager`. L'infrastruttura c'è già: uvicorn/FastAPI è già istanziato per servire gli MP4 al Chromecast — basta aggiungere poche route `POST /control/next`, `/control/toggle`, ecc.

**Pro**
- Non dipende dal focus del terminale: i comandi arrivano via asyncio al pipeline worker, stesso flusso di `_safe_call`.
- Si adatta senza modifiche sia al desktop sia al headless: cambia solo *dove* gira il daemon bridge (sempre sull'host, mai nel container).
- Il control endpoint è riutilizzabile: CLI, web UI, Home Assistant, shortcut iOS, secondi frontend futuri.
- Il Flipper diventa un client "generico", non accoppiato a Textual.

**Contro**
- Richiede una FAP custom (C, toolchain `ufbt`) + un daemon bridge + le route HTTP.
- Superficie di codice più grande; va pensato il layer di auth / binding sull'interfaccia (almeno `127.0.0.1` only, o token condiviso).

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
┌──────────────┐     BLE GATT     ┌──────────────────┐    HTTP loopback   ┌────────────────┐
│   Flipper    │ ───────────────> │  host daemon     │ ─────────────────> │   crt-player   │
│  (FAP custom)│   next/prev/...  │  (bleak, Python) │  POST /control/*   │  control server│
└──────────────┘                  └──────────────────┘                    └────────────────┘
                                                                                 │
                                                                                 v
                                                                     PipelineWorker / QueueManager
                                                                       (metodi già esistenti)
```

Componenti da costruire, in ordine di dipendenza:

1. **Control endpoint in crt-player.** Route HTTP sul server FastAPI già attivo. Bind su `127.0.0.1` (o IP interno del container), endpoint `/control/next`, `/control/prev`, `/control/toggle`, `/control/stop`. Chiama i metodi asyncio già esistenti sulla pipeline — stesso pattern di `_safe_call`.
2. **Daemon bridge BLE → HTTP.** Eseguibile Python standalone con `bleak`. Sta sempre sull'host (mai nel container). Configurabile via env: indirizzo MAC del Flipper, URL del control server.
3. **FAP Flipper.** App nativa che espone un GATT server con una characteristic "command" scrivibile. Ogni pressione pulsante → write BLE → bridge → HTTP → pipeline.

## Open questions (da risolvere quando si implementa)

- Autenticazione del control endpoint: basta `127.0.0.1`-only, o serve un token condiviso anche lì (per evitare che qualsiasi processo locale piloti il player)?
- Pairing e riconnessione BLE: gestione del caso "Flipper fuori portata / batteria scarica" nel daemon.
- Packaging del daemon: servizio `launchd` su macOS, systemd unit su Linux?
- Feedback visivo sul Flipper dopo un comando (richiede una seconda characteristic read o un notify dal bridge).
- Come e dove persistere il MAC del Flipper (env var, file di config, pairing guidato al primo avvio).
