# Flipper FAP `crt_remote` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Costruire una FAP nativa C per Flipper Zero che attiva il profilo BLE Serial (Nordic UART), invia comandi 1-byte al bridge su Lodge alla pressione di pulsanti, e mostra lo stato del player su display + feedback per ogni comando.

**Architecture:** App C in `flipper_app/` (questo repo), build con `ufbt`. Usa `furi_hal_bt_change_app(ble_profile_serial, ...)` per switchare al NUS profile. Una callback registrata via `ble_profile_serial_set_event_callback` riceve i byte dal bridge (RX); button press invocano `ble_profile_serial_tx()` con 1 byte. ViewPort/Canvas standard per la GUI.

**Tech Stack:** C99, Flipper SDK (firmware ufficiale `dev` branch), `ufbt` toolchain, FuriHAL BT, Serial Profile.

**Spec di riferimento:** [`docs/superpowers/specs/2026-05-10-flipper-remote-design.md`](../specs/2026-05-10-flipper-remote-design.md) (sezione "Protocollo BLE — revisione 2026-05-10").

**Repo di lavoro:** `crt-player` (questo repo). Path relativi dalla root.

**Branch:** continua su `flipper-remote` (già esistente).

## Caveats di onestà

- **Verifica empirica obbligatoria:** la Flipper SDK ha API BLE che evolvono. I prototipi e i pattern usati qui derivano da `furi_hal_bt.h` e `serial_profile.h` del branch `dev` al 2026-05-10, ma piccole differenze tra firmware ufficiale / Momentum / Xtreme / RogueMaster sono comuni. Ogni task ha uno step di build+flash+verify per accorgersi presto di drift.
- **Niente unit test:** la SDK Flipper non supporta test isolati. Ogni step si verifica on-device leggendo i log via `ufbt cli log` (CLI seriale del Flipper) o confrontando con `lodge crt-flipper-bridge logs`.
- **Toolchain:** `ufbt` è il tool ufficiale (Python). Si installa con `pipx install ufbt` o `pip install --user ufbt`. Al primo `ufbt update` scarica firmware + SDK.

## Pre-flight

```bash
cd /Users/flocca/src/crt-player
git status                 # should be clean on branch flipper-remote
pipx install ufbt 2>/dev/null || pip3 install --user ufbt
ufbt update                # downloads firmware/SDK on first run
ufbt --help | head -5      # smoke check
```

