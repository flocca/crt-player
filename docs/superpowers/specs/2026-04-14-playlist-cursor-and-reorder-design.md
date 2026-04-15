# Playlist cursor + riordino coda — design

**Data:** 2026-04-14
**Scope:** cambio semantico della coda (cursore di playlist + `done` informativo) + pulsanti ↑/↓ per riordino nella UI.

## Motivazione

La coda è concettualmente una **playlist**: l'utente vuole poterla riordinare liberamente, rigiocare item già visti, e scegliere un punto qualsiasi da cui ripartire. Oggi:

- `QueueManager.move()` permette lo swap solo tra `queued` adiacenti (rifiuta anche solo di affiancare un `queued` a un `ready`).
- Gli item `done` sono terminali: la pipeline li salta e non esiste un modo pulito di rigiocarli senza chiudere/riaprire l'app (che trasforma implicitamente `done` con MP4 in cache in `ready` al reload).
- Non c'è un concetto di "loop" della playlist.
- La UI della coda non ha controlli per-riga; il riordino richiederebbe tastiera.

Vogliamo:

1. Riordino libero della coda tramite bottoni ↑/↓ visibili su ogni riga.
2. `done` come etichetta informativa, non come filtro di pipeline.
3. Cursore di playlist implicito: la riproduzione avanza all'item successivo per posizione, qualunque sia il suo status.
4. Modalità loop opzionale (default: stop alla fine della playlist).

## Modello concettuale

### Cursore di playlist

Il cursore è **implicito**: coincide con l'item che ha `status == "playing"` (o, se assente, con l'ultimo `done` incontrato nella lista). Non viene memorizzato come variabile separata.

### `done` informativo

Lo stato `done` non è più terminale. Un item `done` è funzionalmente equivalente a un `queued` *tranne* per:

- avere (molto probabilmente) l'MP4 già in cache → replay istantaneo;
- avere un simbolo visivo diverso nella UI (già oggi: spunta dim).

La pipeline non lo filtra più. Il prefetch in background **non** lo tocca automaticamente (sarebbe spreco di rete/CPU); viene preparato solo se il cursore ci atterra sopra.

### Avanzamento del cursore

Quando l'item `playing` termina (fine naturale, skip utente, stop esplicito):

1. Lo stato dell'item diventa `done`.
2. Si determina il prossimo item per posizione: `items[index_of(done) + 1]`.
3. Se oltre la fine della lista:
   - **Stop mode** (default): la pipeline si ferma, `cast_enabled` resta True ma nessun target esiste.
   - **Loop mode**: il cursore torna a `items[0]`.
4. Il prossimo item viene preparato on-demand (vedi sotto) e castato.

### Preparazione on-demand

Quando il cursore atterra su un item non-`ready`, la pipeline lo porta a `ready` prima di castare:

| Status in ingresso | Azione |
|---|---|
| `ready` | nessuna azione, cast diretto |
| `done` + MP4 in cache | transizione diretta a `ready`, cast istantaneo |
| `done` senza MP4 | reset a `queued` + `filename=None` → download → encode → cast |
| `error` | reset a `queued` → download → encode → cast |
| `queued` / `downloading` / `encoding` | la pipeline prosegue il lavoro già in corso |

### Riproduzione manuale

Cliccando Play su un item qualsiasi (incluso `done`):

1. Se esiste un item con status `playing`, viene interrotto (`cast.stop()`) e marcato `done`.
2. Si chiama `prepare_for_play` sull'item scelto (porta `done`/`error` a `ready` o `queued`).
3. L'item scelto viene forzato come target del prossimo cast, **bypassando** la logica cursore→avanzamento. In pratica la pipeline espone `cast_now(item)` che salta `advance_cursor` per un giro.
4. Dopo che l'item scelto diventa `playing`, da quel momento il cursore riparte da lì per gli avanzamenti successivi.

Il bypass è necessario perché, se ci limitassimo a `playing → done` + wake, `advance_cursor` troverebbe come cursore l'item appena interrotto e sceglierebbe il suo successore posizionale — non l'item che l'utente ha effettivamente cliccato.

## Macchina degli stati aggiornata

Insieme degli stati invariato: `queued`, `downloading`, `encoding`, `ready`, `casting`, `playing`, `done`, `error`.

