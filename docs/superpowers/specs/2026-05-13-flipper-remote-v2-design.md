# Telecomando Flipper Zero v2 — design

**Data:** 2026-05-13
**Scope:** ridisegnare la UI della FAP `crt_remote` (rotazione display 90° anti-orario, nuovo mapping pulsanti, menu in-app per comandi extra) e aggiungere tre nuovi comandi (seek -15s, seek +30s, delete current video) nel daemon, nel bridge e nella FAP. Costruisce sopra [2026-05-10-flipper-remote-design.md](./2026-05-10-flipper-remote-design.md) (protocollo NUS + framing + tabella comandi v1) — il contratto byte-level non cambia, viene esteso da 7 a 10 byte.

## Motivazione

La UX attuale tiene il Flipper in verticale e mappa i 7 comandi su short/long-press dei tasti fisici. Limiti:
- Usabilità da divano: il Flipper è ergonomicamente migliore tenuto in orizzontale (forma da gamepad).
- Niente seek: comandi base di un media remote (avanti/indietro 15-30s) mancano del tutto.
- Niente delete: l'utente non può scartare video indesiderati dalla playlist senza tornare alla TUI.

Questa iterazione risolve i tre punti con minimi cambiamenti al contratto BLE.

## Decisioni di alto livello

1. **Rotazione 90° anti-orario.** Il Flipper si tiene in orizzontale come un gamepad, con il bordo che normalmente è il lato destro del device ora rivolto verso l'alto dell'utente. La FAP imposta `canvas_set_orientation(canvas, CanvasOrientationVertical)` in `draw_callback`; tutta la drawing usa coordinate post-rotazione (64×128 logico). La convenzione esatta dell'enum (`Vertical` vs `VerticalFlip` = 90° CCW vs 90° CW) va verificata empiricamente in implementazione — Flipper SDK non documenta in modo univoco quale enum produce quale rotazione.

2. **Nuovo mapping pulsanti su `SceneHome`** (POV utente con device ruotato):

   | Physical key | Utente vede | Azione | Byte BLE |
   |---|---|---|---|
   | `InputKeyUp` short | "Left" | Seek -15s | `0x08` *(new)* |
   | `InputKeyDown` short | "Right" | Seek +30s | `0x09` *(new)* |
   | `InputKeyLeft` short | "Down" | Next video | `0x01` *(existing)* |
   | `InputKeyRight` short | "Up" | Prev video | `0x02` *(existing)* |
   | `InputKeyOk` short | OK | Play/Pause | `0x03` *(existing)* |
   | `InputKeyOk` long | OK held | Entra in `SceneExtraMenu` | — *(in-FAP only)* |
   | `InputKeyBack` short | Back | Exit app | — |
   | `InputKeyBack` long | — | (libero) | — |

3. **Menu extra in-FAP** (`SceneExtraMenu`): lista navigabile full-screen, 5 voci hardcoded — Stop, Elimina video, Calibrate, Toggle loop, Sync now. OK conferma + ritorna a `SceneHome`. Back annulla. Cursore wrap-free (clamp ai bordi).

   Mapping su `SceneExtraMenu` (POV utente coerente con `SceneHome`):

   | Physical key | Utente vede | Azione |
   |---|---|---|
   | `InputKeyRight` short | "Up" | Cursore su |
   | `InputKeyLeft` short | "Down" | Cursore giù |
   | `InputKeyOk` short | OK | Esegui voce, torna a `SceneHome` |
   | `InputKeyBack` short | Back | Torna a `SceneHome` senza azione |

4. **Tre comandi nuovi**: `SEEK_BACK_15` (0x08), `SEEK_FORWARD_30` (0x09), `DELETE_CURRENT` (0x0A). Path-param `seconds` nell'URL daemon — il numero "ufficiale" è hardcoded a 15/30 nella FAP e nel bridge, ma il daemon accetta qualsiasi int (utile per chiamate manuali, fuori scope per la FAP).