Se `ufbt update` fallisce, vedi [https://github.com/flipperdevices/flipperzero-ufbt](https://github.com/flipperdevices/flipperzero-ufbt). Il branch SDK target è `dev` (default).

---

### Task 1: Scaffolding FAP + hello-world

Crea la directory `flipper_app/` con un'app minima che mostra un testo, build con `ufbt`, flash e run sul Flipper.

**Files:**
- Create: `flipper_app/application.fam`
- Create: `flipper_app/crt_remote_app.c`
- Create: `flipper_app/icons/` (directory vuota per ora)
- Create: `flipper_app/.gitignore` (`dist/`, `.ufbt/`)
- Create: `flipper_app/README.md`

- [ ] **Step 1: Crea `flipper_app/application.fam`**

```python
App(
    appid="crt_remote",
    name="CRT Remote",
    apptype=FlipperAppType.EXTERNAL,
    entry_point="crt_remote_app",
    requires=["gui"],
    stack_size=2 * 1024,
    fap_category="Tools",
    fap_description="Remote control for crt-player via BLE Serial bridge.",
    fap_version="0.1",
)
```

- [ ] **Step 2: Crea `flipper_app/crt_remote_app.c` (hello world)**

```c
#include <furi.h>
#include <gui/gui.h>
#include <input/input.h>

typedef struct {
    FuriMessageQueue* input_queue;
    ViewPort* view_port;
    Gui* gui;
} CrtRemoteApp;

static void draw_callback(Canvas* canvas, void* ctx) {
    UNUSED(ctx);
    canvas_clear(canvas);
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 2, 12, "CRT Remote");
    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str(canvas, 2, 28, "Hello from FAP");
    canvas_draw_str(canvas, 2, 60, "Back to exit");
}

static void input_callback(InputEvent* event, void* ctx) {
    CrtRemoteApp* app = ctx;
    furi_message_queue_put(app->input_queue, event, FuriWaitForever);
}

int32_t crt_remote_app(void* p) {
    UNUSED(p);
    CrtRemoteApp app = {0};
    app.input_queue = furi_message_queue_alloc(8, sizeof(InputEvent));

    app.view_port = view_port_alloc();
    view_port_draw_callback_set(app.view_port, draw_callback, &app);
    view_port_input_callback_set(app.view_port, input_callback, &app);

    app.gui = furi_record_open(RECORD_GUI);
    gui_add_view_port(app.gui, app.view_port, GuiLayerFullscreen);

    InputEvent event;
    bool running = true;
    while(running) {
        if(furi_message_queue_get(app.input_queue, &event, FuriWaitForever) == FuriStatusOk) {
            if(event.type == InputTypeShort && event.key == InputKeyBack) {
                running = false;
            }
        }
    }

    gui_remove_view_port(app.gui, app.view_port);
    view_port_free(app.view_port);
    furi_record_close(RECORD_GUI);
    furi_message_queue_free(app.input_queue);
    return 0;
}
```

- [ ] **Step 3: Crea `flipper_app/.gitignore`**

```
dist/
.ufbt/
*.swp
```

- [ ] **Step 4: Crea `flipper_app/README.md`**

```markdown
# crt_remote — Flipper FAP

Telecomando per crt-player via BLE Nordic UART.

## Build & flash

```bash
cd flipper_app
ufbt              # build
ufbt launch       # flash + start su Flipper connesso via USB
ufbt cli log      # leggere i log seriali del Flipper
```

Output dell'app in `dist/crt_remote.fap`.

## Uso

1. Sul Flipper: apri Apps → Tools → CRT Remote.
2. La app attiva il profilo BLE Serial (NUS).
3. Sul homeserver: il `crt-flipper-bridge` (vedi repo `lodge-tools`) si connette automaticamente al MAC del Flipper.
4. Pulsanti: Up=next, Down=prev, OK=play/pause, Back-long=stop, Right=loop, Left=sync, OK-long=calibrate.
5. La riga di stato mostra lo stato del player ricevuto dal daemon via il bridge.

## Spec

`crt-player/docs/superpowers/specs/2026-05-10-flipper-remote-design.md`.
```

- [ ] **Step 5: Build**

```bash
cd flipper_app
ufbt
```

Expected: build OK, output `dist/crt_remote.fap` (binario ELF).

- [ ] **Step 6: Flash + run on-device (richiede Flipper via USB)**

```bash
ufbt launch
```

Sul Flipper dovresti vedere l'app aprirsi automaticamente con la schermata "Hello from FAP". Premi Back per uscire.

Se `ufbt launch` fallisce (Flipper non collegato, etc.), `ufbt fap_deploy` copia il `.fap` nella SD del Flipper sotto `apps/Tools/crt_remote.fap`. Lo lanci manualmente da Apps → Tools.

- [ ] **Step 7: Commit**

```bash
git add flipper_app/
git commit -m "flipper-remote: FAP scaffolding (hello world + ufbt build OK)"
```

---

### Task 2: Attivazione profilo Serial (NUS)

Aggiungi switch al profilo `ble_profile_serial`. Verifica che il Flipper appaia in scan BLE come peripheral con NUS service esposto.

**Files:**
- Modify: `flipper_app/crt_remote_app.c`

- [ ] **Step 1: Includi headers BLE**

Aggiungi in cima a `crt_remote_app.c`:
```c
#include <furi_hal_bt.h>
#include <ble/ble.h>
#include <profiles/serial_profile.h>
```

(Path possibili variano: se `<profiles/serial_profile.h>` non si risolve, prova `<services/serial_profile.h>` o `<extra_profiles/serial_profile.h>`. `ufbt` errore di build segnalerà subito il path giusto.)

- [ ] **Step 2: Estendi la struct app**

Sostituisci `typedef struct { ... } CrtRemoteApp;` con:
```c
typedef struct {
    FuriMessageQueue* input_queue;
    ViewPort* view_port;
    Gui* gui;

    FuriHalBleProfileBase* profile;
    const FuriHalBleProfileTemplate* prev_profile;  // to restore on exit
} CrtRemoteApp;
```

- [ ] **Step 3: Helper per attivare/ripristinare il profilo**

Aggiungi sopra `crt_remote_app()`:
```c
#define CRT_REMOTE_LOG_TAG "crt_remote"

static bool ble_serial_start(CrtRemoteApp* app) {
    // Save current profile so we can switch back on exit.
    // Note: there is no public API to query the current template; we just remember
    // we need to switch back to whatever the system default was. The cleanest path
    // is to call furi_hal_bt_change_app to BleProfileSerial directly — when our
    // app exits, the system Bluetooth Settings app may need a manual cycle to
    // restore the default profile (HID etc.). Documented in the README as known
    // limitation; can be revisited later by inspecting the profile registry.
    app->prev_profile = NULL;

    FuriHalBleProfileParams params = {0};
    app->profile = furi_hal_bt_change_app(ble_profile_serial, params, NULL, NULL, app);
    if(app->profile == NULL) {
        FURI_LOG_E(CRT_REMOTE_LOG_TAG, "failed to switch to BLE Serial profile");
        return false;
    }
    FURI_LOG_I(CRT_REMOTE_LOG_TAG, "BLE Serial profile active");
    return true;
}

static void ble_serial_stop(CrtRemoteApp* app) {
    if(app->profile == NULL) return;
    // Switching back to a "default" profile is firmware-dependent. For a clean
    // exit, change_app to BleProfileSerial with NULL params is a known no-op-ish
    // pattern; otherwise the user can re-enable Bluetooth from Settings.
    // TODO: verify what happens when the FAP exits without explicitly switching
    // back. If the system gracefully restarts the default profile via the GAP
    // disconnect callback, this can be removed.
    UNUSED(app);
}
```

- [ ] **Step 4: Wire start/stop in `crt_remote_app()`**

Subito dopo `gui_add_view_port(...);`:
```c
    if(!ble_serial_start(&app)) {
        FURI_LOG_E(CRT_REMOTE_LOG_TAG, "BLE init failed; exiting");
        // Continue with the GUI loop so user sees the error rather than crashing.
    }
```

E subito prima di `gui_remove_view_port(...);`:
```c
    ble_serial_stop(&app);
```

- [ ] **Step 5: Mostra stato BLE sul display**

Modifica `draw_callback` per mostrare se il profilo è attivo:
```c
static void draw_callback(Canvas* canvas, void* ctx) {
    CrtRemoteApp* app = ctx;
    canvas_clear(canvas);
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 2, 12, "CRT Remote");
    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str(canvas, 2, 28,
        app->profile ? "BLE: Serial active" : "BLE: init failed");
    canvas_draw_str(canvas, 2, 60, "Back to exit");
}
```

- [ ] **Step 6: Build e flash**

```bash
cd flipper_app
ufbt launch
```

Expected: app si apre, mostra "BLE: Serial active". Se mostra "init failed":
- Controlla `ufbt cli log` per il messaggio di errore esatto.
- Verifica che il Bluetooth sia abilitato sul Flipper (Settings → Bluetooth → ON).
- Verifica include path del header (`profiles/` vs `services/` vs altro).

- [ ] **Step 7: Smoke check via bridge su Lodge**

Mentre l'app gira sul Flipper, verifica che il bridge si connetta:

```bash
/Users/flocca/src/lodge-tools/lodge crt-flipper-bridge logs
```

Aspettata la riga `connected to <FLIPPER_MAC>` entro ~30s. Se compare, BLE funziona.

Se non compare:
- Da Lodge: `sudo bluetoothctl scan on` per verificare che il MAC sia visibile.
- Dal Flipper: vai in `Settings → Bluetooth → My MAC` e confronta con `FLIPPER_MAC` in `lodge-tools/services/crt-flipper-bridge/.env`.

- [ ] **Step 8: Commit**

```bash
git add flipper_app/crt_remote_app.c
git commit -m "flipper-remote: attiva BLE Serial profile (NUS) all'avvio"
```

---

### Task 3: TX comando 0x01 (Up button) — first end-to-end

Aggiungi un mapping minimo: pulsante Up invia il byte 0x01 al bridge. Smoke test: pressing Up sul Flipper triggera `POST /control/next` lato daemon.

**Files:**
- Modify: `flipper_app/crt_remote_app.c`

- [ ] **Step 1: Helper per inviare 1 byte via Serial TX**

Aggiungi sopra `crt_remote_app()`:
```c
static void ble_serial_send_byte(CrtRemoteApp* app, uint8_t byte_val) {
    if(app->profile == NULL) {
        FURI_LOG_W(CRT_REMOTE_LOG_TAG, "TX 0x%02x but profile is NULL", byte_val);
        return;
    }
    bool ok = ble_profile_serial_tx(app->profile, &byte_val, 1);
    FURI_LOG_I(CRT_REMOTE_LOG_TAG, "TX 0x%02x → %s", byte_val, ok ? "ok" : "fail");
}
```

- [ ] **Step 2: Map Up button → 0x01**

Nel main loop, dentro `if(furi_message_queue_get(...) == FuriStatusOk)`:
```c
            if(event.type == InputTypeShort) {
                switch(event.key) {
                    case InputKeyUp:
                        ble_serial_send_byte(&app, 0x01);
                        break;
                    case InputKeyBack:
                        running = false;
                        break;
                    default:
                        break;
                }
            }
```

(Sostituisci la singola condizione `event.key == InputKeyBack` precedente con questo switch.)

- [ ] **Step 3: Build + flash**

```bash
ufbt launch
```

- [ ] **Step 4: Smoke test end-to-end**

In due terminali:
```bash
# Terminale 1: log del bridge
/Users/flocca/src/lodge-tools/lodge crt-flipper-bridge logs

# Terminale 2: log del daemon
/Users/flocca/src/lodge-tools/lodge crt-player logs
```

Sul Flipper: con app aperta e bridge connesso, premi Up. Aspettato:
- Bridge log: `command 0x01 → POST /control/next` poi `[POST] http://localhost:8765/control/next`.
- Daemon log: una linea che indica advance del cursor.

Se il comando non arriva:
- Verifica nel log del Flipper (`ufbt cli log`) che `TX 0x01 → ok` appaia.
- Verifica che bridge sia "connected" (non in reconnect loop).
- Se bridge dice "disconnected" subito dopo connect, può esserci un MTU mismatch — vedi Task 4 nota.

- [ ] **Step 5: Commit**

```bash
git add flipper_app/crt_remote_app.c
git commit -m "flipper-remote: TX 0x01 su Up button (first end-to-end)"
```

---

### Task 4: Map completo dei 7 comandi

Una volta verificato che 0x01 funziona, mappa tutti i pulsanti.

**Files:**
- Modify: `flipper_app/crt_remote_app.c`

- [ ] **Step 1: Estendi lo switch**

Sostituisci lo switch in `crt_remote_app()` con:
```c
            if(event.type == InputTypeShort) {
                switch(event.key) {
                    case InputKeyUp:    ble_serial_send_byte(&app, 0x01); break;  // next
                    case InputKeyDown:  ble_serial_send_byte(&app, 0x02); break;  // prev
                    case InputKeyOk:    ble_serial_send_byte(&app, 0x03); break;  // toggle
                    case InputKeyRight: ble_serial_send_byte(&app, 0x05); break;  // loop toggle
                    case InputKeyLeft:  ble_serial_send_byte(&app, 0x06); break;  // sync
                    case InputKeyBack:
                        running = false;
                        break;
                    default: break;
                }
            } else if(event.type == InputTypeLong) {
                switch(event.key) {
                    case InputKeyBack:  ble_serial_send_byte(&app, 0x04); break;  // stop
                    case InputKeyOk:    ble_serial_send_byte(&app, 0x07); break;  // calibrate
                    default: break;
                }
            }
```

(Nota: short Back esce dall'app, long Back invia stop. Adattabile in seguito se vuoi un'altra UX.)

- [ ] **Step 2: Build + flash + verifica ogni pulsante**

```bash
ufbt launch
```

Premi ciascuno dei 7 pulsanti/combinazioni e verifica nel log del bridge che ciascun byte arriva. Tabella veloce:

| Pulsante | Byte | Endpoint atteso |
|---|---|---|
| Up (short) | 0x01 | /control/next |
| Down (short) | 0x02 | /control/prev |
| OK (short) | 0x03 | /control/toggle |
| Back (long) | 0x04 | /control/stop |
| Right (short) | 0x05 | /control/loop/toggle |
| Left (short) | 0x06 | /control/sync |
| OK (long) | 0x07 | /control/calibrate |

- [ ] **Step 3: Commit**

```bash
git add flipper_app/crt_remote_app.c
git commit -m "flipper-remote: map completo 7 pulsanti → byte commands"
```

---

### Task 5: RX callback — ricezione frame dal bridge

Registra una callback per leggere i frame `0x01 + last_result` e `0x02 + status` che il bridge invia. Salva lo stato nell'app per disegnarlo.

**Files:**
- Modify: `flipper_app/crt_remote_app.c`

- [ ] **Step 1: Estendi la struct app per lo stato ricevuto**

Aggiungi a `CrtRemoteApp`:
```c
    char status_text[16];      // "idle"/"playing"/"paused"/"casting"/"error"/"-"
    int last_result;           // -1 idle, 0 ok, 1 http_err, 2 net_err
    uint32_t last_result_at;   // furi_get_tick() when last_result arrived
    FuriMutex* state_mutex;    // protects status_text/last_result (callback is on BT thread)
```

- [ ] **Step 2: Callback Serial RX**

Aggiungi sopra `ble_serial_start`:
```c
static uint16_t ble_serial_rx_cb(SerialServiceEvent event, void* ctx) {
    CrtRemoteApp* app = ctx;
    if(event.event != SerialServiceEventTypeDataReceived) {
        return 0;
    }
    if(event.data.size < 1) return 0;

    uint8_t frame_type = event.data.buffer[0];
    if(frame_type == 0x01 && event.data.size >= 2) {
        // last_result: 1 byte after type
        furi_mutex_acquire(app->state_mutex, FuriWaitForever);
        app->last_result = event.data.buffer[1];
        app->last_result_at = furi_get_tick();
        furi_mutex_release(app->state_mutex);
        FURI_LOG_I(CRT_REMOTE_LOG_TAG, "RX last_result=%d", event.data.buffer[1]);
    } else if(frame_type == 0x02 && event.data.size >= 2) {
        // status: ASCII bytes after type, max 15 chars + NUL
        size_t n = event.data.size - 1;
        if(n > sizeof(app->status_text) - 1) n = sizeof(app->status_text) - 1;
        furi_mutex_acquire(app->state_mutex, FuriWaitForever);
        memcpy(app->status_text, event.data.buffer + 1, n);
        app->status_text[n] = '\0';
        furi_mutex_release(app->state_mutex);
        FURI_LOG_I(CRT_REMOTE_LOG_TAG, "RX status=%s", app->status_text);
    } else {
        FURI_LOG_W(CRT_REMOTE_LOG_TAG, "RX unknown frame type=0x%02x size=%u",
                   frame_type, (unsigned)event.data.size);
    }

    // Tell the GUI to redraw.
    view_port_update(app->view_port);

    // Return value is "remaining buffer space hinted to peer" — return non-zero to signal we accepted.
    return event.data.size;
}
```

- [ ] **Step 3: Allocazione mutex + registrazione callback**

In `ble_serial_start`, dopo che `app->profile` è settato:
```c
    if(app->profile != NULL) {
        ble_profile_serial_set_event_callback(
            app->profile,
            BLE_PROFILE_SERIAL_PACKET_SIZE_MAX,
            ble_serial_rx_cb,
            app);
    }
```

In `crt_remote_app()` prima di `ble_serial_start`:
```c
    app.state_mutex = furi_mutex_alloc(FuriMutexTypeNormal);
    strncpy(app.status_text, "-", sizeof(app.status_text));
    app.last_result = -1;
```

In `crt_remote_app()` cleanup, dopo `ble_serial_stop`:
```c
    furi_mutex_free(app.state_mutex);
```

- [ ] **Step 4: Disegna stato e last_result sul display**

Sostituisci `draw_callback` con:
```c
static void draw_callback(Canvas* canvas, void* ctx) {
    CrtRemoteApp* app = ctx;
    canvas_clear(canvas);

    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 2, 12, "CRT Remote");

    canvas_set_font(canvas, FontSecondary);
    if(!app->profile) {
        canvas_draw_str(canvas, 2, 28, "BLE: init failed");
        return;
    }

    char line[32];
    furi_mutex_acquire(app->state_mutex, FuriWaitForever);
    snprintf(line, sizeof(line), "State: %s", app->status_text);
    int last = app->last_result;
    uint32_t age_ms = (last >= 0)
        ? (furi_get_tick() - app->last_result_at) * 1000U / furi_kernel_get_tick_frequency()
        : 0;
    furi_mutex_release(app->state_mutex);
    canvas_draw_str(canvas, 2, 28, line);

    // Show last_result for 2s after it arrives (toast-style).
    if(last >= 0 && age_ms < 2000U) {
        const char* label = "?";
        switch(last) {
            case 0: label = "OK";        break;
            case 1: label = "HTTP err";  break;
            case 2: label = "Net err";   break;
        }
        snprintf(line, sizeof(line), "→ %s", label);
        canvas_draw_str(canvas, 2, 42, line);
    }

    canvas_draw_str(canvas, 2, 60, "Back to exit");
}
```

- [ ] **Step 5: Periodic redraw per il fade del last_result**

Dopo 2s il toast deve sparire — serve un redraw periodico. La via semplice: timer ogni 500ms che chiama `view_port_update`.

In `CrtRemoteApp`:
```c
    FuriTimer* redraw_timer;
```

Sopra `ble_serial_start`:
```c
static void redraw_tick_cb(void* ctx) {
    CrtRemoteApp* app = ctx;
    view_port_update(app->view_port);
}
```

In `crt_remote_app()`, dopo l'allocazione del view_port:
```c
    app.redraw_timer = furi_timer_alloc(redraw_tick_cb, FuriTimerTypePeriodic, &app);
    furi_timer_start(app.redraw_timer, furi_kernel_get_tick_frequency() / 2);  // 500ms
```

In cleanup:
```c
    furi_timer_stop(app.redraw_timer);
    furi_timer_free(app.redraw_timer);
```

- [ ] **Step 6: Build + flash + verify**

```bash
ufbt launch
```

Sul Flipper: l'app mostra inizialmente "State: -". Quando il bridge si connette e fa il primo `GET /status`, dovresti vedere "State: idle" (o lo stato attuale del player).

Premi Up. Il display mostra "State: ..." invariato per un attimo, poi il toast "→ OK" appare per 2s.

Con daemon spento (`lodge crt-player stop`): dopo qualche secondo lo stato diventa "error".

- [ ] **Step 7: Commit**

```bash
git add flipper_app/crt_remote_app.c
git commit -m "flipper-remote: RX callback (frame 0x01/0x02) + status display"
```

---

### Task 6: UI polish — labels pulsanti

Aggiungi una mini-cheatsheet on-screen che mostra il mapping pulsanti.

**Files:**
- Modify: `flipper_app/crt_remote_app.c`

- [ ] **Step 1: Estendi `draw_callback` con label pulsanti**

Sotto la riga del state e (eventuale) toast, aggiungi (prima di "Back to exit"):
```c
    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str(canvas, 2, 50, "↑ next  ↓ prev  OK play");
```

E sostituisci la linea finale:
```c
    canvas_draw_str(canvas, 2, 60, "← sync  → loop  Hold OK calib");
```

(La display 128×64 è stretta; alcune label saranno troncate. Itera su layout dopo aver visto sul device.)

- [ ] **Step 2: Build + flash + verify visualmente**

```bash
ufbt launch
```

Sul Flipper, controlla che il layout sia leggibile.

- [ ] **Step 3: (opzionale) Aggiungi un'icona dell'app in `flipper_app/icons/icon_10x10.png`**

Se vuoi un'icona personalizzata in Apps menu, crea un PNG 10x10 monocromatico e dichiaralo in `application.fam`:
```python
    fap_icon="icons/icon_10x10.png",
    fap_icon_assets="icons",
```

Skippa se non vuoi spendere tempo su grafica.

- [ ] **Step 4: Commit**

```bash
git add flipper_app/
git commit -m "flipper-remote: UI labels per mapping pulsanti"
```

---

### Task 7: README finale + push

**Files:**
- Modify: `flipper_app/README.md` (se necessario, riprendere dalla bozza Task 1)

- [ ] **Step 1: Verifica che il README spiega:**
  - Build con `ufbt`
  - Flash con `ufbt launch`
  - Mapping pulsanti → comando
  - Cosa serve sul lato Lodge (link al servizio `crt-flipper-bridge`)
  - Limitazione nota: dopo aver chiuso l'app, può servire toggle Bluetooth in Settings per ripristinare HID/default profile (vedi Task 2 step 3 commento `TODO`).

- [ ] **Step 2: Aggiorna lo spec con stato Plan B = completato**

Modifica `docs/superpowers/specs/2026-05-10-flipper-remote-design.md`, sezione "Plan split", aggiungi `✅ Completato 2026-MM-DD` accanto a "Plan B".

```bash
git add docs/superpowers/specs/2026-05-10-flipper-remote-design.md
git commit -m "spec Flipper remote: marca Plan B (FAP) completato"
```

- [ ] **Step 3: Push del branch**

```bash
git push -u origin flipper-remote
```

Branch già esistente upstream. Se vuoi un PR:
```bash
gh pr create --title "Flipper Zero remote — FAP + bridge NUS pivot" --body "..."
```

---

## Self-Review Notes

Spec coverage:
- Activate BleProfileSerial via furi_hal_bt_change_app ✅ Task 2
- TX bytes su button press ✅ Task 3, Task 4
- RX callback con framing 0x01/0x02 ✅ Task 5
- UI con stato + last_result feedback ✅ Task 5, Task 6
- Build/flash con ufbt ✅ Task 1, README

Note di rischio dichiarate apertamente:
- Path degli include (`profiles/serial_profile.h`) potrebbe essere `services/serial_profile.h` su firmware diversi — lo step 1 di Task 2 lo segnala.
- Il restore del profilo precedente all'uscita non ha API pubblica netta — lasciato come TODO in Task 2.
- Il valore di ritorno della RX callback (`uint16_t`) è "remaining buffer space hint" e potrebbe non essere documentato univocamente — uso `event.data.size` come safe default.
- Il refresh periodico della GUI per il fade del toast è 500ms, basta. Se il flicker dà fastidio, si passa a un redraw triggerato da timestamp specifici (1.9s dopo il last_result_at).

Nessun placeholder, type/name consistency check passato (CrtRemoteApp / app->profile / state_mutex referenziati coerentemente attraverso le task).