### Transizioni nuove/modificate

- `done` → `ready` — quando il cursore ci atterra sopra e l'MP4 è ancora in cache.
- `done` → `queued` — quando il cursore ci atterra sopra e l'MP4 non esiste più.
- `error` → `queued` — quando il cursore ci atterra sopra (retry automatico).

Transizione `playing` → `done` invariata (fine naturale, skip, stop utente).

## API `QueueManager`

### `move()` — semplificato

```python
def move(self, item_id: str, direction: str) -> bool:
    for i, item in enumerate(self.items):
        if item.id == item_id:
            if direction == "up" and i > 0:
                self.items[i], self.items[i - 1] = self.items[i - 1], self.items[i]
                return True
            if direction == "down" and i < len(self.items) - 1:
                self.items[i], self.items[i + 1] = self.items[i + 1], self.items[i]
                return True
            return False
    return False
```

Nessun controllo di status. Limiti solo ai bordi.

### `can_move()` — nuovo

```python
def can_move(self, item_id: str, direction: str) -> bool:
    for i, item in enumerate(self.items):
        if item.id == item_id:
            if direction == "up":
                return i > 0
            if direction == "down":
                return i < len(self.items) - 1
    return False
```

Usato dalla UI per decidere lo stato `disabled` dei bottoni senza mutare la coda.

### `advance_cursor()` — nuovo

```python
def advance_cursor(self, loop: bool) -> QueueItem | None:
    """Return the next item to play, or None if end of playlist (stop mode).
    Assumes the just-ended item has status='done'. Locates the cursor by
    position, not by status."""
```

Comportamento:

1. Trova il cursore: primo `playing` se esiste, altrimenti ultimo `done`.
2. Se cursore `None`: ritorna `items[0]` se non vuota, altrimenti `None`.
3. `next_idx = cursor_idx + 1`.
4. Se `next_idx >= len(items)`: ritorna `items[0]` se `loop`, altrimenti `None`.
5. Altrimenti ritorna `items[next_idx]`.

**Non muta lo stato.** Il cast loop è responsabile di settare `playing` → `done` prima di chiamare `advance_cursor`.

### `prepare_for_play(item)` — nuovo

```python
def prepare_for_play(self, item: QueueItem) -> None:
    """Transition item to the correct pre-play state based on cache."""
```

- `ready` → invariato.
- `done` / `error` → `ready` se `filename` punta a un MP4 esistente in `TEMP_DIR`, altrimenti `queued` + reset `filename=None`, `progress=0.0`, `error=None`.
- `queued` / `downloading` / `encoding` → invariato.

### `first_queued_after_cursor()` — nuovo (sostituisce `first_queued` nel prefetch)

```python
def first_queued_after_cursor(self) -> QueueItem | None:
    """First 'queued' item after the cursor position. If no cursor, starts
    from the beginning (equivalent to the old first_queued())."""
```

Il prefetch opera solo su ciò che sta dopo il cursore; `done` davanti al cursore resta non-toccato.

### `next_ready()` — rimosso

Non più usato dal cast loop. I test che lo referenziano vanno rimossi/riscritti.

## Pipeline

### Cast loop riscritto

```python
async def _cast_loop(self):
    while self._cast_enabled:
        target = self._pick_next_to_cast()
        if target is None:
            await self._cast_wake.wait()
            continue

        self.queue.prepare_for_play(target)

        if target.status != "ready":
            # In corso di preparazione: aspetta che prepare_loop lo porti a ready
            await self._ready_event.wait()
            self._ready_event.clear()
            continue

        await self._cast_item(target)       # ready → casting → playing
        await self._wait_playback_end()     # idle reason FINISHED/CANCELLED
        target.status = "done"
        # Il giro successivo del loop chiama _pick_next_to_cast e gestisce
        # fine-playlist / loop mode / stop mode.
```

### `_pick_next_to_cast()`

```python
def _pick_next_to_cast(self) -> QueueItem | None:
    # Priorità: item già in corso (cast non interrompibile).
    for item in self.queue.items:
        if item.status in ("casting", "playing"):
            return item
    # Altrimenti: delega a QueueManager.advance_cursor.
    return self.queue.advance_cursor(loop=self.loop_mode)
```