5. **Niente parametrizzazione del byte protocol** (approccio B rigettato). Resta 1 byte per comando, framing invariato.

6. **YouTube write scope.** "Elimina video" cancella sia localmente che dalla playlist YouTube remota. Richiede ampliare `SCOPES` da `youtube.readonly` a `youtube`. Re-OAuth bootstrap obbligatorio una tantum; documentato nel rollout.

7. **Niente conferma per delete.** Decisione utente: esecuzione immediata sulla voce di menu, nessuna schermata di conferma. Trade-off accettato: rischio di mis-tap su un'azione irreversibile.

## Protocollo BLE (tabella aggiornata)

Framing invariato vs [2026-05-10-flipper-remote-design.md](./2026-05-10-flipper-remote-design.md): TX = byte sequence (più press possono arrivare nello stesso pacchetto). Byte sconosciuti nel bridge → warn + skip.

| Byte | Comando | Endpoint daemon | Note |
|---|---|---|---|
| `0x01` | NEXT | `POST /control/next` | existing |
| `0x02` | PREV | `POST /control/prev` | existing |
| `0x03` | TOGGLE | `POST /control/toggle` | existing |
| `0x04` | STOP | `POST /control/stop` | existing, ora in menu extra |
| `0x05` | LOOP | `POST /control/loop/toggle` | existing, ora in menu extra |
| `0x06` | SYNC | `POST /control/sync` | existing, ora in menu extra |
| `0x07` | CALIBRATE | `POST /control/calibrate` | existing, ora in menu extra |
| `0x08` | SEEK_BACK_15 | `POST /control/seek/back/15` | new |
| `0x09` | SEEK_FORWARD_30 | `POST /control/seek/forward/30` | new |
| `0x0A` | DELETE_CURRENT | `POST /control/delete/current` | new |

RX (bridge → Flipper) resta no-op come in v1; nessun cambio.

## Componente 1 — Daemon (`crt/`)

### `crt/api.py` — 3 endpoint nuovi

```python
@app.post("/control/seek/back/{seconds}")
async def control_seek_back(seconds: int):
    if player is None: raise HTTPException(503, "player unavailable")
    await player.seek_relative(-seconds)
    return {"ok": True}

@app.post("/control/seek/forward/{seconds}")
async def control_seek_forward(seconds: int):
    if player is None: raise HTTPException(503, "player unavailable")
    await player.seek_relative(seconds)
    return {"ok": True}

@app.post("/control/delete/current")
async def control_delete_current():
    if player is None: raise HTTPException(503, "player unavailable")
    video_id = library.cursor_video_id
    if not video_id: raise HTTPException(404, "no current video")
    await player.delete_current()
    return {"ok": True, "deleted_video_id": video_id}
```

Path param per `seconds` per non bloccare il valore in schema; il bridge chiama sempre 15/30 fissi.

### `crt/chromecast_mgr.py`

Aggiungere `seek_relative(delta_seconds: float)`:

```python
def seek_relative(self, delta_seconds: float) -> None:
    if self.current_time is None:
        log.info("seek_relative: no current_time, skipping")
        return
    new_pos = max(0.0, self.current_time + delta_seconds)
    self._safe_cmd(lambda: self.cast.media_controller.seek(new_pos))
```

Niente clamp superiore al `current_time + delta`. Da verificare in implementazione: se seek oltre la durata produce comportamento erratico (player IDLE senza `FINISHED` reason), aggiungere clamp a `duration - 0.5s`. Lascio fuori dal contratto v1 per evitare un round-trip extra a `update_status()`.

### `crt/player_core.py`

