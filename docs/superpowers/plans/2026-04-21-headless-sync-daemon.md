# Headless Sync + Player Daemon — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trasformare il prototipo crt-player in un daemon headless che sincronizza una playlist YouTube dedicata e la casta a un Chromecast, controllato via HTTP API. La TUI esistente diventa un client remoto opzionale.

**Architecture:** Singolo processo Python (`crt-daemon`) con moduli interni distinti (`SyncEngine`, `LibraryStore`, `PipelineWorker`, `PlayerCore`, `YouTubeClient`, `api`). Un unico FastAPI/uvicorn espone `/library/*`, `/control/*`, `/status`, `/media/*`. Sync unidirezionale YouTube → library (YT è master). Cursor playback esplicito persistito in state.json v2. Bind LAN, no auth (F1 trust LAN). Vedi spec [2026-04-21-headless-sync-daemon-design.md](../specs/2026-04-21-headless-sync-daemon-design.md).

**Tech Stack:** Python 3.12, asyncio, FastAPI/uvicorn, pychromecast, yt-dlp, ffmpeg (CLI), Textual (TUI client), google-api-python-client + google-auth-oauthlib (YouTube), pytest + pytest-asyncio.

---

## Convenzioni del piano

- **TDD**: per ogni nuovo modulo si scrive il test, lo si vede fallire, si scrive l'implementazione minima, si vede passare, si committa.
- **Refactoring meccanici** (sposta file da A a B): usano una variante più compatta — niente "scrivi test fail-first", basta "muovi + aggiorna import + run all tests + commit".
- **Comandi**: tutti dalla root del repo, ambiente `.venv` attivo. Per attivarlo: `source .venv/bin/activate`.
- **`source .env`**: ad ogni cambio significativo di env vars, ricaricare via `set -a; source .env; set +a` se si esegue al di fuori di `./run.sh`.
- **Test runner**: `python -m pytest` per la suite completa; `python -m pytest tests/path::name -v` per il singolo.
- **Commit**: ogni task termina con un commit. Messaggio di commit nel formato del repo (italiano breve, prefisso del tipo `Phase 1: ...` opzionale per orientarsi nel log).

---

## File structure

### File creati

| Path | Responsabilità |
|---|---|
| `crt/__init__.py` | Package marker. |
| `crt/__main__.py` | `python -m crt.daemon` entry point. |
| `crt/daemon.py` | Orchestrazione: load config, init moduli, avvia task asyncio, signal handling. |
| `crt/bootstrap.py` | Sotto-comando OAuth interattivo per ottenere il refresh token. |
| `crt/sync_engine.py` | Polling YouTube + diff add/remove/reorder. |
| `crt/youtube_client.py` | Wrapper su `googleapiclient` per `playlistItems.list`. |
| `crt/library_store.py` | Evoluzione di `queue_manager.py` con `cursor_video_id`, `loop_mode`, schema v2. |
| `crt/player_core.py` | Cast loop, transizioni cursor, autoplay. Estratto dalla TUI. |
| `crt/api.py` | FastAPI router (library, control, status, media). |
| `tui_client/__init__.py` | Package marker per il client TUI. |
| `tui_client/main.py` | Entry point `crt-tui`. |
| `tui_client/data_provider.py` | HTTP client che parla al daemon. |
| `docker/Dockerfile` | Immagine del daemon. |
| `docker/docker-compose.yml` | Compose stack con volumi e network host. |
| `pyproject.toml` | Project metadata + entry points. |
| `tests/test_library_store.py` | Test per `LibraryStore` (evoluzione di `test_queue_manager.py`). |
| `tests/test_youtube_client.py` | Test con mock `googleapiclient`. |
| `tests/test_sync_engine.py` | Test diff engine. |
| `tests/test_player_core.py` | Test transizioni cursor + autoplay. |
| `tests/test_api.py` | Test FastAPI con `TestClient`. |
| `tests/test_state_v2_migration.py` | Test migrazione v1 → v2. |

### File modificati

| Path | Cambiamento principale |
|---|---|
| `crt/config.py` | Spostato da root. Aggiunte env vars `CRT_YT_*`, `CRT_SYNC_INTERVAL_S`, `CRT_LOG_LEVEL`, `CRT_DAEMON_URL`. |
| `crt/chromecast_mgr.py` | Spostato da root. Logica invariata. |
| `crt/pipeline.py` | Spostato da root. Aggiungo flag di cancellazione asyncio per supportare `stop_and_remove`. |
| `crt/calibration.py` | Spostato da root. |
| `crt/ui.py` (Phase 1-4) → `tui_client/ui.py` (Phase 5) | Phase 1: spostato in `crt/`. Phase 5: portato a HTTP e spostato in `tui_client/`. |
| `requirements.txt` | + `google-api-python-client`, + `google-auth-oauthlib`. |
| `run.sh` | `python main.py` → `python -m crt.daemon`. |
| `pytest.ini` | `pythonpath = .` per supportare imports da `crt/` e `tui_client/`. |
| `tests/conftest.py` | Aggiornati import: `from queue_manager` → `from crt.library_store`. Nuove fixture per HTTP/SyncEngine. |
| Tutti i `tests/test_*.py` esistenti | Aggiornati import. |

### File rimossi

| Path | Motivo |
|---|---|
| `main.py` | Sostituito da `crt/daemon.py` invocato come `python -m crt.daemon`. |
| `media_server.py` | Integrato in `crt/api.py`. |
| `queue_manager.py` | Spostato/rinominato in `crt/library_store.py`. |
| Modulo root `config.py`, `chromecast_mgr.py`, `pipeline.py`, `calibration.py`, `ui.py` | Spostati nel package `crt/`. |
| `state.json` (utente) | Rinominato `state.json.v1.bak` automaticamente al primo avvio v2. |

---

## Phase 1 — Package skeleton & state v2

Refactoring + foundations. A fine fase: sistema funziona ancora come oggi (TUI manuale), ma codice è in `crt/` e `state.json` è in formato v2.

### Task 1.1: Crea package `crt/` e sposta `config.py`

**Files:**
- Create: `crt/__init__.py`, `crt/__main__.py`
- Move: `config.py` → `crt/config.py`
- Modify: `pytest.ini` (aggiungi `pythonpath`)
- Modify: tutti i file che importano `config` o `from config`

- [ ] **Step 1: Crea il package**

```bash
mkdir -p crt
touch crt/__init__.py
git mv config.py crt/config.py
```

- [ ] **Step 2: Aggiorna `pytest.ini`**

```ini
[pytest]
pythonpath = .
markers =
    integration: end-to-end tests requiring a real Chromecast and internet access
```

- [ ] **Step 3: Aggiorna gli import**

Cambia `import config` → `from crt import config` (o `import crt.config as config`) in tutti i file che lo usano:
- `chromecast_mgr.py`, `pipeline.py`, `media_server.py`, `main.py`, `queue_manager.py`, `ui.py`, `calibration.py`
- `tests/conftest.py`, `tests/test_config.py`, `tests/test_pipeline.py`, `tests/test_calibration.py`, `tests/test_state_persistence.py`, `tests/test_queue_manager.py`

```bash
grep -rln "import config" --include="*.py" . | xargs sed -i '' -e 's/^import config as config_module$/import crt.config as config_module/' -e 's/^import config$/from crt import config/'
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: tutti pass (50 test).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 1.1: sposta config in package crt/"
```

### Task 1.2: Sposta `chromecast_mgr.py`, `calibration.py`, `media_server.py`, `pipeline.py`, `queue_manager.py`, `ui.py`

**Files:**
- Move: `chromecast_mgr.py` → `crt/chromecast_mgr.py`
- Move: `calibration.py` → `crt/calibration.py`
- Move: `media_server.py` → `crt/media_server.py`
- Move: `pipeline.py` → `crt/pipeline.py`
- Move: `queue_manager.py` → `crt/queue_manager.py` (rinominazione a `library_store.py` in task 1.4)
- Move: `ui.py` → `crt/ui.py`

- [ ] **Step 1: Sposta i moduli**

```bash
git mv chromecast_mgr.py crt/chromecast_mgr.py
git mv calibration.py crt/calibration.py
git mv media_server.py crt/media_server.py
git mv pipeline.py crt/pipeline.py
git mv queue_manager.py crt/queue_manager.py
git mv ui.py crt/ui.py
```

- [ ] **Step 2: Aggiorna gli import**

In `crt/*.py` cambia `from chromecast_mgr import ...` → `from crt.chromecast_mgr import ...` e analoghi. In `tests/*.py` aggiorna allo stesso modo (es. `from queue_manager import` → `from crt.queue_manager import`).

```bash
for module in chromecast_mgr calibration media_server pipeline queue_manager ui; do
    grep -rln "from $module import\|^import $module$" --include="*.py" . | \
        xargs sed -i '' -e "s/from $module import/from crt.$module import/g" -e "s/^import $module$/from crt import $module/g"
done
```

- [ ] **Step 3: Aggiorna `main.py`**

I suoi import diventano:

```python
from crt import config
from crt.chromecast_mgr import ChromecastManager
from crt.media_server import create_media_app
from crt.pipeline import PipelineWorker
from crt.queue_manager import QueueManager
from crt.ui import CRTCastApp
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: tutti pass.

- [ ] **Step 5: Run app smoke test**

```bash
./run.sh   # apri TUI, verifica che si avvia, premi Ctrl+C
```

Expected: la TUI si apre come prima, niente errori di import.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Phase 1.2: sposta tutti i moduli nel package crt/"
```

### Task 1.3: Crea `pyproject.toml` con entry points

**Files:**
- Create: `pyproject.toml`

- [ ] **Step 1: Scrivi pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "crt-player"
version = "0.1.0"
description = "Headless YouTube → Chromecast bridge with Flipper-controllable playback"
requires-python = ">=3.12"
dependencies = [
    "textual",
    "fastapi",
    "uvicorn",
    "yt-dlp",
    "pychromecast",
    "httpx",
    "google-api-python-client",
    "google-auth-oauthlib",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "pytest-timeout"]

[project.scripts]
crt-daemon = "crt.daemon:main"
crt-bootstrap = "crt.bootstrap:main"
crt-tui = "tui_client.main:main"

[tool.setuptools.packages.find]
include = ["crt*", "tui_client*"]
exclude = ["tests*", "docker*"]
```

- [ ] **Step 2: Installa in modalità editabile**

```bash
.venv/bin/pip install -e .
```

Expected: install ok. Gli script `crt-daemon`, `crt-bootstrap`, `crt-tui` non funzioneranno ancora finché non scriviamo gli entry point (task 1.7), ma il package è importabile.

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: tutti pass.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "Phase 1.3: aggiungi pyproject.toml con entry points"
```

### Task 1.4: Rinomina `queue_manager.py` in `library_store.py` con `LibraryStore`

**Files:**
- Move: `crt/queue_manager.py` → `crt/library_store.py`
- Modify: rename class `QueueManager` → `LibraryStore` (mantieni un alias di backward compat per ora)
- Modify: `tests/test_queue_manager.py` → `tests/test_library_store.py`
- Modify: tutti gli import

- [ ] **Step 1: Sposta il file e rinomina la classe**

```bash
git mv crt/queue_manager.py crt/library_store.py
git mv tests/test_queue_manager.py tests/test_library_store.py
```

In `crt/library_store.py`, rinomina la classe:

```python
class LibraryStore:
    # (corpo invariato per ora)
    ...

# Backward compat: alias per file ancora non aggiornati
QueueManager = LibraryStore
```

- [ ] **Step 2: Aggiorna gli import nel codice**

```bash
grep -rln "from crt.queue_manager\|from crt import queue_manager" --include="*.py" . | \
    xargs sed -i '' -e 's/from crt.queue_manager import/from crt.library_store import/g'
grep -rln "QueueManager" --include="*.py" . | \
    xargs sed -i '' -e 's/QueueManager/LibraryStore/g'
```

(Il `replace_all` toccherà anche `tests/test_library_store.py` e tutto il codice — funziona perché la dataclass `QueueItem` non si chiama `QueueManager`.)

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: tutti pass.

- [ ] **Step 4: Smoke test app**

```bash
./run.sh
```

Expected: TUI si apre.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 1.4: rinomina QueueManager in LibraryStore"
```

### Task 1.5: Aggiungi `video_id` a `QueueItem`

**Files:**
- Modify: `crt/library_store.py` (dataclass `QueueItem`)
- Modify: `tests/test_library_store.py` (nuovo test)

- [ ] **Step 1: Aggiungi test fail-first**

In `tests/test_library_store.py`, aggiungi:

```python
def test_queue_item_has_video_id():
    item = QueueItem(video_id="dQw4w9WgXcQ", url="https://youtube.com/watch?v=dQw4w9WgXcQ")
    assert item.video_id == "dQw4w9WgXcQ"


def test_queue_item_to_dict_includes_video_id():
    item = QueueItem(video_id="dQw4w9WgXcQ", url="https://youtube.com/watch?v=dQw4w9WgXcQ")
    d = item.to_dict()
    assert d["video_id"] == "dQw4w9WgXcQ"


def test_queue_item_from_dict_restores_video_id():
    item = QueueItem.from_dict({
        "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "video_id": "dQw4w9WgXcQ",
        "id": "550e8400-e29b-41d4-a716-446655440000",
    })
    assert item.video_id == "dQw4w9WgXcQ"