`advance_cursor` è l'unico punto in cui vive la logica del cursore (trova `playing`/ultimo `done`, calcola `next_idx`, gestisce loop/stop). `_pick_next_to_cast` aggiunge solo la regola "cast in corso non si tocca".

### Prepare loop

Solo un cambio: usa `first_queued_after_cursor()` al posto di `first_queued()`.

### Wake

Eventi che risvegliano il worker (uguali a oggi + uno nuovo):

- Nuovo item aggiunto → `wake()`
- `move()` ↑/↓ → `wake()` (prefetch potrebbe dover cambiare target)
- Toggle loop ON → `wake()` (può far ripartire la pipeline da fine playlist)
- Skip utente → `wake()`

### Item spostato/rimosso durante riproduzione

- **Spostato:** il cast prosegue (il Chromecast ha già l'URL); alla fine, `advance_cursor` legge la nuova posizione in lista e ritorna il vicino corrente.
- **Rimosso:** `advance_cursor` non trova né `playing` né `done` → ritorna `items[0]` se non vuota, altrimenti `None`. Degradazione sicura.

## UI

### Layout del `QueueListItem`

Oggi: singola `Label` con markup Rich.

Nuovo: `Horizontal` con label a sinistra (`width: 1fr`) e bottoni ↑/↓ a destra (`width: auto`).

```python
def compose(self) -> ComposeResult:
    with Horizontal(classes="queue-row"):
        yield Label(self._build_label(), classes="queue-title")
        with Horizontal(classes="queue-actions"):
            yield Button("↑", id=f"up-{self.queue_item.id}", classes="queue-action-btn")
            yield Button("↓", id=f"down-{self.queue_item.id}", classes="queue-action-btn")
```

CSS aggiuntivo:

```css
.queue-row { height: 1; }
.queue-title { width: 1fr; }
.queue-actions { width: auto; height: 1; }
.queue-action-btn {
    min-width: 3;
    height: 1;
    border: none;
    background: transparent;
    color: $text;
    padding: 0;
    margin: 0 0 0 1;
}
.queue-action-btn:hover { background: $accent; }
.queue-action-btn:disabled { color: $text-disabled; }
```

### Handler

```python
def on_button_pressed(self, event: Button.Pressed) -> None:
    btn_id = event.button.id or ""
    if btn_id.startswith("up-") or btn_id.startswith("down-"):
        direction, _, item_id = btn_id.partition("-")
        if self.queue.move(item_id, direction):
            self._refresh_queue_list()
            self.pipeline.wake()  # il prefetch target può cambiare
        event.stop()
```

L'`event.stop()` è necessario perché il `Button.Pressed` dentro un `ListItem` farebbe bubble al `QueueListView` e scatterebbe `ListView.Selected` (stesso pattern già usato per il click mouse-vs-tastiera in `QueueListView.on_list_view_selected`).

### Refresh dello stato `disabled`

In `_refresh_queue_list`, dopo aver sincronizzato gli item:

```python
for qli in list_view.query(QueueListItem):
    up_btn = qli.query_one(f"#up-{qli.queue_item.id}", Button)
    down_btn = qli.query_one(f"#down-{qli.queue_item.id}", Button)
    up_btn.disabled = not self.queue.can_move(qli.queue_item.id, "up")
    down_btn.disabled = not self.queue.can_move(qli.queue_item.id, "down")
```

### Toggle loop

Nuovo binding `Binding("ctrl+r", "toggle_loop", "Loop", show=True, priority=True)`.

```python
def action_toggle_loop(self) -> None:
    self.loop_mode = not self.loop_mode
    self.pipeline.loop_mode = self.loop_mode
    self.notify(f"Loop: {'ON' if self.loop_mode else 'OFF'}")
    self._refresh_loop_indicator()
    if self.loop_mode:
        self.pipeline.wake()
```

Indicatore nell'header della coda: ` CODA ` diventa ` CODA ⟳` quando loop è ON.

Stato `loop_mode` **session-local**, non persistito in `state.json`. Inizializzato da `config.LOOP_MODE_DEFAULT`.

## Configurazione

Nuovo env var in `config.py`:

```python
LOOP_MODE_DEFAULT = os.getenv("CRT_LOOP", "0") == "1"
```

Default: stop mode. Il toggle runtime sovrascrive per la sessione corrente.

## File modificati

| File | Cambiamenti |
|---|---|
| `queue_manager.py` | `move()` semplificato; nuovi `can_move`, `advance_cursor`, `prepare_for_play`, `first_queued_after_cursor`; `next_ready` rimosso. |
| `pipeline.py` | Cast loop riscritto attorno a `_pick_next_to_cast` + `advance_cursor`; prepare loop usa `first_queued_after_cursor`; attributo `loop_mode`. |
| `ui.py` | `QueueListItem.compose()` con `Horizontal` + `Button ↑/↓`; `on_button_pressed` per up/down; `_refresh_queue_list` aggiorna `disabled`; `action_toggle_loop` + binding `ctrl+r`; indicatore `⟳` nell'header; campo `loop_mode` propagato alla pipeline. |
| `config.py` | `LOOP_MODE_DEFAULT` da `CRT_LOOP`. |
| `CLAUDE.md` | Documenta `CRT_LOOP`, nuova semantica `done`, cursore, helper nuovi. |

## Testing

### Unit tests nuovi in `tests/test_queue_manager.py`

- `move()` scambia item di qualsiasi status: `queued ↔ ready`, `queued ↔ playing`, `queued ↔ done`, `done ↔ done`.
- `move()` ritorna `False` solo ai bordi (primo su ↑, ultimo su ↓).
- `can_move()` coerente con i casi sopra.
- `advance_cursor(loop=False)`: cursore interno → item successivo.
- `advance_cursor(loop=False)`: cursore sull'ultimo → `None`.
- `advance_cursor(loop=True)`: cursore sull'ultimo → primo item.
- `advance_cursor`: nessun `playing` né `done` → primo item se lista non vuota, `None` se vuota.
- `advance_cursor`: cursore = ultimo `done` quando `playing` assente.
- `prepare_for_play`: `done` con MP4 in cache → `ready` (file esistente in `TEMP_DIR`).
- `prepare_for_play`: `done` senza MP4 → `queued` con `filename=None`.
- `prepare_for_play`: `error` → `queued`.
- `prepare_for_play`: `ready` / `queued` / `downloading` / `encoding` → invariati.
- `first_queued_after_cursor`: salta item (anche `queued`) prima del cursore.
- `first_queued_after_cursor`: senza cursore, comportamento == vecchio `first_queued`.

### Unit tests aggiornati

- `tests/test_queue_manager.py` — rimuovere i test della vecchia semantica di `move()` (che verificano il rifiuto su non-queued).
- Eventuali test in `tests/test_pipeline.py` che referenziano `next_ready()`.

### UI tests nuovi in `tests/test_ui.py`

- Click su `↑` di un item invoca `queue.move(id, "up")` e rinfresca la lista.
- Click su `↑` del primo item: bottone `disabled`, click senza effetto.
- Click su `↓` dell'ultimo item: bottone `disabled`, click senza effetto.
- Click su `↑`/`↓` non scatena `ListView.Selected` (verifica di `event.stop()`).
- `ctrl+r` flippa `self.loop_mode` e aggiorna l'indicatore nell'header.
- Toast "Loop: ON/OFF" su toggle.

### Rispetto del gotcha CLAUDE.md

I test che mutano `config.LOOP_MODE_DEFAULT` devono avere `autouse` `_restore_config` fixture (stesso pattern già usato in `tests/test_pipeline.py` e `tests/test_calibration.py`) per evitare contaminazione cross-file.

### Integration tests (opzionali, `tests/test_integration.py`)

- Playlist di 2 item, primo arriva a `done`, il secondo parte automatico (verifica advance).
- Loop attivo, 1 item che finisce e riparte da capo. **Attenzione:** timeout alto e interruzione esplicita per evitare loop infinito in CI.

## Non-scope

- Azioni "rimuovi item" / "sposta in cima" / "sposta in fondo" nella colonna azioni — solo ↑/↓ in questa spec.
- Pulsante dedicato per replay di un `done` — non serve: Enter/Play sull'item funziona come su qualsiasi altro grazie alla nuova semantica.
- Persistenza di `loop_mode` in `state.json`.
- Modifica della UI del "Now Playing" per mostrare stato cursore/loop.
- Prefetch automatico dei `done` (resta disattivato per risparmio risorse).