```python
async def seek_relative(self, seconds: int) -> None:
    await asyncio.to_thread(self.chromecast.seek_relative, seconds)

async def delete_current(self) -> None:
    item = self.library.cursor_item()  # may need to add this helper
    if not item: return
    await self.stop()
    await asyncio.to_thread(self._delete_local, item)
    if item.playlist_item_id:
        try:
            await asyncio.to_thread(self.youtube.delete_playlist_item, item.playlist_item_id)
        except Exception as e:
            log.error("YouTube remote delete failed for %s: %s", item.video_id, e)
    else:
        log.warning("playlist_item_id missing for %s; remote delete skipped", item.video_id)

def _delete_local(self, item) -> None:
    self.library.remove(item.id)
    cache_path = os.path.join(
        config.TEMP_DIR,
        cached_encoded_filename(item.video_id, config.SCALE_MODE,
                                config.MARGIN_TOP, config.MARGIN_BOTTOM,
                                config.MARGIN_LEFT, config.MARGIN_RIGHT),
    )
    if os.path.isfile(cache_path):
        os.unlink(cache_path)
```

Errori YouTube non bloccano la rimozione locale: idempotenza accettata, il prossimo sync riconcilia.

### `crt/youtube_client.py`

- `SCOPES = ["https://www.googleapis.com/auth/youtube"]` (read+write).
- `PlaylistEntry` estesa con `playlist_item_id: str` (popolato da `raw["id"]` in `_list_inner`).
- Nuovo metodo:

```python
def delete_playlist_item(self, playlist_item_id: str) -> None:
    try:
        self._api.playlistItems().delete(id=playlist_item_id).execute()
    except HttpError as e:
        status = getattr(e.resp, "status", None)
        if status in (401, 403):
            raise YouTubeAuthError(f"YouTube auth error ({status}): {e}") from e
        if status == 404:
            log.info("playlist item %s already gone", playlist_item_id)
            return
        raise
```

### `crt/library_store.py`

- `QueueItem` aggiunge `playlist_item_id: str | None = None`.
- Aggiungere `cursor_item()` helper (ritorna `next((i for i in self.items if i.video_id == self.cursor_video_id), None)`).
- Migrazione state.json: versione `v3`. Item esistenti con `playlist_item_id is None` sono validi; il prossimo sync li popola.

### `crt/sync_engine.py`

Quando crea/aggiorna `QueueItem` da `PlaylistEntry`, propaga `entry.playlist_item_id`. Niente altri cambi.

### `crt/bootstrap.py`

Niente cambi al codice (usa già `SCOPES` da `youtube_client`). Ma all'utente serve ri-eseguire `crt-bootstrap` perché il token salvato non copre il nuovo scope; documentato in Rollout.

## Componente 2 — Bridge (`lodge-tools/services/crt-flipper-bridge/`)

Niente refactor strutturale. Aggiungere 3 righe a `COMMAND_TABLE`:

```python
COMMAND_TABLE = {
    0x01: "/control/next",
    0x02: "/control/prev",
    0x03: "/control/toggle",
    0x04: "/control/stop",
    0x05: "/control/loop/toggle",
    0x06: "/control/sync",
    0x07: "/control/calibrate",
    0x08: "/control/seek/back/15",       # new
    0x09: "/control/seek/forward/30",    # new
    0x0A: "/control/delete/current",     # new
}
```

Aggiornare i test unit di `parse_command` per coprire i 3 nuovi byte. Re-deploy.

## Componente 3 — FAP (`flipper_app/`)

Niente split in scene-files separate (overkill per 2 scene statiche). Tutto resta in `crt_remote_app.c` (~280 righe stimate).

### Stato app