```

- [ ] **Step 2: Run test, verifica fallimento**

```bash
python -m pytest tests/test_library_store.py::test_queue_item_has_video_id -v
```

Expected: FAIL — `QueueItem.__init__() got an unexpected keyword argument 'video_id'`.

- [ ] **Step 3: Implementa**

In `crt/library_store.py`:

```python
@dataclass
class QueueItem:
    url: str
    video_id: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    status: str = "queued"
    progress: float = 0.0
    error: str | None = None
    filename: str | None = None
    playback_position: float = 0.0
    downloaded_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "video_id": self.video_id,
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "progress": self.progress,
            "error": self.error,
            "filename": self.filename,
            "playback_position": self.playback_position,
            "downloaded_path": self.downloaded_path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> QueueItem:
        return cls(
            url=d["url"],
            video_id=d.get("video_id", ""),
            id=d.get("id", str(uuid.uuid4())),
            title=d.get("title", ""),
            status=d.get("status", "queued"),
            progress=d.get("progress", 0.0),
            error=d.get("error"),
            filename=d.get("filename"),
            playback_position=d.get("playback_position", 0.0),
            downloaded_path=d.get("downloaded_path"),
        )
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: tutti pass (compreso il backward compat: `video_id` ha default `""`, gli test esistenti che creano `QueueItem(url=...)` continuano a funzionare).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 1.5: aggiungi video_id a QueueItem"
```

### Task 1.6: Aggiungi `cursor_video_id` e `loop_mode` a `LibraryStore`

**Files:**
- Modify: `crt/library_store.py`
- Modify: `tests/test_library_store.py`

- [ ] **Step 1: Test fail-first**

```python
def test_library_store_has_cursor_video_id():
    ls = LibraryStore()
    assert ls.cursor_video_id is None


def test_library_store_has_loop_mode():
    ls = LibraryStore()
    assert ls.loop_mode is False


def test_library_store_set_cursor():
    ls = LibraryStore()
    ls.cursor_video_id = "abc"
    assert ls.cursor_video_id == "abc"
```

- [ ] **Step 2: Run, verify fail**

```bash
python -m pytest tests/test_library_store.py::test_library_store_has_cursor_video_id -v
```

Expected: FAIL.

- [ ] **Step 3: Implementa**

In `crt/library_store.py`, modifica `LibraryStore.__init__`:

```python
class LibraryStore:
    def __init__(self) -> None:
        self.items: list[QueueItem] = []
        self.history: list[QueueItem] = []
        self.cursor_video_id: str | None = None
        from crt import config
        self.loop_mode: bool = config.LOOP_MODE_DEFAULT
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_library_store.py -v
```

Expected: pass tutti i test (vecchi + nuovi).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 1.6: aggiungi cursor_video_id e loop_mode a LibraryStore"
```

### Task 1.7: state.json v2: schema + migrazione v1→backup

**Files:**
- Modify: `crt/library_store.py` (`save_state`, `load_state`)
- Create: `tests/test_state_v2_migration.py`

- [ ] **Step 1: Test della migrazione**

In `tests/test_state_v2_migration.py`:

```python
import json
import os
from pathlib import Path

import pytest

from crt.library_store import LibraryStore


def test_save_state_writes_v2(tmp_path: Path):
    ls = LibraryStore()
    ls.cursor_video_id = "abc"
    ls.loop_mode = True
    state_file = tmp_path / "state.json"
    ls.save_state(str(state_file))
    data = json.loads(state_file.read_text())
    assert data["version"] == 2
    assert data["cursor_video_id"] == "abc"
    assert data["loop_mode"] is True


def test_load_state_v2_restores_cursor_and_loop(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "version": 2,
        "cursor_video_id": "xyz",
        "loop_mode": True,
        "items": [],
        "history": [],
    }))
    ls = LibraryStore()
    ls.load_state(str(state_file))
    assert ls.cursor_video_id == "xyz"
    assert ls.loop_mode is True


def test_load_state_v1_backs_up_and_starts_empty(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "version": 1,
        "playback_position": 0.0,
        "items": [{"url": "u", "id": "i", "title": "t", "status": "queued"}],
        "history": [],
    }))
    ls = LibraryStore()
    ls.load_state(str(state_file))
    assert ls.items == []
    assert ls.cursor_video_id is None
    backup = tmp_path / "state.json.v1.bak"
    assert backup.exists()
    backup_data = json.loads(backup.read_text())
    assert backup_data["version"] == 1


def test_load_state_no_version_treated_as_v1(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "items": [{"url": "u", "id": "i", "title": "t", "status": "queued"}],
    }))
    ls = LibraryStore()
    ls.load_state(str(state_file))
    assert ls.items == []
    assert (tmp_path / "state.json.v1.bak").exists()


def test_load_state_missing_file_returns_empty(tmp_path: Path):
    ls = LibraryStore()
    state_file = tmp_path / "nonexistent.json"
    ls.load_state(str(state_file))
    assert ls.items == []
    assert ls.cursor_video_id is None
```

- [ ] **Step 2: Run, verifica fallimento**

```bash
python -m pytest tests/test_state_v2_migration.py -v
```

