# Telecomando Flipper Zero — design

**Data:** 2026-05-10
**Scope:** definire architettura, protocollo BLE GATT, deploy e split implementativo del telecomando Flipper Zero per crt-player. Costruisce sui due documenti precedenti: la fattibilità è già stata stabilita in [2026-04-19-flipper-zero-remote-research.md](./2026-04-19-flipper-zero-remote-research.md) (decisione: BLE GATT custom + bridge HTTP); il control endpoint del daemon è stato implementato come parte di [2026-04-21-headless-sync-daemon-design.md](./2026-04-21-headless-sync-daemon-design.md) e vive in [crt/api.py](../../../crt/api.py).

Questo doc copre i due componenti rimanenti: il **bridge BLE→HTTP** (Python su Lodge, dockerizzato) e la **FAP Flipper** (app nativa C su Flipper Zero). Topologia e contratto sono fissati qui; ogni componente ha poi il proprio plan di implementazione.

## Contesto operativo

- **Daemon crt-player** già deployato su Lodge come servizio Docker (`lodge-tools/services/crt-player/`), `network_mode: host`, ascolta su `0.0.0.0:8765`. Endpoint `POST /control/{next,prev,toggle,stop,loop/toggle,sync,calibrate}` disponibili.
- **Lodge** = Raspberry Pi 5 8GB, RPi OS Lite arm64, BLE built-in (BCM43455). Stack Bluetooth standard via BlueZ.
- **Flipper Zero** = hardware utente, comunica via BLE 5.0. Toolchain `ufbt` per FAP nativa C.
- **Mac** = workstation di sviluppo. Non partecipa al runtime (solo build/flash della FAP).

## Decisioni architetturali

1. **Bridge gira su Lodge nello stesso host del daemon.** Co-locazione semplifica networking (`localhost:8765` via `network_mode: host`), elimina cross-host hop, riduce superficie di errore. Il bridge non gira sul Mac perché il setup target è headless.

2. **Bridge è dockerizzato.** Segue il pattern `lodge-tools/services/<name>/` (precedenti: `ecovacs`, `homeassistant`). Container con `network_mode: host` + bind `/var/run/dbus` per accesso BlueZ.

3. **Repo split:**
   - **`flipper_app/`** in repo `crt-player` — FAP nativa C, build con `ufbt`, lifecycle indipendente.
   - **`services/crt-flipper-bridge/`** in repo `lodge-tools` — bridge Python + Dockerfile + install.sh, deployato come servizio lodge.
   - **Spec (questo doc)** in `crt-player/docs/superpowers/specs/` — fonte di verità del protocollo, referenziato da entrambi.

4. **Protocollo: BLE Nordic UART Service (NUS) con il Flipper come peripheral.** La FAP attiva il profilo `Serial` built-in del firmware Flipper (NUS standard). Il bridge è il central, sottoscrive a TX notify per ricevere comandi, scrive su RX per inviare status/feedback. La pivot da "GATT custom" è imposta dal firmware stock — vedi sezione "Pivot: niente GATT custom" sotto.

5. **No autenticazione né su HTTP né su BLE.** Estensione coerente della scelta F1 ("trust the LAN") già presa per il daemon. La superficie BLE è limitata a chi è in raggio di Lodge.

## Topologia runtime

```
┌────────────────────── Lodge (Pi 5) ──────────────────────┐
│                                                            │
│  ┌──────────────────────┐    HTTP localhost  ┌──────────┐ │
│  │ crt-flipper-bridge   │───── POST ───────> │crt-player│ │
│  │ (Docker,             │      :8765         │ (Docker, │ │
│  │  network_mode=host,  │                    │  host)   │ │
│  │  /var/run/dbus mount)│                    └──────────┘ │
│  └─────────┬────────────┘                                 │
│            │ D-Bus → BlueZ                                │
│            │                                              │
│       Pi 5 BLE radio                                      │
└────────────┼──────────────────────────────────────────────┘
             │
             │ BLE GATT (Flipper = peripheral, bridge = central)
             │
        ┌────▼─────────┐
        │   Flipper    │
        │  (FAP custom)│
        └──────────────┘
```