```c
typedef enum {
    SceneHome = 0,
    SceneExtraMenu,
} Scene;

typedef struct {
    // ... existing fields (input_queue, view_port, gui, bt, profile, ble_state) ...
    Scene scene;
    uint8_t menu_index;
} CrtRemoteApp;

#define CMD_NEXT             0x01
#define CMD_PREV             0x02
#define CMD_TOGGLE           0x03
#define CMD_STOP             0x04
#define CMD_LOOP             0x05
#define CMD_SYNC             0x06
#define CMD_CALIBRATE        0x07
#define CMD_SEEK_BACK_15     0x08
#define CMD_SEEK_FORWARD_30  0x09
#define CMD_DELETE           0x0A

typedef struct { const char* label; uint8_t cmd_byte; } MenuItem;

static const MenuItem MENU_ITEMS[] = {
    {"Stop",          CMD_STOP},
    {"Elimina video", CMD_DELETE},
    {"Calibrate",     CMD_CALIBRATE},
    {"Toggle loop",   CMD_LOOP},
    {"Sync now",      CMD_SYNC},
};
#define MENU_ITEMS_COUNT (sizeof(MENU_ITEMS) / sizeof(MENU_ITEMS[0]))
```

### Input dispatch

Single input handler in `crt_remote_app()` ramifica su `app.scene` (`SceneHome` / `SceneExtraMenu`) come da sezione 4 del brainstorming. Long-press OK in `SceneHome` setta `scene = SceneExtraMenu; menu_index = 0` e chiama `view_port_update`. Voce-OK in `SceneExtraMenu` invia il byte corrispondente, torna a `SceneHome`. Back in `SceneExtraMenu` torna a `SceneHome` senza inviare nulla.

### Drawing

`draw_callback` chiama `canvas_set_orientation(canvas, CanvasOrientationVertical)` per primo, poi ramifica su `app->scene`:

**`SceneHome`** (64×128 logico):
- Header `CRT Remote` FontPrimary, centrato y≈10.
- Riga `BLE: <state>` FontSecondary y≈22.
- Block 4 labels (POV utente):
  - `◀ -15s` y≈42
  - `▶ +30s` y≈54
  - `▲ prev` y≈66
  - `▼ next` y≈78
- Footer: `OK = play/pause` y≈100; `hold OK → extras` y≈115.

**`SceneExtraMenu`**:
- Header `Comandi` FontPrimary y≈10.
- 5 voci a y ∈ {30, 42, 54, 66, 78}, FontSecondary, prefisso `> ` sulla voce selezionata, `  ` sulle altre.
- Footer: `OK conferma` y≈110, `Back annulla` y≈120.

Le frecce `◀▶▲▼` sono char ASCII/UTF-8 leggibili nella font Flipper standard. Se non rese correttamente in implementazione, fallback a `<-`, `->`, `^`, `v`.

### File layout

Nessun nuovo file. `crt_remote_app.c` cresce da ~160 a ~280 righe stimate. `application.fam` invariato (niente `sources=`, niente `requires` nuovi).

## Testing

### Daemon (pytest, no hardware)

- `tests/test_api.py`: 3 nuovi test per i nuovi endpoint con `player` mockato. Verifica path param parsing per seek, 404 quando `cursor_video_id is None` per delete.
- `tests/test_player_core.py`:
  - `seek_relative`: no-op se `current_time is None`; clamp a 0 per seek-back oltre l'inizio.
  - `delete_current`: ordine delle chiamate (stop → library.remove → unlink → youtube.delete_playlist_item); errore YouTube non blocca; assenza di `playlist_item_id` salta solo lo step remoto.
- `tests/test_youtube_client.py`: `delete_playlist_item` con mock googleapiclient — 401/403 → `YouTubeAuthError`; 404 → swallow; altri 5xx → re-raise.
- `tests/test_library_store.py`: `QueueItem` accetta `playlist_item_id=None`; migrazione v2→v3 setta `None` per item esistenti.
- `tests/test_sync_engine.py`: sync popola `playlist_item_id` da `PlaylistEntry`.
- `tests/test_state_v2_migration.py` (esistente): estendere con caso v2→v3.

### Bridge (lodge-tools)

Estendere fixture di `parse_command` con i 3 nuovi byte. Nessun nuovo file di test.

### FAP