Expected: FAIL (almeno l'assert su `version == 2` fallisce, oggi è `1`).

- [ ] **Step 3: Implementa save_state v2 e migration**

In `crt/library_store.py`:

```python
def save_state(self, path: str, playback_position: float = 0.0) -> None:
    data = {
        "version": 2,
        "cursor_video_id": self.cursor_video_id,
        "loop_mode": self.loop_mode,
        "items": [item.to_dict() for item in self.items],
        "history": [item.to_dict() for item in self.history],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        log.exception("Failed to save state to %s", path)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def load_state(self, path: str) -> float:
    if not os.path.isfile(path):
        return 0.0
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        log.warning("Corrupt or unreadable state file %s, starting fresh", path)
        return 0.0

    version = data.get("version", 1)
    if version != 2:
        backup = path + ".v1.bak"
        log.warning("Migrating state.json v%s → empty v2; old state saved to %s", version, backup)
        os.replace(path, backup)
        return 0.0

    self.cursor_video_id = data.get("cursor_video_id")
    self.loop_mode = data.get("loop_mode", False)

    for raw in data.get("items", []):
        item = QueueItem.from_dict(raw)
        # Reset transitorial states (come prima)
        if item.status == "downloading":
            item.status = "queued"
            item.progress = 0.0
            item.filename = None
            item.downloaded_path = None
        elif item.status == "encoding":
            from crt import config
            if item.downloaded_path:
                base = os.path.splitext(os.path.basename(item.downloaded_path))[0]
                partial = os.path.join(config.TEMP_DIR, config.cached_encoded_filename(base))
                if os.path.isfile(partial):
                    try:
                        os.unlink(partial)
                    except OSError:
                        pass
                if not os.path.isfile(item.downloaded_path):
                    item.downloaded_path = None
            item.status = "queued"
            item.progress = 0.0
            item.filename = None
        elif item.status == "casting":
            item.status = "queued"
            item.progress = 0.0
            item.filename = None
        elif item.status == "playing":
            from crt import config
            if item.filename and os.path.isfile(os.path.join(config.TEMP_DIR, item.filename)):
                item.status = "ready"
            else:
                item.status = "queued"
                item.filename = None
            item.progress = 0.0
        elif item.status == "done":
            from crt import config
            if item.filename and os.path.isfile(os.path.join(config.TEMP_DIR, item.filename)):
                item.status = "ready"
            item.progress = 0.0
        self.items.append(item)

    for raw in data.get("history", []):
        self.history.append(QueueItem.from_dict(raw))

    log.info(
        "Loaded state v2: %d items, %d history, cursor=%s, loop=%s",
        len(self.items), len(self.history), self.cursor_video_id, self.loop_mode,
    )
    return 0.0  # legacy: il vecchio playback_position è morto, vive in QueueItem.playback_position
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: tutti pass. Test esistenti di state persistence vanno rivisti — se `test_state_persistence.py` salva un v1 e si aspetta di leggerlo, ora la migrazione lo cancella. Aggiusta i test esistenti per scrivere v2 esplicitamente:

```bash
grep -l '"version": 1' tests/*.py
# rimpiazza tutti gli "version": 1 in fixture con "version": 2 e aggiungi cursor_video_id/loop_mode
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 1.7: state.json v2 schema + migrazione v1→backup"
```

### Task 1.8: Crea `crt/daemon.py` come entry point e aggiorna `run.sh`

**Files:**
- Move: `main.py` → `crt/daemon.py`
- Modify: `run.sh`
- Create: `crt/__main__.py`

- [ ] **Step 1: Sposta main.py**

```bash
git mv main.py crt/daemon.py
```

In `crt/daemon.py`, lascia per ora la logica TUI invariata (ci penseremo nelle Phase successive a sostituirla con il vero daemon).

Aggiorna gli import in cima:

```python
from crt import config
from crt.chromecast_mgr import ChromecastManager
from crt.media_server import create_media_app
from crt.pipeline import PipelineWorker
from crt.library_store import LibraryStore
from crt.ui import CRTCastApp
```

E rinomina `QueueManager` in `LibraryStore` nel corpo se serve.

Cambia il path del log file (era `os.path.dirname(__file__)` che sotto `crt/` punta dentro il package; lo vogliamo a livello repo):

```python
LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "crt_cast.log")
```

- [ ] **Step 2: Crea `crt/__main__.py`**

```python
from crt.daemon import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Aggiorna `run.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a
exec .venv/bin/python -m crt.daemon
```

- [ ] **Step 4: Smoke test**

```bash
./run.sh
```

Expected: TUI si apre come prima. Premi `Ctrl+C` per uscire.

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: tutti pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Phase 1.8: sposta main.py in crt/daemon.py + aggiorna run.sh"
```

### Phase 1 checkpoint

A questo punto:
- Codice riorganizzato sotto `crt/` (e cartella stub `tui_client/` ancora vuota).
- `state.json` v2 con `cursor_video_id` e `loop_mode`.
- Migrazione v1 testata.
- `pyproject.toml` con entry points (anche se solo `crt-daemon` è completo).
- TUI esistente funziona ancora come prima — è un cambio di scaffolding senza regressione comportamentale.
- Suite test tutta verde.

---

## Phase 2 — YouTube integration & SyncEngine

A fine fase: il daemon polla YouTube, scarica nuovi video automaticamente, rimuove quelli tolti dalla playlist. La TUI ancora vede i risultati, ma i video arrivano dal sync invece che dall'input manuale dell'utente.

### Task 2.1: Aggiungi dipendenze YouTube

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Aggiungi le dipendenze**

```
textual
fastapi
uvicorn
yt-dlp
pychromecast
pytest
httpx
pytest-asyncio
pytest-timeout
google-api-python-client
google-auth-oauthlib
google-auth-httplib2
```

- [ ] **Step 2: Installa**

```bash
.venv/bin/pip install -r requirements.txt
```

- [ ] **Step 3: Verifica import**

```bash
.venv/bin/python -c "from googleapiclient.discovery import build; from google_auth_oauthlib.flow import InstalledAppFlow; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "Phase 2.1: aggiungi google-api-python-client e google-auth-oauthlib"
```

### Task 2.2: Aggiungi env vars YouTube e log level a `crt/config.py`

**Files:**
- Modify: `crt/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Test fail-first**

In `tests/test_config.py`, aggiungi:

```python
def test_youtube_env_vars_loaded(monkeypatch):
    monkeypatch.setenv("CRT_YT_PLAYLIST_ID", "PLtestxxx")
    monkeypatch.setenv("CRT_YT_CLIENT_SECRETS", "/x/secrets.json")
    monkeypatch.setenv("CRT_YT_TOKEN_FILE", "/x/token.json")
    monkeypatch.setenv("CRT_SYNC_INTERVAL_S", "60")
    monkeypatch.setenv("CRT_LOG_LEVEL", "DEBUG")

    import importlib
    from crt import config
    importlib.reload(config)

    assert config.YT_PLAYLIST_ID == "PLtestxxx"
    assert config.YT_CLIENT_SECRETS == "/x/secrets.json"
    assert config.YT_TOKEN_FILE == "/x/token.json"
    assert config.SYNC_INTERVAL_S == 60
    assert config.LOG_LEVEL == "DEBUG"
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_config.py::test_youtube_env_vars_loaded -v
```

Expected: FAIL.

- [ ] **Step 3: Implementa**

In `crt/config.py`, aggiungi in fondo:

```python
YT_PLAYLIST_ID = os.environ.get("CRT_YT_PLAYLIST_ID", "")
YT_CLIENT_SECRETS = os.environ.get(
    "CRT_YT_CLIENT_SECRETS",
    os.path.join(os.path.expanduser("~"), ".local", "share", "crt-player", "client_secrets.json"),
)
YT_TOKEN_FILE = os.environ.get(
    "CRT_YT_TOKEN_FILE",
    os.path.join(os.path.expanduser("~"), ".local", "share", "crt-player", "oauth_token.json"),
)
SYNC_INTERVAL_S = int(os.environ.get("CRT_SYNC_INTERVAL_S", "300"))
LOG_LEVEL = os.environ.get("CRT_LOG_LEVEL", "INFO")
DAEMON_URL = os.environ.get("CRT_DAEMON_URL", "http://localhost:8765")
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_config.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 2.2: aggiungi env vars YouTube e log level"
```

### Task 2.3: `YouTubeClient` — list_playlist_items con paginazione

**Files:**
- Create: `crt/youtube_client.py`
- Create: `tests/test_youtube_client.py`

- [ ] **Step 1: Test fail-first**

In `tests/test_youtube_client.py`:

```python
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

import pytest

from crt.youtube_client import PlaylistEntry, YouTubeAuthError, YouTubeClient


def _mock_response(items, next_page_token=None):
    return {
        "items": items,
        "nextPageToken": next_page_token,
    } if next_page_token else {"items": items}


def _build_item(video_id, title, position):
    return {
        "snippet": {
            "title": title,
            "position": position,
            "resourceId": {"videoId": video_id},
        },
    }


def test_list_playlist_items_single_page():
    api_mock = MagicMock()
    api_mock.playlistItems.return_value.list.return_value.execute.return_value = _mock_response([
        _build_item("vid1", "Title 1", 0),
        _build_item("vid2", "Title 2", 1),
    ])

    client = YouTubeClient(api_service=api_mock)
    entries = client.list_playlist_items("PLxxx")

    assert entries == [
        PlaylistEntry(video_id="vid1", title="Title 1", position=0),
        PlaylistEntry(video_id="vid2", title="Title 2", position=1),
    ]


def test_list_playlist_items_paginates():
    api_mock = MagicMock()
    list_mock = api_mock.playlistItems.return_value.list

    list_mock.return_value.execute.side_effect = [
        _mock_response([_build_item(f"vid{i}", f"T{i}", i) for i in range(50)], next_page_token="PG2"),
        _mock_response([_build_item("vid50", "T50", 50)]),
    ]

    client = YouTubeClient(api_service=api_mock)
    entries = client.list_playlist_items("PLxxx")

    assert len(entries) == 51
    assert entries[0].video_id == "vid0"
    assert entries[50].video_id == "vid50"
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_youtube_client.py -v
```

Expected: FAIL — modulo non esiste.

- [ ] **Step 3: Implementa**

In `crt/youtube_client.py`:

```python
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


class YouTubeAuthError(Exception):
    """Raised when OAuth token is missing or invalid."""


class YouTubeClient:
    def __init__(self, api_service):
        """api_service is a googleapiclient resource. In production built via build()."""
        self._api = api_service

    def list_playlist_items(self, playlist_id: str) -> list[PlaylistEntry]:
        entries: list[PlaylistEntry] = []
        page_token: str | None = None
        while True:
            request = self._api.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
            resp = request.execute()
            for raw in resp.get("items", []):
                snippet = raw["snippet"]
                entries.append(PlaylistEntry(
                    video_id=snippet["resourceId"]["videoId"],
                    title=snippet["title"],
                    position=snippet["position"],
                ))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return entries


@dataclass(frozen=True)
class PlaylistEntry:
    video_id: str
    title: str
    position: int
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_youtube_client.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 2.3: YouTubeClient.list_playlist_items con paginazione"
```

### Task 2.4: `YouTubeClient` — error handling e factory `from_token_file`

**Files:**
- Modify: `crt/youtube_client.py`
- Modify: `tests/test_youtube_client.py`

- [ ] **Step 1: Test fail-first per error handling**

In `tests/test_youtube_client.py`:

```python
from googleapiclient.errors import HttpError


def test_list_playlist_items_auth_error_raises_typed():
    api_mock = MagicMock()
    err = HttpError(
        resp=MagicMock(status=401, reason="Unauthorized"),
        content=b'{"error": {"message": "Invalid Credentials"}}',
    )
    api_mock.playlistItems.return_value.list.return_value.execute.side_effect = err

    client = YouTubeClient(api_service=api_mock)
    with pytest.raises(YouTubeAuthError):
        client.list_playlist_items("PLxxx")


def test_from_token_file_missing_raises():
    with pytest.raises(YouTubeAuthError):
        YouTubeClient.from_token_file("/nonexistent/path.json", "/nonexistent/secrets.json")
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_youtube_client.py::test_list_playlist_items_auth_error_raises_typed tests/test_youtube_client.py::test_from_token_file_missing_raises -v
```

Expected: FAIL.

- [ ] **Step 3: Implementa**

In `crt/youtube_client.py`, aggiungi:

```python
import os
import json
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials


SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


class YouTubeClient:
    # ... esistente ...

    def list_playlist_items(self, playlist_id: str) -> list[PlaylistEntry]:
        try:
            return self._list_playlist_items_inner(playlist_id)
        except HttpError as e:
            if getattr(e.resp, "status", None) in (401, 403):
                raise YouTubeAuthError(f"YouTube auth error: {e}") from e
            raise

    def _list_playlist_items_inner(self, playlist_id: str) -> list[PlaylistEntry]:
        # (corpo precedente di list_playlist_items)
        ...

    @classmethod
    def from_token_file(cls, token_file: str, client_secrets_file: str) -> "YouTubeClient":
        if not os.path.isfile(token_file):
            raise YouTubeAuthError(
                f"OAuth token file missing: {token_file}. Run `crt-bootstrap` first."
            )
        with open(token_file) as f:
            token_data = json.load(f)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        api = build("youtube", "v3", credentials=creds, cache_discovery=False)
        return cls(api_service=api)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_youtube_client.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 2.4: YouTubeClient error handling + from_token_file"
```

### Task 2.5: `crt/bootstrap.py` — flow OAuth interattivo (copy-paste URL)

**Files:**
- Create: `crt/bootstrap.py`
- Create: `tests/test_bootstrap.py`

- [ ] **Step 1: Test fail-first**

In `tests/test_bootstrap.py`:

```python
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from crt import bootstrap


def test_extract_code_from_callback_url():
    url = "http://localhost/?code=4/0AX_xyz&scope=youtube.readonly"
    code = bootstrap.extract_code_from_url(url)
    assert code == "4/0AX_xyz"


def test_extract_code_from_url_missing_code_raises():
    with pytest.raises(ValueError, match="missing 'code'"):
        bootstrap.extract_code_from_url("http://localhost/?error=access_denied")
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_bootstrap.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implementa il modulo**

In `crt/bootstrap.py`:

```python
"""Interactive OAuth bootstrap subcommand.

Usage:
    docker compose run --rm crt-daemon crt-bootstrap

Logs the consent URL, waits for the user to paste back the redirect URL,
extracts the code, exchanges it for tokens, writes the token file.
"""
from __future__ import annotations

import json
import logging
import sys
from urllib.parse import parse_qs, urlparse

from google_auth_oauthlib.flow import Flow

from crt import config

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
REDIRECT_URI = "http://localhost/"


def extract_code_from_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "code" not in qs:
        raise ValueError("URL missing 'code' parameter; check that you copied the FULL URL from your browser after consent.")
    return qs["code"][0]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    flow = Flow.from_client_secrets_file(
        config.YT_CLIENT_SECRETS,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")

    print("Open this URL in your browser, sign in, and consent:")
    print()
    print(auth_url)
    print()
    print("After consenting, your browser will redirect to a URL like:")
    print("    http://localhost/?code=4/0AX_...&scope=...")
    print("It will show 'connection refused' (no listener on localhost). That's expected.")
    print("Copy the FULL URL from the browser address bar and paste it below.")
    print()

    callback_url = input("Paste the URL: ").strip()
    code = extract_code_from_url(callback_url)

    flow.fetch_token(code=code)
    creds = flow.credentials

    import os
    os.makedirs(os.path.dirname(config.YT_TOKEN_FILE), exist_ok=True)
    with open(config.YT_TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"Token saved to {config.YT_TOKEN_FILE}")
    print("Bootstrap complete. You can now run `crt-daemon`.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_bootstrap.py -v
```

Expected: pass.

- [ ] **Step 5: Manual smoke test (opzionale, richiede `client_secrets.json`)**

Se l'utente ha già un `client_secrets.json` di test:

```bash
.venv/bin/python -m crt.bootstrap
# Apre URL, l'utente esegue il flow manualmente
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Phase 2.5: crt-bootstrap subcommand per OAuth"
```

### Task 2.6: `SyncEngine` — diff add/remove (TDD)

**Files:**
- Create: `crt/sync_engine.py`
- Create: `tests/test_sync_engine.py`

- [ ] **Step 1: Test fail-first**

In `tests/test_sync_engine.py`:

```python
from unittest.mock import MagicMock

import pytest

from crt.library_store import LibraryStore, QueueItem
from crt.sync_engine import SyncEngine
from crt.youtube_client import PlaylistEntry


def _entry(video_id, title="T", position=0):
    return PlaylistEntry(video_id=video_id, title=title, position=position)


def test_apply_diff_adds_new_items():
    library = LibraryStore()
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = [
        _entry("vid1", "Title 1", 0),
        _entry("vid2", "Title 2", 1),
    ]
    engine = SyncEngine(library, yt_client, playlist_id="PLxxx")

    engine.run_sync_once()

    assert [i.video_id for i in library.items] == ["vid1", "vid2"]
    assert library.items[0].status == "queued"
    assert library.items[0].title == "Title 1"


def test_apply_diff_removes_items_not_in_playlist():
    library = LibraryStore()
    library.items.append(QueueItem(url="u", video_id="vid_old", title="old"))
    library.items.append(QueueItem(url="u", video_id="vid1", title="kept"))
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = [_entry("vid1", "kept", 0)]
    engine = SyncEngine(library, yt_client, playlist_id="PLxxx")

    engine.run_sync_once()

    assert [i.video_id for i in library.items] == ["vid1"]


def test_apply_diff_reorders_existing_items_to_match_playlist():
    library = LibraryStore()
    library.items.append(QueueItem(url="u", video_id="A", title="A"))
    library.items.append(QueueItem(url="u", video_id="B", title="B"))
    library.items.append(QueueItem(url="u", video_id="C", title="C"))
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = [
        _entry("C", "C", 0),
        _entry("A", "A", 1),
        _entry("B", "B", 2),
    ]
    engine = SyncEngine(library, yt_client, playlist_id="PLxxx")

    engine.run_sync_once()

    assert [i.video_id for i in library.items] == ["C", "A", "B"]


def test_apply_diff_idempotent():
    library = LibraryStore()
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = [_entry("vid1", "T", 0)]
    engine = SyncEngine(library, yt_client, playlist_id="PLxxx")

    engine.run_sync_once()
    snapshot1 = [(i.video_id, i.status) for i in library.items]
    engine.run_sync_once()
    snapshot2 = [(i.video_id, i.status) for i in library.items]

    assert snapshot1 == snapshot2
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_sync_engine.py -v
```

Expected: FAIL — modulo non esiste.

- [ ] **Step 3: Implementa**

In `crt/sync_engine.py`:

```python
from __future__ import annotations

import logging

from crt.library_store import LibraryStore, QueueItem
from crt.youtube_client import PlaylistEntry, YouTubeAuthError, YouTubeClient

log = logging.getLogger(__name__)


class SyncEngine:
    def __init__(
        self,
        library: LibraryStore,
        yt_client: YouTubeClient,
        playlist_id: str,
        on_remove=None,
    ):
        """on_remove: optional callback(video_id) invoked when an item is being removed.
        Lets PlayerCore stop playback and PipelineWorker cancel in-flight work."""
        self.library = library
        self.yt = yt_client
        self.playlist_id = playlist_id
        self._on_remove = on_remove
        self.last_sync_at: str | None = None
        self.last_error: str | None = None
        self.state: str = "ok"

    def run_sync_once(self) -> None:
        """Fetch playlist snapshot and apply diff to library."""
        try:
            snapshot = self.yt.list_playlist_items(self.playlist_id)
        except YouTubeAuthError as e:
            self.state = "degraded"
            self.last_error = str(e)
            log.error("Sync failed (auth): %s", e)
            return
        except Exception as e:
            self.state = "degraded"
            self.last_error = str(e)
            log.exception("Sync failed: %s", e)
            return

        self._apply_diff(snapshot)
        self.state = "ok"
        self.last_error = None
        from datetime import datetime, timezone
        self.last_sync_at = datetime.now(timezone.utc).isoformat()

    def _apply_diff(self, snapshot: list[PlaylistEntry]) -> None:
        snapshot_ids = [e.video_id for e in snapshot]
        snapshot_set = set(snapshot_ids)
        current = {item.video_id: item for item in self.library.items}
        current_ids = set(current.keys())

        removed_ids = current_ids - snapshot_set
        for video_id in removed_ids:
            self._remove_item(video_id)

        for entry in snapshot:
            if entry.video_id not in current:
                self._add_item(entry)

        # Reorder by snapshot order
        items_by_id = {item.video_id: item for item in self.library.items}
        self.library.items = [items_by_id[vid] for vid in snapshot_ids if vid in items_by_id]

    def _add_item(self, entry: PlaylistEntry) -> None:
        url = f"https://www.youtube.com/watch?v={entry.video_id}"
        item = QueueItem(url=url, video_id=entry.video_id, title=entry.title)
        self.library.items.append(item)
        log.info("Added: %s (%s)", entry.title, entry.video_id)

    def _remove_item(self, video_id: str) -> None:
        if self._on_remove is not None:
            self._on_remove(video_id)
        self.library.items = [i for i in self.library.items if i.video_id != video_id]
        if self.library.cursor_video_id == video_id:
            self.library.cursor_video_id = None
        log.info("Removed: %s", video_id)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_sync_engine.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 2.6: SyncEngine diff add/remove/reorder"
```

### Task 2.7: `SyncEngine` — cleanup file su remove + invocazione `on_remove`

**Files:**
- Modify: `crt/sync_engine.py`
- Modify: `tests/test_sync_engine.py`

- [ ] **Step 1: Test fail-first**

```python
def test_remove_invokes_on_remove_callback():
    library = LibraryStore()
    library.items.append(QueueItem(url="u", video_id="vid_old"))
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = []
    on_remove = MagicMock()
    engine = SyncEngine(library, yt_client, playlist_id="PL", on_remove=on_remove)

    engine.run_sync_once()

    on_remove.assert_called_once_with("vid_old")


def test_remove_clears_cursor_if_was_pointing_at_removed():
    library = LibraryStore()
    library.items.append(QueueItem(url="u", video_id="vid_old"))
    library.cursor_video_id = "vid_old"
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = []
    engine = SyncEngine(library, yt_client, playlist_id="PL")

    engine.run_sync_once()

    assert library.cursor_video_id is None


def test_remove_deletes_cache_files(tmp_path, monkeypatch):
    from crt import config
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path))

    encoded_path = tmp_path / "vid_old_pal_crop.mp4"
    encoded_path.write_text("fake mp4")
    download_path = tmp_path / "vid_old.mp4"
    download_path.write_text("fake source")

    library = LibraryStore()
    library.items.append(QueueItem(
        url="u", video_id="vid_old",
        filename="vid_old_pal_crop.mp4",
        downloaded_path=str(download_path),
    ))
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = []
    engine = SyncEngine(library, yt_client, playlist_id="PL")

    engine.run_sync_once()

    assert not encoded_path.exists()
    assert not download_path.exists()
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_sync_engine.py::test_remove_deletes_cache_files -v
```

Expected: FAIL.

- [ ] **Step 3: Implementa cleanup**

In `crt/sync_engine.py`, modifica `_remove_item`:

```python
def _remove_item(self, video_id: str) -> None:
    item = next((i for i in self.library.items if i.video_id == video_id), None)
    if item is None:
        return

    if self._on_remove is not None:
        try:
            self._on_remove(video_id)
        except Exception:
            log.exception("on_remove callback failed for %s", video_id)

    self._delete_cache_files(item)

    self.library.items = [i for i in self.library.items if i.video_id != video_id]
    if self.library.cursor_video_id == video_id:
        self.library.cursor_video_id = None
    log.info("Removed: %s", video_id)


def _delete_cache_files(self, item: QueueItem) -> None:
    import os
    from crt import config
    if item.filename:
        encoded = os.path.join(config.TEMP_DIR, item.filename)
        if os.path.isfile(encoded):
            try:
                os.unlink(encoded)
                log.debug("Deleted encoded: %s", encoded)
            except OSError as e:
                log.warning("Failed to delete encoded %s: %s", encoded, e)
    if item.downloaded_path and os.path.isfile(item.downloaded_path):
        try:
            os.unlink(item.downloaded_path)
            log.debug("Deleted download: %s", item.downloaded_path)
        except OSError as e:
            log.warning("Failed to delete download %s: %s", item.downloaded_path, e)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_sync_engine.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 2.7: SyncEngine cleanup file e on_remove callback"
```

### Task 2.8: `SyncEngine` — async polling loop con backoff

**Files:**
- Modify: `crt/sync_engine.py`
- Modify: `tests/test_sync_engine.py`

- [ ] **Step 1: Test fail-first**

```python
import asyncio


@pytest.mark.asyncio
async def test_poll_loop_runs_sync_at_interval(monkeypatch):
    library = LibraryStore()
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = []
    engine = SyncEngine(library, yt_client, playlist_id="PL")

    sync_calls = []
    orig_sync = engine.run_sync_once
    def counting_sync():
        sync_calls.append(1)
        orig_sync()
    engine.run_sync_once = counting_sync

    task = asyncio.create_task(engine.run_loop(interval_s=0.05, initial_delay_s=0))
    await asyncio.sleep(0.18)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(sync_calls) >= 3


@pytest.mark.asyncio
async def test_poll_loop_backs_off_on_error():
    library = LibraryStore()
    yt_client = MagicMock()
    yt_client.list_playlist_items.side_effect = RuntimeError("transient")
    engine = SyncEngine(library, yt_client, playlist_id="PL")

    task = asyncio.create_task(engine.run_loop(interval_s=0.05, initial_delay_s=0))
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert engine.state == "degraded"
    assert "transient" in engine.last_error
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_sync_engine.py::test_poll_loop_runs_sync_at_interval -v
```

Expected: FAIL — `run_loop` non esiste.

- [ ] **Step 3: Implementa loop async**

In `crt/sync_engine.py`:

```python
import asyncio


class SyncEngine:
    # ... esistente ...

    def __init__(self, ...):
        # ... esistente ...
        self._kick = asyncio.Event()  # creato lazy in run_loop
        self._kick = None

    async def run_loop(self, interval_s: int = 300, initial_delay_s: int = 10) -> None:
        """Periodic sync. Cancellable via task.cancel()."""
        self._kick = asyncio.Event()
        await asyncio.sleep(initial_delay_s)
        backoff = 0
        while True:
            await asyncio.to_thread(self.run_sync_once)
            if self.state == "degraded":
                backoff = min((backoff or 30) * 2, 1800)  # 30s → 60s → ... → 30m
                wait = backoff
            else:
                backoff = 0
                wait = interval_s
            try:
                await asyncio.wait_for(self._kick.wait(), timeout=wait)
                self._kick.clear()
            except asyncio.TimeoutError:
                pass

    def kick(self) -> None:
        """Force the next iteration to run immediately."""
        if self._kick is not None:
            self._kick.set()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_sync_engine.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 2.8: SyncEngine async polling loop con backoff"
```

### Task 2.9: Integra SyncEngine nel daemon (TUI flow esistente)

**Files:**
- Modify: `crt/daemon.py`
- Modify: `crt/library_store.py` (aggiungi `add_item_from_sync` opzionale, o usa direttamente l'API)

- [ ] **Step 1: Avvia il SyncEngine come task**

In `crt/daemon.py`, prima di `app.run()`:

```python
import asyncio
from crt.youtube_client import YouTubeClient
from crt.sync_engine import SyncEngine

# ...
def main() -> None:
    # ... codice esistente per cleanup, server, queue, chromecast, pipeline ...

    # SyncEngine setup (best-effort: se OAuth manca, logga e continua senza sync)
    sync_engine = None
    if config.YT_PLAYLIST_ID:
        try:
            yt_client = YouTubeClient.from_token_file(config.YT_TOKEN_FILE, config.YT_CLIENT_SECRETS)
            sync_engine = SyncEngine(queue, yt_client, config.YT_PLAYLIST_ID)
            log.info("SyncEngine ready (playlist=%s, interval=%ds)", config.YT_PLAYLIST_ID, config.SYNC_INTERVAL_S)
        except Exception as e:
            log.warning("SyncEngine disabled: %s", e)

    # ... existing CRTCastApp setup ...

    # Start sync loop alongside the TUI (Textual asyncio loop hosts it)
    if sync_engine is not None:
        async def _sync_task():
            await sync_engine.run_loop(interval_s=config.SYNC_INTERVAL_S)
        # We need to start this from inside Textual's loop. Easiest: pass to CRTCastApp.
        app._sync_engine = sync_engine
        app._sync_interval = config.SYNC_INTERVAL_S

    app.run()
```

E in `crt/ui.py`, in `on_mount`:

```python
async def on_mount(self) -> None:
    # ... esistente ...
    if hasattr(self, "_sync_engine") and self._sync_engine is not None:
        self._sync_task = asyncio.create_task(
            self._sync_engine.run_loop(interval_s=self._sync_interval)
        )
```

- [ ] **Step 2: Smoke test (richiede `client_secrets.json` + `oauth_token.json` + playlist test)**

```bash
export CRT_YT_PLAYLIST_ID=PL_test
./run.sh
# La TUI parte; controlla in `crt_cast.log`: "SyncEngine ready"
# Aggiungi un video alla playlist YouTube; entro 5 min compare nella TUI con status queued.
```

- [ ] **Step 3: Run unit tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: tutti pass (i test esistenti non sono toccati dal sync, è additivo).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Phase 2.9: integra SyncEngine nel flusso daemon (TUI compatibile)"
```

### Phase 2 checkpoint

A questo punto:
- Il daemon polla YouTube ogni 5 min e popola la library con i video della playlist.
- Le rimozioni dalla playlist svuotano la library e cancellano i file.
- Le modifiche di ordine in YT si riflettono nella library al prossimo sync.
- Errori OAuth/rete sono visibili nel log e non bloccano la TUI.
- La TUI esistente è ancora la UI principale, ma ora i video arrivano automaticamente, non manualmente.
- L'utente può ancora aggiungere URL manualmente dalla TUI; non sono distinguibili dai sync (li aggiungiamo come "queued" come fa il sync).

---

## Phase 3 — PlayerCore extraction

A fine fase: la logica di playback è in un modulo dedicato `PlayerCore` con cursor esplicito, separata dalla TUI. La TUI ancora la consuma direttamente in-process (non via HTTP — quello è Phase 4).

### Task 3.1: Crea `crt/player_core.py` con cursor esplicito (TDD)

**Files:**
- Create: `crt/player_core.py`
- Create: `tests/test_player_core.py`

- [ ] **Step 1: Test fail-first**

In `tests/test_player_core.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from crt.library_store import LibraryStore, QueueItem
from crt.player_core import PlayerCore


def _make_library(video_ids):
    ls = LibraryStore()
    for vid in video_ids:
        ls.items.append(QueueItem(url=f"u/{vid}", video_id=vid, title=vid, status="ready", filename=f"{vid}.mp4"))
    return ls


def _make_chromecast():
    cc = MagicMock()
    cc.connected = True
    cc.cast_url = MagicMock()
    cc.stop = MagicMock()
    cc.pause_or_resume = MagicMock()
    cc.player_state = "IDLE"
    cc.wait_for_connection = AsyncMock()
    return cc


@pytest.mark.asyncio
async def test_next_with_no_cursor_advances_to_first_item():
    library = _make_library(["A", "B", "C"])
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.next()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_next_advances_cursor_by_one():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.next()

    assert library.cursor_video_id == "B"


@pytest.mark.asyncio
async def test_prev_moves_cursor_back():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "B"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.prev()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_prev_at_first_item_is_no_op():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.prev()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_next_at_end_no_loop_stops_at_last():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = False
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.next()

    assert library.cursor_video_id == "B"  # no advance


@pytest.mark.asyncio
async def test_next_at_end_with_loop_wraps_to_first():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = True
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.next()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_play_specific_video_id():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.play("C")

    assert library.cursor_video_id == "C"


@pytest.mark.asyncio
async def test_play_unknown_video_id_raises():
    library = _make_library(["A"])
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    with pytest.raises(KeyError):
        await pc.play("NOPE")
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_player_core.py -v
```

Expected: FAIL — modulo non esiste.

- [ ] **Step 3: Implementa skeleton di `PlayerCore`**

In `crt/player_core.py`:

```python
from __future__ import annotations

import asyncio
import logging

from crt.chromecast_mgr import ChromecastManager
from crt.library_store import LibraryStore, QueueItem

log = logging.getLogger(__name__)


class PlayerCore:
    def __init__(self, library: LibraryStore, chromecast: ChromecastManager):
        self.library = library
        self.chromecast = chromecast
        self.state: str = "idle"  # idle | casting | playing | paused

    def _index_of(self, video_id: str) -> int | None:
        for i, item in enumerate(self.library.items):
            if item.video_id == video_id:
                return i
        return None

    def _cursor_index(self) -> int | None:
        if self.library.cursor_video_id is None:
            return None
        return self._index_of(self.library.cursor_video_id)

    async def next(self) -> None:
        if not self.library.items:
            return
        idx = self._cursor_index()
        if idx is None:
            new_idx = 0
        elif idx + 1 < len(self.library.items):
            new_idx = idx + 1
        elif self.library.loop_mode:
            new_idx = 0
        else:
            return  # no advance, end of list
        self.library.cursor_video_id = self.library.items[new_idx].video_id
        await self._cast_current()

    async def prev(self) -> None:
        if not self.library.items:
            return
        idx = self._cursor_index()
        if idx is None or idx == 0:
            return
        self.library.cursor_video_id = self.library.items[idx - 1].video_id
        await self._cast_current()

    async def play(self, video_id: str) -> None:
        idx = self._index_of(video_id)
        if idx is None:
            raise KeyError(f"video_id not in library: {video_id}")
        self.library.cursor_video_id = video_id
        await self._cast_current()

    async def _cast_current(self) -> None:
        idx = self._cursor_index()
        if idx is None:
            return
        item = self.library.items[idx]
        if item.status != "ready":
            log.info("Cursor item %s not ready (status=%s); waiting", item.video_id, item.status)
            return
        # Implementazione full di cast in task 3.4; stub per ora:
        log.info("Cast current: %s", item.video_id)
        self.state = "casting"
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_player_core.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 3.1: PlayerCore con cursor esplicito (next/prev/play)"
```

### Task 3.2: `PlayerCore.toggle()` e `stop()` (TDD)

**Files:**
- Modify: `crt/player_core.py`
- Modify: `tests/test_player_core.py`

- [ ] **Step 1: Test fail-first**

```python
@pytest.mark.asyncio
async def test_stop_calls_chromecast_stop():
    library = _make_library(["A"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.stop()

    cc.stop.assert_called_once()
    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_toggle_when_playing_pauses():
    library = _make_library(["A"])
    cc = _make_chromecast()
    cc.player_state = "PLAYING"
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.toggle()

    cc.pause_or_resume.assert_called_once()


@pytest.mark.asyncio
async def test_toggle_when_idle_with_no_cursor_starts_first_item():
    library = _make_library(["A", "B"])
    library.cursor_video_id = None
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.toggle()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_toggle_when_idle_with_cursor_starts_cursor_item():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "idle"

    await pc.toggle()

    assert library.cursor_video_id == "B"  # invariato
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_player_core.py::test_stop_calls_chromecast_stop -v
```

Expected: FAIL.

- [ ] **Step 3: Implementa**

In `crt/player_core.py`:

```python
async def stop(self) -> None:
    await asyncio.to_thread(self.chromecast.stop)
    self.state = "idle"

async def toggle(self) -> None:
    if self.state in ("playing", "casting"):
        # Pause
        await asyncio.to_thread(self.chromecast.pause_or_resume)
        self.state = "paused"
        return
    if self.state == "paused":
        # Resume
        await asyncio.to_thread(self.chromecast.pause_or_resume)
        self.state = "playing"
        return
    # idle → start cursor item (or first if no cursor)
    if self.library.cursor_video_id is None:
        if not self.library.items:
            return
        self.library.cursor_video_id = self.library.items[0].video_id
    await self._cast_current()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_player_core.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 3.2: PlayerCore toggle e stop"
```

### Task 3.3: `PlayerCore.stop_and_remove()` per integrazione SyncEngine

**Files:**
- Modify: `crt/player_core.py`
- Modify: `tests/test_player_core.py`

- [ ] **Step 1: Test fail-first**

```python
@pytest.mark.asyncio
async def test_stop_and_remove_stops_if_video_id_is_current():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.stop_and_remove("A")

    cc.stop.assert_called_once()
    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_stop_and_remove_no_op_if_video_id_is_not_current():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.stop_and_remove("B")

    cc.stop.assert_not_called()
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_player_core.py::test_stop_and_remove_stops_if_video_id_is_current -v
```

Expected: FAIL.

- [ ] **Step 3: Implementa**

In `crt/player_core.py`:

```python
async def stop_and_remove(self, video_id: str) -> None:
    """Called by SyncEngine when an item is removed from YT.
    Stops playback if the removed item is current; lets the caller proceed with library cleanup."""
    if self.library.cursor_video_id == video_id and self.state in ("playing", "casting", "paused"):
        await asyncio.to_thread(self.chromecast.stop)
        self.state = "idle"
```

E in `crt/daemon.py`, quando si crea il `SyncEngine`, passa `on_remove`:

```python
def _on_yt_remove(video_id: str):
    if player_core is not None:
        # Synchronous call from SyncEngine (which runs in to_thread); schedule on the loop
        loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(player_core.stop_and_remove(video_id), loop)

sync_engine = SyncEngine(queue, yt_client, config.YT_PLAYLIST_ID, on_remove=_on_yt_remove)
```

(player_core sarà creato in task successivo; per ora, in daemon.py, può essere `None` e `_on_yt_remove` skippa.)

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_player_core.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 3.3: PlayerCore stop_and_remove per SyncEngine"
```

### Task 3.4: Implementa cast vero in `_cast_current` (porting da TUI/pipeline)

**Files:**
- Modify: `crt/player_core.py`
- Modify: `tests/test_player_core.py`

- [ ] **Step 1: Test (verifica side effect cast_url)**

```python
@pytest.mark.asyncio
async def test_cast_current_calls_chromecast_cast_url(monkeypatch):
    from crt import config
    monkeypatch.setattr(config, "TEMP_DIR", "/tmp")
    monkeypatch.setattr(config, "SERVER_PORT", 8765)

    library = _make_library(["A"])
    library.cursor_video_id = "A"
    library.items[0].status = "ready"
    library.items[0].filename = "A.mp4"
    library.items[0].playback_position = 42.0

    cc = _make_chromecast()
    cc.connected = True
    pc = PlayerCore(library, cc)

    await pc._cast_current()

    cc.wait_for_connection.assert_awaited()
    args, kwargs = cc.cast_url.call_args
    assert "A.mp4" in args[0]
    assert kwargs.get("title") == "A"
    assert kwargs.get("current_time") == 42.0
```

- [ ] **Step 2: Run**

```bash
python -m pytest tests/test_player_core.py::test_cast_current_calls_chromecast_cast_url -v
```

Expected: probabilmente FAIL (lo stub corrente non chiama cast_url).

- [ ] **Step 3: Implementa il cast vero**

In `crt/player_core.py`, sostituisci `_cast_current`:

```python
import socket
from crt import config


def _get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


class PlayerCore:
    # ...
    async def _cast_current(self) -> None:
        idx = self._cursor_index()
        if idx is None:
            return
        item = self.library.items[idx]
        if item.status != "ready" or not item.filename:
            log.info("Cursor item %s not ready (status=%s); waiting", item.video_id, item.status)
            return
        await self.chromecast.wait_for_connection()
        local_ip = _get_local_ip()
        url = f"http://{local_ip}:{config.SERVER_PORT}/media/{item.filename}"
        item.status = "casting"
        await asyncio.to_thread(
            self.chromecast.cast_url,
            url,
            title=item.title,
            current_time=item.playback_position,
        )
        self.state = "casting"
        item.status = "playing"
        log.info("Casting %s (resume from %.1fs)", item.video_id, item.playback_position)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_player_core.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 3.4: PlayerCore._cast_current implementazione vera"
```

### Task 3.5: Autoplay loop con polling Chromecast

**Files:**
- Modify: `crt/player_core.py`
- Modify: `tests/test_player_core.py`

- [ ] **Step 1: Test fail-first**

```python
@pytest.mark.asyncio
async def test_on_playback_finished_advances_cursor():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.on_playback_finished()

    assert library.cursor_video_id == "B"


@pytest.mark.asyncio
async def test_on_playback_finished_at_end_no_loop_stops():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = False
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.on_playback_finished()

    assert library.cursor_video_id == "B"
    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_on_playback_finished_at_end_with_loop_wraps():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = True
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.on_playback_finished()

    assert library.cursor_video_id == "A"
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_player_core.py::test_on_playback_finished_advances_cursor -v
```

Expected: FAIL.

- [ ] **Step 3: Implementa**

In `crt/player_core.py`:

```python
async def on_playback_finished(self) -> None:
    """Hook invoked when chromecast reports idle_reason == 'FINISHED'.
    Marks current as done, advances cursor, casts next."""
    idx = self._cursor_index()
    if idx is not None:
        self.library.items[idx].status = "done"
        self.library.items[idx].playback_position = 0.0

    if idx is None:
        return
    if idx + 1 < len(self.library.items):
        self.library.cursor_video_id = self.library.items[idx + 1].video_id
        await self._cast_current()
    elif self.library.loop_mode:
        self.library.cursor_video_id = self.library.items[0].video_id
        await self._cast_current()
    else:
        self.state = "idle"
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_player_core.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 3.5: PlayerCore.on_playback_finished autoplay"
```

### Task 3.6: Wire `PlayerCore` al daemon (in parallelo alla TUI esistente)

**Files:**
- Modify: `crt/daemon.py`

- [ ] **Step 1: Crea PlayerCore in main**

In `crt/daemon.py`:

```python
from crt.player_core import PlayerCore

# ...
def main() -> None:
    # ... esistente ...
    queue = LibraryStore()
    saved_position = queue.load_state(config.STATE_FILE)
    chromecast = ChromecastManager()
    pipeline = PipelineWorker(queue, chromecast)
    pipeline.resume_position = saved_position
    player_core = PlayerCore(queue, chromecast)  # NEW

    # SyncEngine creation (continued from task 2.9)
    if config.YT_PLAYLIST_ID:
        try:
            yt_client = YouTubeClient.from_token_file(config.YT_TOKEN_FILE, config.YT_CLIENT_SECRETS)

            def _on_yt_remove(video_id: str):
                loop = asyncio.get_event_loop()
                asyncio.run_coroutine_threadsafe(player_core.stop_and_remove(video_id), loop)

            sync_engine = SyncEngine(queue, yt_client, config.YT_PLAYLIST_ID, on_remove=_on_yt_remove)
            log.info("SyncEngine ready")
        except Exception as e:
            log.warning("SyncEngine disabled: %s", e)
            sync_engine = None
    else:
        sync_engine = None

    app = CRTCastApp(queue, pipeline, chromecast)
    app._sync_engine = sync_engine
    app._sync_interval = config.SYNC_INTERVAL_S
    app._player_core = player_core  # available to TUI for future use
    app.run()
    # ...
```

- [ ] **Step 2: Run smoke test**

```bash
./run.sh
```

Expected: TUI parte come prima, log mostra "SyncEngine ready" e nessun errore di import.

- [ ] **Step 3: Run unit tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: tutti pass.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Phase 3.6: wire PlayerCore + SyncEngine on_remove nel daemon"
```

### Phase 3 checkpoint

A questo punto:
- `PlayerCore` esiste con cursor esplicito e tutte le transizioni testate.
- Sincronizzazione + `stop_and_remove` connesse: rimuovere un video da YT mentre suona ferma il playback.
- La TUI esistente continua a usare il suo cast loop interno (PipelineWorker.run_cast); `PlayerCore` esiste ma non è ancora il "driver" della TUI. Questo cambierà in Phase 4 quando la TUI userà l'HTTP API.

---

## Phase 4 — FastAPI unified

A fine fase: il daemon è pilotabile **interamente** via HTTP. La TUI esistente continua a funzionare (in-process). `curl` e Swagger UI sono i nuovi punti d'ingresso primari.

### Task 4.1: Crea `crt/api.py` con app factory e `/library/items`

**Files:**
- Create: `crt/api.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Test fail-first**

In `tests/test_api.py`:

```python
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from crt.api import create_app
from crt.library_store import LibraryStore, QueueItem


def _make_app(library, player=None, sync_engine=None, pipeline=None):
    return create_app(library=library, player=player, sync_engine=sync_engine, pipeline=pipeline)


def test_get_library_items_empty():
    library = LibraryStore()
    app = _make_app(library)
    client = TestClient(app)

    resp = client.get("/library/items")

    assert resp.status_code == 200
    body = resp.json()
    assert body["cursor_video_id"] is None
    assert body["loop_mode"] is False
    assert body["items"] == []


def test_get_library_items_with_cursor():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A", title="Title A", status="ready"))
    library.items.append(QueueItem(url="u/B", video_id="B", title="Title B", status="queued"))
    library.cursor_video_id = "A"
    app = _make_app(library)
    client = TestClient(app)

    resp = client.get("/library/items")

    assert resp.status_code == 200
    body = resp.json()
    assert body["cursor_video_id"] == "A"
    assert len(body["items"]) == 2
    assert body["items"][0]["video_id"] == "A"
    assert body["items"][0]["is_cursor"] is True
    assert body["items"][1]["is_cursor"] is False
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_api.py -v
```

Expected: FAIL — modulo non esiste.

- [ ] **Step 3: Implementa**

In `crt/api.py`:

```python
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

log = logging.getLogger(__name__)


def create_app(library, player=None, sync_engine=None, pipeline=None) -> FastAPI:
    app = FastAPI(title="crt-player daemon")

    @app.get("/library/items")
    def get_library_items():
        return {
            "cursor_video_id": library.cursor_video_id,
            "loop_mode": library.loop_mode,
            "items": [
                {
                    "video_id": item.video_id,
                    "id": item.id,
                    "title": item.title,
                    "status": item.status,
                    "progress": item.progress,
                    "error": item.error,
                    "is_cursor": item.video_id == library.cursor_video_id,
                }
                for item in library.items
            ],
        }

    return app
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_api.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 4.1: FastAPI app factory + GET /library/items"
```

### Task 4.2: `GET /status`

**Files:**
- Modify: `crt/api.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Test fail-first**

```python
def test_get_status_includes_youtube_pipeline_player():
    library = LibraryStore()
    sync_engine = MagicMock()
    sync_engine.state = "ok"
    sync_engine.last_sync_at = "2026-04-21T12:00:00+00:00"
    sync_engine.last_error = None
    sync_engine.playlist_id = "PLxxx"

    pipeline = MagicMock()
    pipeline.state = "idle"
    pipeline.current_video_id = None

    player = MagicMock()
    player.state = "idle"

    chromecast = MagicMock()
    chromecast.connected = True
    chromecast.current_time = 0.0
    chromecast.duration = 0.0

    app = create_app(library, player=player, sync_engine=sync_engine, pipeline=pipeline)
    app.state.chromecast = chromecast

    client = TestClient(app)
    resp = client.get("/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["youtube"]["state"] == "ok"
    assert body["youtube"]["last_sync_at"] == "2026-04-21T12:00:00+00:00"
    assert body["pipeline"]["state"] == "idle"
    assert body["player"]["state"] == "idle"
    assert body["player"]["chromecast"] == "connected"
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_api.py::test_get_status_includes_youtube_pipeline_player -v
```

Expected: FAIL.

- [ ] **Step 3: Implementa**

In `crt/api.py`, all'interno di `create_app`:

```python
@app.get("/status")
def get_status():
    yt = sync_engine
    pl = pipeline
    pc = player
    cc = getattr(app.state, "chromecast", None)

    return {
        "youtube": {
            "state": getattr(yt, "state", "ok") if yt else "disabled",
            "last_sync_at": getattr(yt, "last_sync_at", None) if yt else None,
            "last_error": getattr(yt, "last_error", None) if yt else None,
            "playlist_id": getattr(yt, "playlist_id", None) if yt else None,
            "playlist_size": len(library.items),
        },
        "pipeline": {
            "state": getattr(pl, "state", "idle") if pl else "idle",
            "current_video_id": getattr(pl, "current_video_id", None) if pl else None,
            "queue_depth": sum(1 for i in library.items if i.status == "queued"),
        },
        "player": {
            "state": getattr(pc, "state", "idle") if pc else "idle",
            "current_video_id": library.cursor_video_id,
            "current_time_s": getattr(cc, "current_time", None) if cc else None,
            "duration_s": getattr(cc, "duration", None) if cc else None,
            "chromecast": "connected" if (cc and getattr(cc, "connected", False)) else "disconnected",
        },
    }
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_api.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 4.2: GET /status"
```

### Task 4.3: Endpoint `/control/next`, `/control/prev`, `/control/toggle`, `/control/stop`

**Files:**
- Modify: `crt/api.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Test fail-first**

```python
def test_post_control_next_calls_player():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A", status="ready"))

    player = MagicMock()
    async def _next():
        library.cursor_video_id = "A"
    player.next = _next

    app = create_app(library, player=player)
    client = TestClient(app)

    resp = client.post("/control/next")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["cursor_video_id"] == "A"


def test_post_control_prev_calls_player():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A"))
    library.items.append(QueueItem(url="u/B", video_id="B"))
    library.cursor_video_id = "B"

    player = MagicMock()
    async def _prev():
        library.cursor_video_id = "A"
    player.prev = _prev

    app = create_app(library, player=player)
    client = TestClient(app)

    resp = client.post("/control/prev")

    assert resp.status_code == 200
    assert resp.json()["cursor_video_id"] == "A"


def test_post_control_stop_calls_player():
    library = LibraryStore()
    player = MagicMock()
    called = []
    async def _stop():
        called.append(1)
    player.stop = _stop

    app = create_app(library, player=player)
    client = TestClient(app)

    resp = client.post("/control/stop")

    assert resp.status_code == 200
    assert called == [1]


def test_post_control_toggle_returns_state():
    library = LibraryStore()
    player = MagicMock()
    player.state = "playing"
    async def _toggle():
        player.state = "paused"
    player.toggle = _toggle

    app = create_app(library, player=player)
    client = TestClient(app)

    resp = client.post("/control/toggle")

    assert resp.status_code == 200
    assert resp.json()["state"] == "paused"
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_api.py -v -k "control"
```

Expected: FAIL.

- [ ] **Step 3: Implementa**

In `crt/api.py`:

```python
@app.post("/control/next")
async def control_next():
    if player is None:
        raise HTTPException(503, "player unavailable")
    await player.next()
    return {"ok": True, "cursor_video_id": library.cursor_video_id}


@app.post("/control/prev")
async def control_prev():
    if player is None:
        raise HTTPException(503, "player unavailable")
    await player.prev()
    return {"ok": True, "cursor_video_id": library.cursor_video_id}


@app.post("/control/toggle")
async def control_toggle():
    if player is None:
        raise HTTPException(503, "player unavailable")
    await player.toggle()
    return {"ok": True, "state": player.state}


@app.post("/control/stop")
async def control_stop():
    if player is None:
        raise HTTPException(503, "player unavailable")
    await player.stop()
    return {"ok": True}
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_api.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 4.3: /control/next /prev /toggle /stop"
```

### Task 4.4: `/control/play/{video_id}` con 404

**Files:**
- Modify: `crt/api.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Test fail-first**

```python
def test_post_control_play_video_id():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A", status="ready"))
    library.items.append(QueueItem(url="u/B", video_id="B", status="ready"))

    player = MagicMock()
    async def _play(vid):
        library.cursor_video_id = vid
    player.play = _play

    app = create_app(library, player=player)
    client = TestClient(app)

    resp = client.post("/control/play/B")

    assert resp.status_code == 200
    assert resp.json()["cursor_video_id"] == "B"


def test_post_control_play_unknown_video_id_returns_404():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A"))

    player = MagicMock()
    async def _play(vid):
        raise KeyError(vid)
    player.play = _play

    app = create_app(library, player=player)
    client = TestClient(app)

    resp = client.post("/control/play/UNKNOWN")

    assert resp.status_code == 404
    assert "not in library" in resp.json()["error"].lower() or resp.json()["detail"]
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_api.py::test_post_control_play_video_id -v
```

Expected: FAIL.

- [ ] **Step 3: Implementa**

In `crt/api.py`:

```python
@app.post("/control/play/{video_id}")
async def control_play(video_id: str):
    if player is None:
        raise HTTPException(503, "player unavailable")
    try:
        await player.play(video_id)
    except KeyError:
        raise HTTPException(404, f"video_id {video_id} not in library")
    return {"ok": True, "cursor_video_id": library.cursor_video_id}
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_api.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 4.4: /control/play/{video_id} con 404"
```

### Task 4.5: `/control/loop/toggle`, `/control/sync`, `/control/calibrate`

**Files:**
- Modify: `crt/api.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Test fail-first**

```python
def test_post_control_loop_toggle_inverts():
    library = LibraryStore()
    library.loop_mode = False

    app = create_app(library)
    client = TestClient(app)

    resp1 = client.post("/control/loop/toggle")
    resp2 = client.post("/control/loop/toggle")

    assert resp1.json()["loop_mode"] is True
    assert resp2.json()["loop_mode"] is False


def test_post_control_sync_kicks_engine():
    library = LibraryStore()
    sync_engine = MagicMock()

    app = create_app(library, sync_engine=sync_engine)
    client = TestClient(app)

    resp = client.post("/control/sync")

    assert resp.status_code == 202
    sync_engine.kick.assert_called_once()


def test_post_control_calibrate_invokes_player():
    library = LibraryStore()
    player = MagicMock()
    called = []
    async def _calibrate():
        called.append(1)
    player.calibrate = _calibrate

    app = create_app(library, player=player)
    client = TestClient(app)

    resp = client.post("/control/calibrate")

    assert resp.status_code == 200
    assert called == [1]
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_api.py -v -k "loop or sync or calibrate"
```

Expected: FAIL.

- [ ] **Step 3: Implementa**

In `crt/api.py`:

```python
@app.post("/control/loop/toggle")
def control_loop_toggle():
    library.loop_mode = not library.loop_mode
    return {"ok": True, "loop_mode": library.loop_mode}


@app.post("/control/sync", status_code=202)
def control_sync():
    if sync_engine is None:
        raise HTTPException(503, "sync engine unavailable")
    sync_engine.kick()
    return {"ok": True}


@app.post("/control/calibrate")
async def control_calibrate():
    if player is None:
        raise HTTPException(503, "player unavailable")
    await player.calibrate()
    return {"ok": True}
```

E in `crt/player_core.py` aggiungi un `calibrate` minimale:

```python
async def calibrate(self) -> None:
    from crt import calibration
    from crt import config
    await self.chromecast.wait_for_connection()
    pattern_path = await asyncio.to_thread(calibration.generate_pattern_mp4, config.TEMP_DIR)
    local_ip = _get_local_ip()
    url = f"http://{local_ip}:{config.SERVER_PORT}/media/{pattern_path}"
    await asyncio.to_thread(self.chromecast.cast_url, url, title="Calibration")
```

(Verifica `crt/calibration.py` per il nome esatto della funzione esistente; aggiusta di conseguenza.)

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_api.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 4.5: /control/loop/toggle /sync /calibrate"
```

### Task 4.6: `GET /media/{filename}` (porting da `media_server.py`)

**Files:**
- Modify: `crt/api.py`
- Modify: `tests/test_api.py`
- Delete (eventuale): `crt/media_server.py` (dopo aver migrato)

- [ ] **Step 1: Test fail-first**

```python
def test_get_media_serves_file(tmp_path):
    f = tmp_path / "test.mp4"
    f.write_bytes(b"FAKE_MP4_BYTES")

    library = LibraryStore()
    app = create_app(library, media_dir=str(tmp_path))
    client = TestClient(app)

    resp = client.get("/media/test.mp4")

    assert resp.status_code == 200
    assert resp.content == b"FAKE_MP4_BYTES"
    assert resp.headers["content-type"].startswith("video/mp4")


def test_get_media_404_for_missing(tmp_path):
    library = LibraryStore()
    app = create_app(library, media_dir=str(tmp_path))
    client = TestClient(app)

    resp = client.get("/media/missing.mp4")

    assert resp.status_code == 404


def test_get_media_rejects_path_traversal(tmp_path):
    library = LibraryStore()
    app = create_app(library, media_dir=str(tmp_path))
    client = TestClient(app)

    resp = client.get("/media/..%2Fetc%2Fpasswd")

    assert resp.status_code == 404
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_api.py -v -k "media"
```

Expected: FAIL.

- [ ] **Step 3: Implementa**

In `crt/api.py`, aggiungi `media_dir` al factory e route:

```python
import os
from fastapi.responses import FileResponse


def create_app(library, player=None, sync_engine=None, pipeline=None, media_dir: str | None = None) -> FastAPI:
    if media_dir is None:
        from crt import config
        media_dir = config.TEMP_DIR
    # ... esistente ...

    @app.get("/media/{filename}")
    async def serve_media(filename: str):
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(404, "not found")
        filepath = os.path.join(media_dir, filename)
        if not os.path.isfile(filepath):
            raise HTTPException(404, "not found")
        return FileResponse(filepath, media_type="video/mp4")
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_api.py -v
```

Expected: pass.

- [ ] **Step 5: Rimuovi `crt/media_server.py` e i suoi import**

```bash
git rm crt/media_server.py
git rm tests/test_media_server.py  # se esiste e i suoi test sono ora coperti da test_api
grep -rln "from crt.media_server import\|from crt import media_server" --include="*.py" .
# Aggiorna tutti i file trovati per usare crt.api invece
```

- [ ] **Step 6: Run all tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Phase 4.6: integra /media in api.py, rimuovi media_server.py"
```

### Task 4.7: Daemon orchestration: avvia FastAPI come task asyncio + signal handling

**Files:**
- Modify: `crt/daemon.py`

- [ ] **Step 1: Refactor `daemon.py` per il nuovo lifecycle**

Sostituisci tutto `crt/daemon.py` con la nuova versione (basata sullo schema della spec):

```python
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

import uvicorn

from crt import config
from crt.api import create_app
from crt.chromecast_mgr import ChromecastManager
from crt.library_store import LibraryStore
from crt.pipeline import PipelineWorker
from crt.player_core import PlayerCore
from crt.sync_engine import SyncEngine
from crt.youtube_client import YouTubeClient


LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "crt_cast.log")
_log_fh = open(LOG_FILE, "w")
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    stream=_log_fh,
)
sys.stderr = _log_fh
log = logging.getLogger(__name__)


def cleanup_temp_files() -> None:
    if config.FILE_TTL_HOURS <= 0:
        return
    if not os.path.isdir(config.TEMP_DIR):
        return
    import time
    cutoff = time.time() - config.FILE_TTL_HOURS * 3600
    for fname in os.listdir(config.TEMP_DIR):
        fpath = os.path.join(config.TEMP_DIR, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
            log.info("Removing old temp file: %s", fname)
            os.remove(fpath)


async def main_async() -> None:
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    cleanup_temp_files()

    library = LibraryStore()
    library.load_state(config.STATE_FILE)
    chromecast = ChromecastManager()
    pipeline = PipelineWorker(library, chromecast)
    player = PlayerCore(library, chromecast)

    sync_engine = None
    if config.YT_PLAYLIST_ID:
        try:
            yt_client = YouTubeClient.from_token_file(config.YT_TOKEN_FILE, config.YT_CLIENT_SECRETS)

            def _on_yt_remove(video_id: str):
                loop = asyncio.get_event_loop()
                asyncio.run_coroutine_threadsafe(player.stop_and_remove(video_id), loop)

            sync_engine = SyncEngine(library, yt_client, config.YT_PLAYLIST_ID, on_remove=_on_yt_remove)
            log.info("SyncEngine ready (playlist=%s)", config.YT_PLAYLIST_ID)
        except Exception as e:
            log.warning("SyncEngine disabled: %s", e)

    app = create_app(
        library=library,
        player=player,
        sync_engine=sync_engine,
        pipeline=pipeline,
        media_dir=config.TEMP_DIR,
    )
    app.state.chromecast = chromecast

    uvicorn_config = uvicorn.Config(
        app, host="0.0.0.0", port=config.SERVER_PORT, log_level="warning",
    )
    server = uvicorn.Server(uvicorn_config)

    tasks = [
        asyncio.create_task(server.serve(), name="uvicorn"),
        asyncio.create_task(chromecast.discover_loop(), name="cc_discovery"),
        asyncio.create_task(pipeline.run_prepare(), name="pipeline_prepare"),
    ]
    if sync_engine is not None:
        tasks.append(asyncio.create_task(
            sync_engine.run_loop(interval_s=config.SYNC_INTERVAL_S),
            name="sync_loop",
        ))

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    log.info("Daemon ready on port %d", config.SERVER_PORT)
    await stop_event.wait()
    log.info("Shutdown signal received")

    server.should_exit = True
    for t in tasks:
        if not t.done():
            t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    library.save_state(config.STATE_FILE, playback_position=chromecast.current_time)
    chromecast.set_status_callback(None)
    chromecast.set_connection_callback(None)
    pipeline.cancel_current()
    if chromecast.cast:
        try:
            chromecast.cast.quit_app()
        except Exception:
            pass
    chromecast.shutdown()
    log.info("Daemon stopped")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
```

**Importante**: questa versione non avvia più la TUI (`CRTCastApp`). La TUI verrà sostituita dal client HTTP in Phase 5. Per ora, il daemon è solo HTTP.

- [ ] **Step 2: Smoke test daemon HTTP**

```bash
./run.sh &
sleep 3
curl -s http://localhost:8765/library/items | python -m json.tool
curl -s http://localhost:8765/status | python -m json.tool
curl -X POST http://localhost:8765/control/sync
sleep 5
curl -s http://localhost:8765/library/items | python -m json.tool
kill %1
```

Expected: risposte JSON ben formate. Dopo il sync, gli item della playlist YouTube popolano `library/items`.

- [ ] **Step 3: Run unit tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_ui.py
```

Expected: tutti pass tranne `test_ui.py` (la TUI non è più la primary; i test esistenti del UI ora si rompono perché `CRTCastApp` non è più montata dal daemon. Saranno aggiornati in Phase 5).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Phase 4.7: daemon è ora HTTP-only (no TUI)"
```

### Phase 4 checkpoint

A questo punto:
- `./run.sh` avvia un daemon **headless** che espone HTTP su 8765.
- `curl` può fare tutte le operazioni: vedere la library, controllare playback, forzare sync, calibrare.
- Swagger UI su `http://localhost:8765/docs` mostra tutte le route.
- La TUI è momentaneamente non funzionante — verrà ricostruita come client HTTP in Phase 5.
- Sistema **utilizzabile** via curl/Postman/Home Assistant; il bridge Flipper potrebbe già agganciarsi alle stesse route.
- Test unit: tutti pass (tranne `test_ui.py` che è stale, lo aggiusteremo).

---

## Phase 5 — TUI as remote HTTP client

A fine fase: `crt-tui` è un client che parla al daemon via HTTP. Gira fuori dal container, dove ti pare.

### Task 5.1: Crea package `tui_client/` e `data_provider.py`

**Files:**
- Create: `tui_client/__init__.py`, `tui_client/main.py`, `tui_client/data_provider.py`
- Create: `tests/test_tui_client_data_provider.py`

- [ ] **Step 1: Test fail-first per data provider**

In `tests/test_tui_client_data_provider.py`:

```python
import pytest
from unittest.mock import MagicMock, patch

from tui_client.data_provider import DaemonClient


def test_fetch_library_items_calls_correct_endpoint():
    with patch("tui_client.data_provider.httpx.Client") as MockClient:
        instance = MockClient.return_value
        instance.get.return_value.json.return_value = {
            "cursor_video_id": "A",
            "loop_mode": False,
            "items": [],
        }
        instance.get.return_value.raise_for_status = MagicMock()

        client = DaemonClient("http://daemon:8765")
        result = client.fetch_library()

        instance.get.assert_called_with("/library/items")
        assert result["cursor_video_id"] == "A"


def test_post_control_next():
    with patch("tui_client.data_provider.httpx.Client") as MockClient:
        instance = MockClient.return_value
        instance.post.return_value.json.return_value = {"ok": True, "cursor_video_id": "B"}
        instance.post.return_value.raise_for_status = MagicMock()

        client = DaemonClient("http://daemon:8765")
        result = client.next()

        instance.post.assert_called_with("/control/next")
        assert result["cursor_video_id"] == "B"
```

- [ ] **Step 2: Run, verifica fail**

```bash
python -m pytest tests/test_tui_client_data_provider.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implementa**

In `tui_client/__init__.py`: vuoto.

In `tui_client/data_provider.py`:

```python
from __future__ import annotations

import httpx


class DaemonClient:
    def __init__(self, base_url: str, timeout_s: float = 5.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def fetch_library(self) -> dict:
        r = self._client.get("/library/items")
        r.raise_for_status()
        return r.json()

    def fetch_status(self) -> dict:
        r = self._client.get("/status")
        r.raise_for_status()
        return r.json()

    def next(self) -> dict:
        r = self._client.post("/control/next")
        r.raise_for_status()
        return r.json()

    def prev(self) -> dict:
        r = self._client.post("/control/prev")
        r.raise_for_status()
        return r.json()

    def toggle(self) -> dict:
        r = self._client.post("/control/toggle")
        r.raise_for_status()
        return r.json()

    def stop(self) -> dict:
        r = self._client.post("/control/stop")
        r.raise_for_status()
        return r.json()

    def play(self, video_id: str) -> dict:
        r = self._client.post(f"/control/play/{video_id}")
        r.raise_for_status()
        return r.json()

    def loop_toggle(self) -> dict:
        r = self._client.post("/control/loop/toggle")
        r.raise_for_status()
        return r.json()

    def trigger_sync(self) -> dict:
        r = self._client.post("/control/sync")
        r.raise_for_status()
        return r.json()

    def calibrate(self) -> dict:
        r = self._client.post("/control/calibrate")
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_tui_client_data_provider.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 5.1: tui_client.data_provider DaemonClient"
```

### Task 5.2: Porta `crt/ui.py` in `tui_client/ui.py` rimpiazzando l'in-process state con HTTP polling

**Files:**
- Move: `crt/ui.py` → `tui_client/ui.py`
- Modify: per usare `DaemonClient` invece di `LibraryStore` / `PipelineWorker` / `ChromecastManager` diretti

Questo è un task grosso e in parte meccanico. Strategia: la UI continua a essere visivamente la stessa, ma i metodi che leggono lo stato (oggi `self.queue.items`, `self.chromecast.player_state`, etc.) ora chiamano `self.client.fetch_library()` / `fetch_status()`. I metodi che eseguono azioni chiamano `self.client.next()` / etc.

- [ ] **Step 1: Sposta il file**

```bash
git mv crt/ui.py tui_client/ui.py
```

- [ ] **Step 2: Sostituisci la classe `CRTCastApp`**

Riscrivi `tui_client/ui.py`. Mantieni la *struttura visiva* di `compose()` e i bind tasti, ma rimpiazza i data-source.

```python
from __future__ import annotations

import asyncio
import logging

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from tui_client.data_provider import DaemonClient

log = logging.getLogger(__name__)


class CRTCastApp(App):
    CSS = """ /* mantieni il CSS esistente */ """

    BINDINGS = [
        Binding("ctrl+space", "toggle", "Play/Pause", priority=True),
        Binding("ctrl+s", "stop", "Stop", priority=True),
        Binding("ctrl+n", "next", "Next", priority=True),
        Binding("ctrl+b", "prev", "Prev", priority=True),
        Binding("ctrl+t", "calibrate", "Calibrate", priority=True),
        Binding("ctrl+r", "loop_toggle", "Loop", priority=True),
        Binding("ctrl+y", "sync", "Sync now", priority=True),
        Binding("ctrl+c", "quit", "Quit", priority=True),
    ]

    library_state = reactive({})
    status_state = reactive({})

    def __init__(self, daemon_url: str):
        super().__init__()
        self.client = DaemonClient(daemon_url)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("", id="status_bar"),
            ListView(id="items_list"),
        )
        yield Footer()

    async def on_mount(self) -> None:
        self.set_interval(1.0, self._refresh_library)
        self.set_interval(2.0, self._refresh_status)
        await self._refresh_library()
        await self._refresh_status()

    async def _refresh_library(self) -> None:
        try:
            self.library_state = await asyncio.to_thread(self.client.fetch_library)
        except Exception as e:
            log.warning("fetch_library failed: %s", e)

    async def _refresh_status(self) -> None:
        try:
            self.status_state = await asyncio.to_thread(self.client.fetch_status)
        except Exception as e:
            log.warning("fetch_status failed: %s", e)

    def watch_library_state(self, value: dict) -> None:
        list_view = self.query_one("#items_list", ListView)
        list_view.clear()
        for item in value.get("items", []):
            marker = "▶ " if item.get("is_cursor") else "  "
            label = f"{marker}{item['status']:>10s}  {item['title']}"
            list_view.append(ListItem(Label(label)))

    def watch_status_state(self, value: dict) -> None:
        try:
            bar = self.query_one("#status_bar", Static)
        except Exception:
            return
        yt = value.get("youtube", {})
        pl = value.get("player", {})
        bar.update(
            f"YT: {yt.get('state', '?')} (last sync: {yt.get('last_sync_at', 'never')})  |  "
            f"Player: {pl.get('state', '?')}  |  CC: {pl.get('chromecast', '?')}"
        )

    async def action_toggle(self) -> None:
        await asyncio.to_thread(self.client.toggle)

    async def action_stop(self) -> None:
        await asyncio.to_thread(self.client.stop)

    async def action_next(self) -> None:
        await asyncio.to_thread(self.client.next)

    async def action_prev(self) -> None:
        await asyncio.to_thread(self.client.prev)

    async def action_calibrate(self) -> None:
        await asyncio.to_thread(self.client.calibrate)

    async def action_loop_toggle(self) -> None:
        await asyncio.to_thread(self.client.loop_toggle)

    async def action_sync(self) -> None:
        await asyncio.to_thread(self.client.trigger_sync)
```

(Note: il CSS originale di `crt/ui.py` va incollato dentro la stringa `CSS`. Lo lascio come exercise: copia esattamente il valore del campo `CSS = """..."""` dall'ui.py spostato.)

- [ ] **Step 3: Crea `tui_client/main.py`**

```python
from __future__ import annotations

import os

from tui_client.ui import CRTCastApp


def main() -> None:
    daemon_url = os.environ.get("CRT_DAEMON_URL", "http://localhost:8765")
    app = CRTCastApp(daemon_url)
    app.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Aggiorna pyproject.toml entry point**

Già fatto nel task 1.3 (`crt-tui = "tui_client.main:main"`). Re-installa:

```bash
.venv/bin/pip install -e .
```

- [ ] **Step 5: Smoke test**

Da un terminale:

```bash
./run.sh &  # avvia daemon
```

Da un altro:

```bash
crt-tui
```

Expected: la TUI si apre, mostra la library popolata via HTTP, i tasti `Ctrl+N/B/Space/S` funzionano (controllando il Chromecast attraverso il daemon). Premi `Ctrl+C` per chiudere TUI; chiudi daemon con `kill`.

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_ui.py
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Phase 5.2: TUI come client HTTP remoto"
```

### Task 5.3: Riscrivi `tests/test_ui.py` per il nuovo client HTTP

**Files:**
- Modify: `tests/test_ui.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Riscrivi i test fixture e i test**

In `tests/conftest.py`, sostituisci il fixture `app` con uno che usa `DaemonClient` mockato:

```python
@pytest.fixture
def mock_daemon_client():
    from unittest.mock import MagicMock
    c = MagicMock()
    c.fetch_library.return_value = {"cursor_video_id": None, "loop_mode": False, "items": []}
    c.fetch_status.return_value = {
        "youtube": {"state": "ok", "last_sync_at": None, "last_error": None},
        "player": {"state": "idle", "chromecast": "disconnected"},
    }
    c.next.return_value = {"ok": True, "cursor_video_id": "A"}
    c.prev.return_value = {"ok": True, "cursor_video_id": None}
    c.toggle.return_value = {"ok": True, "state": "paused"}
    c.stop.return_value = {"ok": True}
    c.calibrate.return_value = {"ok": True}
    c.loop_toggle.return_value = {"ok": True, "loop_mode": True}
    c.trigger_sync.return_value = {"ok": True}
    return c


@pytest.fixture
def tui_app(mock_daemon_client):
    from tui_client.ui import CRTCastApp
    app = CRTCastApp("http://mock")
    app.client = mock_daemon_client
    return app
```

In `tests/test_ui.py`, riscrivi i test che dipendevano dalla TUI in-process. Esempio minimo:

```python
import pytest


@pytest.mark.asyncio
async def test_press_ctrl_n_calls_next(tui_app, mock_daemon_client):
    async with tui_app.run_test() as pilot:
        await pilot.press("ctrl+n")
        await pilot.pause()
    mock_daemon_client.next.assert_called()


@pytest.mark.asyncio
async def test_library_renders_from_fetch(tui_app, mock_daemon_client):
    mock_daemon_client.fetch_library.return_value = {
        "cursor_video_id": "A",
        "loop_mode": False,
        "items": [
            {"video_id": "A", "id": "x", "title": "Hello", "status": "ready", "progress": 100, "error": None, "is_cursor": True},
        ],
    }
    async with tui_app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()  # dopo refresh interval
    # Verifica che la lista contenga "Hello" (controllo soft: cerca in render)
```

(I test specifici dipendevano molto dall'UI vecchia con queue manuale. Per ora aggiungi solo questi smoke test; più test specifici si possono aggiungere se servono.)

- [ ] **Step 2: Run**

```bash
python -m pytest tests/test_ui.py -v
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "Phase 5.3: aggiorna test_ui per il client HTTP"
```

### Phase 5 checkpoint

A questo punto:
- `crt-tui` parla al daemon via HTTP, può girare ovunque in LAN.
- Tutta la suite test verde.
- Il sistema è completamente operabile dal Mac (TUI) o da qualsiasi script HTTP.

---

## Phase 6 — Docker

A fine fase: il daemon gira in un container Docker sul homeserver Linux.

### Task 6.1: Crea `docker/Dockerfile`

**Files:**
- Create: `docker/Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 1: Scrivi il Dockerfile**

In `docker/Dockerfile`:

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

COPY crt ./crt
COPY tui_client ./tui_client

RUN pip install --no-cache-dir -e .

ENV CRT_TEMP_DIR=/data/cache
ENV CRT_STATE_FILE=/data/state/state.json
ENV CRT_YT_CLIENT_SECRETS=/data/secrets/client_secrets.json
ENV CRT_YT_TOKEN_FILE=/data/secrets/oauth_token.json

EXPOSE 8765

ENTRYPOINT ["crt-daemon"]
```

In `.dockerignore`:

```
.venv
.git
.env*
*.log
state.json
state.json.v1.bak
__pycache__
tests
docs
docker
```

- [ ] **Step 2: Build locale (smoke)**

```bash
docker build -f docker/Dockerfile -t crt-player:dev .
```

Expected: build success.

- [ ] **Step 3: Commit**

```bash
git add docker/Dockerfile .dockerignore
git commit -m "Phase 6.1: Dockerfile"
```

### Task 6.2: Crea `docker/docker-compose.yml`

**Files:**
- Create: `docker/docker-compose.yml`

- [ ] **Step 1: Scrivi il compose**

In `docker/docker-compose.yml`:

```yaml
services:
  crt-daemon:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    image: crt-player:latest
    restart: unless-stopped
    network_mode: host
    environment:
      CRT_YT_PLAYLIST_ID: "${CRT_YT_PLAYLIST_ID}"
      CRT_CHROMECAST_NAME: "${CRT_CHROMECAST_NAME:-Living Room TV}"
      CRT_SCALE_MODE: "${CRT_SCALE_MODE:-crop}"
      CRT_AUTO_CROP: "${CRT_AUTO_CROP:-1}"
      CRT_SYNC_INTERVAL_S: "${CRT_SYNC_INTERVAL_S:-300}"
      CRT_LOG_LEVEL: "${CRT_LOG_LEVEL:-INFO}"
      CRT_MARGIN_TOP: "${CRT_MARGIN_TOP:-0}"
      CRT_MARGIN_BOTTOM: "${CRT_MARGIN_BOTTOM:-0}"
      CRT_MARGIN_LEFT: "${CRT_MARGIN_LEFT:-0}"
      CRT_MARGIN_RIGHT: "${CRT_MARGIN_RIGHT:-0}"
    volumes:
      - ./data/cache:/data/cache
      - ./data/state:/data/state
      - ./data/secrets:/data/secrets:ro
```

- [ ] **Step 2: Documenta procedura bootstrap**

Crea `docker/README.md`:

```markdown
# Docker deploy

## Setup iniziale (una volta)

1. Crea un progetto in Google Cloud Console, abilita YouTube Data API v3, scarica `client_secrets.json`.
2. Crea i volumi:
   ```
   mkdir -p data/cache data/state data/secrets
   cp /path/to/client_secrets.json data/secrets/
   ```
3. Configura `.env` accanto a `docker-compose.yml`:
   ```
   CRT_YT_PLAYLIST_ID=PL_your_playlist_id
   CRT_CHROMECAST_NAME=Your TV
   ```
4. Bootstrap OAuth (interattivo):
   ```
   docker compose run --rm crt-daemon crt-bootstrap
   ```
5. Avvia in background:
   ```
   docker compose up -d
   ```

## Operazioni

- Logs: `docker compose logs -f`
- Stop: `docker compose down`
- Sync forzato: `curl -X POST http://localhost:8765/control/sync`
- Status: `curl http://localhost:8765/status | jq`
```

- [ ] **Step 3: Commit**

```bash
git add docker/
git commit -m "Phase 6.2: docker-compose.yml + README deploy"
```

### Task 6.3: Smoke test deploy completo (manuale)

Questo è un task di verifica, non scrive codice.

- [ ] **Step 1: Su un host Linux con Docker**

```bash
cd /path/to/crt-player
mkdir -p docker/data/cache docker/data/state docker/data/secrets
cp /path/to/client_secrets.json docker/data/secrets/
cd docker
echo "CRT_YT_PLAYLIST_ID=PL_test_playlist" > .env
echo "CRT_CHROMECAST_NAME=Test TV" >> .env
docker compose run --rm crt-daemon crt-bootstrap
# Esegui flow OAuth, incolla URL
docker compose up -d
sleep 10
curl http://localhost:8765/status | jq
docker compose logs --tail 50
```

Expected: status JSON ben formato, log mostra "SyncEngine ready" e "Daemon ready on port 8765". Aggiungere un video alla playlist YT, attendere ~5 min, verificare con `curl /library/items` che appare.

- [ ] **Step 2: Test di telecomando**

```bash
curl -X POST http://localhost:8765/control/sync
# attendi pochi secondi, poi:
curl -X POST http://localhost:8765/control/toggle
# Il Chromecast dovrebbe iniziare a riprodurre il primo item
curl -X POST http://localhost:8765/control/next
# Avanza al secondo
curl -X POST http://localhost:8765/control/stop
```

Expected: comandi accettati, TV reagisce di conseguenza.

- [ ] **Step 3: Cleanup**

```bash
docker compose down
```

- [ ] **Step 4: Documenta eventuali fix necessari**

Se uno dei passaggi sopra fallisce (es. mDNS non funziona, OAuth scope mancante), fix nel codice e nuovo commit. Possibili problemi tipici:
- Bind di port 8765 conflict con altro servizio → cambia `CRT_SERVER_PORT`.
- Chromecast non discovery in Docker → verifica `network_mode: host`.
- ffmpeg non trovato → controlla che il `apt install` sia eseguito.

- [ ] **Step 5: Commit dei fix (se necessari)**

```bash
git add -A
git commit -m "Phase 6.3: fix smoke deploy"
```

### Phase 6 checkpoint

A questo punto:
- Container Docker funzionante sul homeserver.
- Bootstrap OAuth eseguito una volta sola.
- `docker compose up -d` avvia tutto.
- Sync, download, encoding, casting funzionano end-to-end.

---

## Phase 7 — Integration tests refresh

A fine fase: i test di integrazione sono allineati al nuovo flusso (daemon HTTP + sync da playlist YT reale).

### Task 7.1: Aggiorna `tests/test_integration.py` al nuovo flusso

**Files:**
- Modify: `tests/test_integration.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Aggiungi env var TEST_YT_PLAYLIST_ID al fixture**

In `tests/conftest.py`, in `integration_config`:

```python
@pytest.fixture(scope="session")
def integration_config():
    name = os.environ.get("TEST_CHROMECAST_NAME", "").strip()
    playlist = os.environ.get("TEST_YT_PLAYLIST_ID", "").strip()
    if not name or not playlist:
        pytest.skip(
            "Integration tests require TEST_CHROMECAST_NAME and TEST_YT_PLAYLIST_ID env vars. "
            "Run: source .env.integration"
        )
    return {
        "chromecast_name": name,
        "playlist_id": playlist,
        "playback_wait_s": int(os.environ.get("TEST_PLAYBACK_WAIT_S", "300")),
        "encode_wait_s": int(os.environ.get("TEST_ENCODE_WAIT_S", "600")),
    }
```

- [ ] **Step 2: Crea fixture per daemon completo**

```python
@pytest.fixture
def integration_daemon(integration_config, tmp_path_factory):
    """Avvia il daemon completo (HTTP + sync + player) in-process per il test."""
    import asyncio
    import threading
    from crt import config as cfg
    from crt.api import create_app
    from crt.chromecast_mgr import ChromecastManager
    from crt.library_store import LibraryStore
    from crt.pipeline import PipelineWorker
    from crt.player_core import PlayerCore
    from crt.sync_engine import SyncEngine
    from crt.youtube_client import YouTubeClient

    d = tmp_path_factory.mktemp("integration_daemon")
    cfg.TEMP_DIR = str(d / "cache")
    cfg.STATE_FILE = str(d / "state.json")
    cfg.CHROMECAST_NAME = integration_config["chromecast_name"]
    cfg.YT_PLAYLIST_ID = integration_config["playlist_id"]

    library = LibraryStore()
    cc = ChromecastManager()
    cc._discover_sync()
    pipeline = PipelineWorker(library, cc)
    player = PlayerCore(library, cc)
    yt = YouTubeClient.from_token_file(cfg.YT_TOKEN_FILE, cfg.YT_CLIENT_SECRETS)
    sync_engine = SyncEngine(library, yt, cfg.YT_PLAYLIST_ID)

    app = create_app(library=library, player=player, sync_engine=sync_engine, pipeline=pipeline, media_dir=cfg.TEMP_DIR)
    app.state.chromecast = cc

    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=cfg.SERVER_PORT, log_level="warning"))
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    import time
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)

    yield {
        "library": library,
        "player": player,
        "sync_engine": sync_engine,
        "chromecast": cc,
        "pipeline": pipeline,
        "url": f"http://localhost:{cfg.SERVER_PORT}",
    }

    server.should_exit = True
    cc.set_status_callback(None)
    cc.set_connection_callback(None)
    try:
        cc.shutdown()
    except Exception:
        pass
```

- [ ] **Step 3: Riscrivi i test di integration**

Sostituisci i test esistenti con uno scenario end-to-end:

```python
import time
import httpx
import pytest


@pytest.mark.integration
def test_full_flow_sync_download_cast(integration_daemon, integration_config):
    """End-to-end: daemon polla YT, scarica, encoda, casta."""
    base = integration_daemon["url"]

    # Force sync
    r = httpx.post(f"{base}/control/sync")
    assert r.status_code == 202

    # Wait for sync to populate library
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        items = httpx.get(f"{base}/library/items").json()["items"]
        if items:
            break
        time.sleep(1)
    assert items, "library not populated within 30s"

    # Wait for first item to be ready (download + encode)
    target_vid = items[0]["video_id"]
    deadline = time.monotonic() + integration_config["encode_wait_s"]
    while time.monotonic() < deadline:
        cur = next((i for i in httpx.get(f"{base}/library/items").json()["items"] if i["video_id"] == target_vid), None)
        if cur and cur["status"] == "ready":
            break
        time.sleep(2)
    assert cur and cur["status"] == "ready", f"item not ready within {integration_config['encode_wait_s']}s"

    # Trigger play
    r = httpx.post(f"{base}/control/play/{target_vid}")
    assert r.status_code == 200

    # Wait for playback
    time.sleep(5)
    status = httpx.get(f"{base}/status").json()
    assert status["player"]["state"] in ("casting", "playing")

    # Cleanup
    httpx.post(f"{base}/control/stop")
```

(I test pre-esistenti che usavano `integration_app`/`real_pipeline`/etc. possono essere conservati come "legacy" se ancora rilevanti, oppure rimossi. Strategia minima: rimuoverli e tenere solo quello sopra.)

- [ ] **Step 4: Run integration test (richiede env vars + Chromecast reale)**

```bash
source .env.integration  # con TEST_CHROMECAST_NAME, TEST_YT_PLAYLIST_ID, etc.
python -m pytest tests/test_integration.py -v -m integration -s
```

Expected: il test passa. Tempo atteso: 5-10 min.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Phase 7.1: integration test sul flusso headless via HTTP"
```

### Phase 7 checkpoint

A questo punto:
- Tutto il sistema è testato end-to-end.
- Pronto per uso quotidiano.

---

## Self-review (eseguito al momento della scrittura del piano)

**Spec coverage:**
- A2 (OAuth + dedicated playlist): coperto in 2.5 (`crt-bootstrap`).
- B1+ (single daemon, modular): coperto in 1.1-1.8 (package + 4.7 (uvicorn unico)).
- C1 (YT master): coperto in 2.6 (`SyncEngine` diff con reorder + remove).
- D1 (no write-back): nessun task scrive su YT — coperto per omissione.
- E4 (REST + TUI client): 4.x + 5.x.
- F1 (LAN trust): 4.7 bind 0.0.0.0, nessun token check.
- Cursor esplicito: 1.6 + 3.1.
- Stop+remove on YT removal: 2.7 + 3.3.
- State.json v2 + migration: 1.7.
- Migrazione da prototipo: documented in 1.x sequence (no separate migration code).

**Placeholder scan:** nessun "TBD". Ogni task ha codice eseguibile in ogni step.

**Type consistency:** `LibraryStore.cursor_video_id`, `QueueItem.video_id`, `PlayerCore.state`, `SyncEngine.state`/`last_sync_at`/`last_error` definite e referenziate coerentemente. `PlayerCore.next/prev/toggle/stop/play/calibrate/stop_and_remove/on_playback_finished` tutti async, tutti referenziati con `await` nelle chiamate dell'API.

**Scope:** un piano sequenziale di 7 fasi, ciascuna autocontenuta. Phase 1-4 producono già un sistema utile via curl; phase 5-7 polish.