## Protocollo BLE — revisione 2026-05-10 (Nordic UART Service)

### Pivot: niente GATT custom

L'idea iniziale del doc (UUID custom `ddb10001-…` con tre characteristic `command`/`status`/`last_result`) è **non implementabile su firmware Flipper stock**. Le API `furi_hal_bt` esposte alle FAP permettono solo di **switchare tra profili pre-provisionati** (HID, Serial, BLE Beacon, ecc.) — non c'è registrazione di GATT service custom da app esterne. Verificato in [furi_hal_bt.h reference](https://developer.flipper.net/flipperzero/doxygen/furi__hal__bt_8h.html) e in [serial_profile.c](https://github.com/flipperdevices/flipperzero-firmware/blob/dev/targets/f7/ble_glue/profiles/serial_profile.c).

La pivot è **Nordic UART Service (NUS)**: la FAP attiva il profilo Serial built-in, che espone TX/RX come pipe binari bidirezionali. La semantica resta identica; cambia solo il framing perché c'è un canale unico per direzione invece di tre characteristic separate.

### Service & characteristics (NUS, well-known UUIDs)

| UUID | Nome | Properties | Direzione | Payload |
|---|---|---|---|---|
| `6e400001-b5a3-f393-e0a9-e50e24dcca9e` | service | — | — | — |
| `6e400002-b5a3-f393-e0a9-e50e24dcca9e` | RX | Write | bridge → Flipper | feedback (vedi framing sotto) |
| `6e400003-b5a3-f393-e0a9-e50e24dcca9e` | TX | Notify | Flipper → bridge | 1+ byte command sequence (vedi framing sotto) |

(Naming RX/TX è dal punto di vista del peripheral, cioè il Flipper.)

### Framing

**Flipper → bridge (TX notify):** sequenza di byte, ogni byte è un command code (0x01-0x07 — tabella invariata, vedi sotto). Più press in rapida successione possono arrivare nello stesso pacchetto: il bridge itera e processa ciascuno indipendentemente. Byte non riconosciuti → log warn, scartati.

**Bridge → Flipper (RX write):** messaggio prefissato da un byte di tipo:

| Byte 0 | Significato | Byte 1+ |
|---|---|---|
| `0x01` | last_result | 1 byte: `0x00` ok, `0x01` HTTP err, `0x02` net err. Totale 2 byte. |
| `0x02` | status update | ASCII: `idle`, `playing`, `paused`, `casting`, `error`. Totale 2..N byte. |

Type byte != ASCII printable → no ambiguità con il payload status. La FAP fa uno switch su `data[0]` per dispatchare.

### Tabella comandi

| Byte | Endpoint POST | Pulsante Flipper (default UI) |
|---|---|---|
| `0x01` | `/control/next` | Up |
| `0x02` | `/control/prev` | Down |
| `0x03` | `/control/toggle` | OK |
| `0x04` | `/control/stop` | Back (long press) |
| `0x05` | `/control/loop/toggle` | Right |
| `0x06` | `/control/sync` | Left |
| `0x07` | `/control/calibrate` | OK (long press) |

Byte non riconosciuti → bridge logga warning e ignora.

### Sequenza tipica

```
Flipper UI: utente preme "Up"
  └─ FAP: ble_profile_serial_tx(&[0x01], 1)            ← TX notify
       └─ bridge: POST http://localhost:8765/control/next
            └─ daemon: cursor++; return 200 {"ok":true}
                 └─ bridge: write_gatt_char(RX, [0x01, 0x00])     ← last_result OK
                      └─ FAP: switch byte 0 → 0x01 → result OK; aggiorna display
            └─ (parallelo, ogni 2s) bridge: GET /status
                 └─ bridge: write_gatt_char(RX, [0x02] + b"playing")  ← status update
                      └─ FAP: switch byte 0 → 0x02 → status; aggiorna riga di stato
```

### Versioning del protocollo

Nessun campo versione in v1. Service UUID è quello fisso di NUS, non lo possiamo cambiare; per evolvere il protocollo si introducono nuovi type byte in RX (es. `0x03` per "current_video_id" se utile in futuro). Type byte sconosciuti → la FAP ignora e logga.

## Componente 1 — Bridge (`lodge-tools/services/crt-flipper-bridge/`)

### Struttura file

```
services/crt-flipper-bridge/
├── service.conf               # SERVICE_PORT=0, SERVICE_CONTAINER=lodge-crt-flipper-bridge
├── .env.template              # FLIPPER_MAC, CRT_DAEMON_URL, LOG_LEVEL
├── Dockerfile                 # python:3.12-slim-bookworm + bleak + httpx
├── requirements.txt
├── bridge.py                  # entry point, ~150 righe
├── docker-compose.yml         # network_mode: host, /var/run/dbus mount
├── install.sh                 # deploy_env, SCP source, build, smoke test
└── CLAUDE.md                  # operational gotchas (BLE, pairing, ufw)
```

### Configurazione (.env.template)

```
# MAC address del Flipper Zero. Trovalo dal menu Bluetooth del Flipper.
FLIPPER_MAC=

# URL del daemon crt-player. Su Lodge col daemon nello stesso host: http://localhost:8765.
CRT_DAEMON_URL=http://localhost:8765

# Livello di log.
LOG_LEVEL=INFO
```

### docker-compose.yml

```yaml
services:
  crt-flipper-bridge:
    build: { context: . }
    image: lodge-crt-flipper-bridge:local
    container_name: lodge-crt-flipper-bridge
    restart: unless-stopped
    network_mode: host
    env_file: .env
    volumes:
      - /run/dbus:/run/dbus
    cap_add:
      - NET_ADMIN
```

Note sul mount D-Bus:
- Path canonico su RPi OS è `/run/dbus` (`/var/run` è symlink a `/run` su systemd).
- Niente `:ro` — alcuni stack BlueZ richiedono il bind read-write per socket auxiliari/lock.

**Fallback escalation se D-Bus + NET_ADMIN non bastano:**
1. Aggiungere `cap_add: [NET_ADMIN, NET_RAW]`.
2. Se ancora no: `privileged: true` (precedente: `homeassistant` su Lodge).

Decisione durante l'esecuzione del Plan A.

### bridge.py — comportamento

Loop di alto livello:

1. **Connessione.** `bleak.BleakClient(FLIPPER_MAC)`. Su `BleakError` o disconnect, retry con backoff esponenziale `1s → 2s → 4s → 8s → 16s → 30s` (cap a 30s).
2. **Subscribe.** Una volta connesso, `start_notify(NUS_TX_UUID, on_command)`.
3. **On notify.** `on_command(sender, data)`:
   - Itera su tutti i byte in `data` (più press in un singolo pacchetto sono possibili).
   - Per ogni byte: se non in `COMMAND_TABLE` → log warn + skip; altrimenti dispatch.
   - Dispatch: `result = await post(endpoint)` con timeout 5s, 1 retry dopo 1s.
   - `await write_gatt_char(NUS_RX_UUID, bytes([0x01, result_code]))` (frame `last_result`).
4. **Status poll task.** `asyncio.create_task(poll_status())`:
   - Ogni 2s: `GET /status`.
   - Mappa `status.player.state` → ASCII: `idle`/`playing`/`paused`/`casting`. Se daemon irraggiungibile → `error`.
   - `write_gatt_char(NUS_RX_UUID, bytes([0x02]) + ascii_state)` solo se cambiato.

Funzioni pure facilmente testabili:
- `parse_command(byte: int) -> str | None` — byte → endpoint path.
- `state_to_ascii(player_state: str) -> bytes` — `playing` → `b"playing"`, ecc.

### Dockerfile

```dockerfile
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    libdbus-1-3 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY bridge.py ./
CMD ["python", "-u", "bridge.py"]
```

### requirements.txt

```
bleak==0.22.*
httpx==0.27.*
```

### install.sh

Pattern equivalente a `services/ecovacs/install.sh`:
1. `deploy_env crt-flipper-bridge`.
2. Validazione: `FLIPPER_MAC` non vuoto in `.env`.
3. Assicura BlueZ installato su Lodge: `lodge_ssh "sudo apt-get install -y bluez bluez-tools"` (idempotente).
4. SCP `Dockerfile`, `requirements.txt`, `bridge.py` in `${LODGE_DATA_DIR}/crt-flipper-bridge/`.
5. `deploy_and_compose crt-flipper-bridge "up -d --build"`.
6. Smoke check: `docker logs lodge-crt-flipper-bridge --since 30s | grep -q "connected to ${FLIPPER_MAC}"`. Warn se no.

Niente UFW (BLE non TCP, niente porte da aprire).

### Test strategy

**Unit (Mac, no hardware):**
- `parse_command` — mapping completo + byte sconosciuti.
- `state_to_ascii` — tutti gli stati.
- HTTP retry con `httpx.MockTransport`: 1 errore → retry → success; 2 errori → return network_err.

**Integration locale (Mac, opt-in):**
- Daemon FastAPI in-process come fixture, con stub di `player`/`sync_engine`/`library`.
- Bridge punta a quel daemon, comandi simulati come dict scritti direttamente nel callback (no BLE).
- Verifica che POST arrivino correttamente.

**Integration su Lodge (post-deploy):**
- `lodge crt-flipper-bridge logs` deve mostrare "connected to FLIPPER_MAC" entro 30s.
- Premi un pulsante sul Flipper, verifica POST nel log + `lodge crt-player logs` mostra l'effetto sul daemon.

Non si testa BLE end-to-end automaticamente — richiede hardware fisico.

## Componente 2 — FAP Flipper (`flipper_app/`)

### Struttura file

```
flipper_app/
├── application.fam            # manifest Flipper, dichiara nome/icona/categoria
├── crt_remote_app.c           # entry point, GUI + view dispatcher
├── ble_serial.c               # wrapper su BleProfileSerial (start, send, RX callback)
├── ble_serial.h
├── icons/                     # PNG/icone per la UI Flipper
└── README.md                  # build/flash con ufbt, troubleshooting
```

Niente GATT server custom: il file `ble_serial.c` chiama `furi_hal_bt_start_app(ble_profile_serial, ...)` per attivare il profilo Serial built-in, registra una callback per RX e usa `ble_profile_serial_tx()` per inviare 1 byte per pulsante.

### UI sul Flipper (v1)

Schermata singola con:
- **Header:** "CRT Remote".
- **Mapping pulsanti:** sequenza di righe con icona+label per i 4 comandi principali (next/prev/toggle/stop). Le 3 funzioni extra (loop/sync/calibrate) sono accessibili da un sotto-menu o long-press.
- **Riga di stato (in basso):** legge l'ultimo `status` ricevuto via NUS RX (frame type `0x02`), mostra `IDLE`/`PLAY`/`PAUSE`/`CAST`/`ERR`.
- **Indicatore connessione:** icona in alto a destra che mostra connesso/disconnesso al central.

Layout dettagliato disegnato durante Plan B (richiede iterazione su display 128×64).

### v1 minimal vs v1.0 finale

**v1 minimal (per smoke test iniziale):** solo invio comandi. Niente status, niente last_result. Permette di validare il path BLE→HTTP→daemon prima di ottimizzare la UI.

**v1.0 finale:** include status display + last_result feedback (icona check/warning per pochi secondi dopo ogni comando).

Decisione di scope durante Plan B; il protocollo lato bridge supporta entrambi senza riscritture.

### Build & flash

```bash
cd crt-player/flipper_app
ufbt                  # compile FAP
ufbt launch           # flash + start su Flipper connesso via USB
```

### Test strategy

Niente unit test — il toolchain Flipper non li supporta facilmente.

Smoke test manuale on-device:
1. Build + flash con `ufbt launch`.
2. Sul Flipper: avvia app, vai in modalità "advertise" (esposizione BLE).
3. Sul Mac in dev: `bleak` script che si connette e simula il bridge — verifica che notify arrivino e write su status si vedano sul display.
4. Su Lodge in deploy: bridge gira, premi pulsanti, verifica nel `crt-player logs`.

## Pi-side prerequisites

Da aggiungere a `lodge-tools` (in fase di Plan A):
- `bluez` + `bluez-tools` installati. Aggiunti come step nell'`install.sh` del bridge (idempotente). Non si tocca `setup/00-base.sh` per evitare di estendere il setup base con dipendenze service-specifiche.
- BlueZ deve essere `enabled` e `started`: `systemctl enable --now bluetooth`.
- Niente pairing manuale: BlueZ in modalità "auto-accept" via `bluetoothctl agent on; default-agent`. Il primo `BleakClient.connect(MAC)` triggers il pairing.

Sotto il tetto di "tradeoff coerenti col F1 trust-the-LAN": niente PIN BLE, niente passkey. Se in futuro serve, si aggiunge un secondo doc.

## Open questions risolte

| Domanda | Decisione |
|---|---|
| Pairing/riconnessione BLE | Backoff esponenziale 1→30s. MAC fisso da env, no discovery dinamica. Auto-accept via BlueZ. |
| Packaging del bridge | Docker su Lodge tramite pattern `lodge-tools/services/`. No systemd nativo. |
| BLE in container | `network_mode: host` + bind `/var/run/dbus` + `cap_add: NET_ADMIN`. Fallback `privileged: true` se D-Bus non basta. |
| Feedback Flipper | Da v1: status poll + last_result write. Display Flipper in v1 minimal opzionale, in v1.0 finale obbligatorio. |
| Persistenza MAC | env var `FLIPPER_MAC` in `.env` lodge-tools. Niente file di config separato. |
| Discovery daemon | env var `CRT_DAEMON_URL`, default `http://localhost:8765`. |
| Auth HTTP | Nessuna (F1 trust-the-LAN). |
| Auth BLE | Nessuna (auto-accept). |

## Plan split

**Plan A — `crt-flipper-bridge` su Lodge** (lodge-tools repo). ✅ Completato 2026-05-10.
- `services/crt-flipper-bridge/` completo: source + Docker + install.
- Deploy su Lodge confermato (container `lodge-crt-flipper-bridge` Up, in loop di reconnect verso `FLIPPER_MAC`).
- `lodge crt-flipper-bridge install/update/logs/status/restart` funzionanti.

**Plan A.1 — Refactor bridge per pivot NUS** (lodge-tools repo). ⏳ Necessario dopo la pivot del 2026-05-10.
- Sostituire UUIDs custom con NUS UUIDs.
- Iterare su tutti i byte del notify (più press per pacchetto).
- Aggiungere type byte prefisso ai write da bridge → Flipper.
- Aggiornare test.
- Re-deploy su Lodge.

**Plan B — `flipper_app` FAP** (crt-player repo). ⏳ Da costruire.
- App C con `ufbt` toolchain.
- Attivazione `ble_profile_serial`, callbacks RX, button → TX.
- v1 minimal prima (solo command TX, no UI di stato).
- v1.0 finale con UI stato + feedback last_result.
- Smoke test on-device col bridge reale su Lodge.

Il protocollo (NUS + framing + tabella comandi) è il contratto tra Plan A.1 e Plan B.

## Non-goals (v1)

Esplicitamente fuori scope, da considerare in v2 se utili:
- **mDNS discovery del daemon.** Per ora env var basta.
- **Multiple Flipper paired contemporaneamente.** Un solo MAC, un solo Flipper.
- **Configurazione del mapping pulsanti via UI Flipper.** Hardcoded nella FAP.
- **Volume control via Flipper.** Daemon non espone endpoint volume; richiederebbe estensione `crt/api.py` prima.
- **Notifiche di playback end / item change verso il Flipper.** Possibile in v2 con un quarto characteristic notify-from-bridge.
- **Battery indicator del Flipper sul display di Lodge.** No reverse channel, no.