Niente unit test (toolchain non li supporta). Smoke test manuale on-device:
1. `ufbt launch` + verifica `BLE: active` su Flipper.
2. Ogni pulsante in `SceneHome` → verifica byte ricevuto nei `lodge crt-flipper-bridge logs`.
3. Long-press OK → menu appare; navigazione su/giù; OK su ogni voce → verifica byte e ritorno a home.
4. Back nel menu → ritorno a home senza byte trasmesso.

### Integration

Nessun nuovo test di integrazione automatizzato (Chromecast hardware non testabile in CI). Smoke manuale post-deploy: seek visibile sul TV, delete fa sparire l'item sia dalla libreria locale che dalla playlist YouTube web.

## Rollout

Ordinato per minimizzare il rischio di break in produzione:

1. **Daemon refactor** (questo repo) — merge → `lodge crt-player update` → restart container. Endpoint vecchi continuano a funzionare; i 3 nuovi sono no-op se non chiamati. Re-OAuth non ancora necessario.
2. **Re-OAuth** (manuale, una tantum) — `crt-bootstrap` su Mac (browser consent col nuovo scope `youtube`), `scp oauth_token.json` su `/opt/lodge/crt-player/secrets/` su Lodge (oppure `lodge crt-player install` che lo include), restart container. Verifica nei log che `sync_engine` continua a funzionare (sync periodico ok, niente 401).
3. **Bridge update** (lodge-tools repo) — `COMMAND_TABLE` allargata, `lodge crt-flipper-bridge update` → deploy.
4. **FAP refactor** (questo repo, `flipper_app/`) — `ufbt launch` con Flipper connesso via USB.
5. **End-to-end smoke** — esercita tutti e 10 i comandi dal Flipper, verifica nel `lodge crt-player logs` che gli endpoint corretti vengono colpiti.

Rollback: in caso di problema con un singolo step, ogni step è autonomamente reversibile (rollback git del repo corrispondente + redeploy).

## Doc updates post-implementazione

- `flipper_app/CLAUDE.md` — aggiornare tabella "Button → command byte mapping" (10 byte, nuovo POV utente ruotato, scene model), aggiungere nota su `canvas_set_orientation`.
- `CLAUDE.md` (root) — aggiornare l'enumerazione di `/control/*` nella sezione "Production deployment / HTTP control surface" con i 3 nuovi endpoint; aggiungere nota sul re-OAuth scope `youtube` write.
- `lodge-tools/services/crt-player/CLAUDE.md` — runbook re-bootstrap.
- `lodge-tools/services/crt-flipper-bridge/CLAUDE.md` — mirror COMMAND_TABLE aggiornato.

## Open question risolte in brainstorming

| Domanda | Decisione |
|---|---|
| Scope di "Elimina video" (locale, remoto, blocklist) | Locale + remoto YouTube. Richiede re-OAuth con scope `youtube`. |
| Comportamento menu extra | Lista navigabile full-screen. OK conferma, Back annulla. |
| Conferma su delete | Nessuna. Esecuzione immediata. |
| Loop / Sync nella nuova UX | Mantenuti come voci del menu extra. |
| Approccio protocollo | A — 1 byte per comando, endpoint fissi. |
| Rotazione display | 90° anti-orario; `CanvasOrientationVertical` (da verificare empiricamente). |

## Non-goals (v1 di questa iterazione)

- **RX bridge → FAP**: status/last_result feedback resta in spec ma non implementato. La home mostra solo BLE link state.
- **Schermata di conferma su delete**: utente l'ha esplicitamente rigettata.
- **Seek con valori configurabili runtime**: hardcoded 15/30. Cambio futuro = nuovo byte o approccio B.
- **Cleanup batch della cache MP4** per video non-correnti: fuori scope.
- **Visualizzazione titolo video corrente in `SceneHome`**: richiede RX dal bridge (sopra), rimandato.
- **Conferma vocale / feedback haptic** all'invio comandi: fuori scope.
