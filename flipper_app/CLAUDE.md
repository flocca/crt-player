# CLAUDE.md — flipper_app

This file provides guidance to Claude Code when working inside this subproject (`flipper_app/`). The parent project CLAUDE.md is in `../CLAUDE.md`; this file covers only what's specific to the Flipper FAP.

## What this is

A Flipper Zero FAP (native C app) that acts as a BLE remote for the `crt-player` daemon. It pairs with the `crt-flipper-bridge` running on Lodge (sibling `lodge-tools` repo at `services/crt-flipper-bridge/`); the bridge translates BLE bytes into HTTP POSTs against `crt/api.py`.

The FAP is its own build/release lifecycle — it does not share the Python venv, tests, or CI of the parent repo. It is checked into this repo solely for spec co-location with the bridge contract.

## Commands

```bash
# All commands run from this directory (flipper_app/).
ufbt              # build → dist/crt_remote.fap (requires ufbt SDK installed globally)
ufbt launch       # build + flash + start on the Flipper attached via USB
ufbt cli log      # tail FURI_LOG output from the Flipper over the USB CLI
ufbt vscode_dist  # regenerate VSCode IntelliSense config (already committed in .vscode/)
```

No Python tests in this subdir — the Flipper toolchain doesn't support unit tests for FAPs. Validation is on-device smoke test against the real bridge on Lodge.

## Architecture

Single-source app: [crt_remote_app.c](crt_remote_app.c) — ViewPort + input handler + BLE profile activation. The full BLE Serial profile is vendored locally under [libs/](libs/) (see "Forked Serial profile" below).

Lifecycle in `crt_remote_app(void* p)`:
1. Alloc input queue + ViewPort with `draw_callback` / `input_callback`.
2. Open `RECORD_BT`, set a **separate keys storage** (`APP_DATA_PATH(".bt_serial.keys")`) so our bond doesn't collide with the system Flipper bond (qFlipper Mac/iOS).
3. `bt_profile_start(bt, ble_profile_serial, &params)` with `BleProfileSerialParams { device_name_prefix="CRTRem", mac_xor=0x0042 }`.
4. Blocking input loop: each `InputTypeShort`/`InputTypeLong` press sends one command byte via `ble_profile_serial_tx`.
5. On exit: `bt_disconnect`, restore default keys path, `bt_profile_restore_default`, close `RECORD_BT`. Order matters — without restoring the default keys path the system BT use after the app exits would pair against our custom store.

The app is **TX-only in this version**. No RX callback is registered; the bridge runs in no-op on the RX channel. The full bidirectional protocol (`0x01 last_result`, `0x02 status update`) is defined in the spec but not implemented here — re-enabling means calling `ble_profile_serial_set_event_callback` and dispatching on `data[0]`. The byte-level contract supports both versions without bridge changes.

### Display rotation

The FAP calls `view_port_set_orientation(view_port, ViewPortOrientationVerticalFlip)` once during init (after `view_port_alloc()`, before `gui_add_view_port()`), so all subsequent input + drawing operate in 64×128 logical coordinates from the user's POV with the device rotated for landscape grip (original right edge of the Flipper becomes the user's top).

**Two things the Flipper SDK does NOT make obvious:**
1. There is no `canvas_set_orientation()` API. The correct call is `view_port_set_orientation()` on the **ViewPort** (not the canvas), at setup time — not per-frame in `draw_callback`.
2. `view_port_set_orientation` rotates **both** the canvas pixels **and** the input event codes. So `InputKeyUp` arrives when the user presses what they perceive as up (after rotation); there's no need to remap physical-to-user in the input handler. Earlier drafts of this code assumed only the canvas rotated and added a reverse-remap — that produced double-rotated inputs (pressing user-Up gave you the byte for user-Down). The current code maps `InputKey*` directly to user POV.

If on-device tests show the rotation runs the wrong way (display upside-down) and/or inputs feel mirrored, swap the enum between `ViewPortOrientationVertical` and `ViewPortOrientationVerticalFlip` — Flipper SDK docs don't say which corresponds to 90° CW vs CCW.

## Forked Serial profile (`libs/serial_profile.{c,h}`)

This is the **load-bearing piece** of this FAP and the reason it works at all on stock firmware. Origin: Flipper Zero firmware `targets/f7/ble_glue/profiles/serial_profile.c`, with the `BleProfileSerialParams { mac_xor, device_name_prefix }` extension popularized by Momentum FW, sourced via `EmmerichFrog/home_remote_public`. GPL-3.0 (see [COPYING](COPYING) / [LICENSE](LICENSE) — both apply).

### Why the fork is needed

Stock firmware ships a `Serial` profile, but the firmware's **BtSrv RPC handler** is bound to that profile's MAC. If the FAP activates the stock profile and the bridge connects, BtSrv opens an RPC session on top of ours — TX bytes get swallowed by the RPC handler, the bridge sees protocol-framed garbage or silent drops, and pairing logic intermittently disconnects us.

The fix is to ship our own profile with:
- **Custom MAC** (`mac_xor` parameter): different MAC → BtSrv RPC doesn't match → our session stays clean.
- **Custom advertise name** (`device_name_prefix`): so the bridge can discover us by substring `CRTRem` instead of relying on a guessed MAC.

### MAC derivation

In [libs/serial_profile.c](libs/serial_profile.c), `ble_profile_serial_get_config()` builds the GAP MAC from `furi_hal_version_get_ble_mac()`:
- `byte 2 += 1` (always, even with `mac_xor=0`)
- `byte 0 ^= mac_xor & 0xFF`
- `byte 1 ^= (mac_xor >> 8) & 0xFF`
- bytes 3/4/5 unchanged

With the default `mac_xor=0x0042` this means `byte 2 += 1` and `byte 0 ^= 0x42`. The MAC seen by the bridge is **not** the one shown in the Flipper's Bluetooth menu — discover it via `bluetoothctl scan on` looking for advert name containing `CRTRem`.

The advertise name format is `<X>CRTRem <NAME>` where `<X>` is the first char of the Flipper's device name and `<NAME>` is the Flipper's name (e.g. `FCRTRem Dlignone`). The substring `CRTRem` is the only stable handle — use it for discovery.

## Button → command byte mapping

The FAP runs in two scenes — `SceneHome` and `SceneExtraMenu` — selected by long-press OK on Home. Mapping below is from the user's POV with the Flipper held rotated for landscape grip (see "Display rotation"). The SDK remaps input events along with the canvas, so `InputKey*` in code = user POV (no physical-to-user mental translation needed).

### SceneHome

| Key (user POV) | Byte | Bridge endpoint |
|---|---|---|
| Up (short) | `0x02` | `/control/prev` |
| Down (short) | `0x01` | `/control/next` |
| Left (short) | `0x08` | `/control/seek/back/15` |
| Right (short) | `0x09` | `/control/seek/forward/30` |
| OK (short) | `0x03` | `/control/toggle` |
| OK (long) | — | enters `SceneExtraMenu` (in-FAP only) |
| Back (short) | — | exit app |

### SceneExtraMenu

| Key (user POV) | Action |
|---|---|
| Up (short) | move cursor up |
| Down (short) | move cursor down |
| OK (short) | send selected byte, return to `SceneHome` |
| Back (short) | return to `SceneHome` without sending |

Menu entries (hardcoded order): `Stop` (0x04), `Elimina video` (0x0A), `Calibrate` (0x07), `Toggle loop` (0x05), `Sync now` (0x06).

`Stop`/`Loop`/`Sync`/`Calibrate` no longer have dedicated keys — all four moved into the extras menu in v2.

The byte values are duplicated as `CMD_*` `#define`s at the top of [crt_remote_app.c](crt_remote_app.c#L22-L28). The bridge's `COMMAND_TABLE` lives in `../../lodge-tools/services/crt-flipper-bridge/bridge.py`. **When you change/add a mapping here, mirror it in the bridge** — the spec ([../docs/superpowers/specs/2026-05-10-flipper-remote-design.md](../docs/superpowers/specs/2026-05-10-flipper-remote-design.md)) is the contract, but both ends are hand-maintained.

The set of daemon endpoints lives in `../crt/api.py`. Adding a new control endpoint there means: pick a free byte, add a `CMD_*` define + switch case here, and add the entry to the bridge.

## On-screen UI

Two scenes (`SceneHome`, `SceneExtraMenu`) drawn by a scene-dispatching `draw_callback` that calls `draw_home` or `draw_extra_menu`. Logical canvas is 64×128 (rotated 90° CCW; see "Display rotation").

**`SceneHome`** (default):
- Header "CRT Remote" (FontPrimary, centered at y=10).
- Status line: `BLE: starting` / `BLE: active` / `BLE: failed` — reflects `app->ble_state`. **BLE link state only, not player state** (player state RX is unimplemented).
- Four left-aligned direction labels: `< -15s` (y=42), `> +30s` (y=54), `^ prev` (y=66), `v next` (y=78). ASCII glyphs (UTF-8 arrows may not render in the default Flipper font).
- Footer: `OK = play/pause` and `hold OK: extras`.

**`SceneExtraMenu`**: list of 5 hardcoded entries (`MENU_ITEMS`) with `> ` cursor on the selected row. Header `Comandi`, footer `OK conferma` / `Back annulla`. See "Button → command byte mapping".

No font with libfreetype-style metrics — keep strings short to avoid clipping. The view doesn't auto-refresh; if you mutate state outside the input loop (e.g. on scene transition, menu navigation) you must call `view_port_update(app.view_port)`.

## Gotchas

- **Don't add `sources=` to [application.fam](application.fam) once subdirs exist.** ufbt auto-discovery is recursive by default; an explicit glob like `sources=["*.c", "libs/*.c"]` causes duplicate-definition link errors because `libs/serial_profile.c` ends up in the build twice. Leave the glob unspecified.
- **`requires=["bt", "gui"]` is mandatory** — without `bt`, `bt_profile_start` is a null symbol at runtime and the app crashes on launch. `stack_size = 4 * 1024` is the minimum that survives a profile start; halving it caused stack overflows during smoke tests.
- **Always set a separate keys storage before `bt_profile_start`** (`bt_keys_storage_set_storage_path(...)`). Without it, our custom-MAC bond writes into the system keystore and corrupts the qFlipper / Mac BT pairing. Equally important: restore the default path on teardown.
- **`furi_hal_bt_is_active()` can return false momentarily during link renegotiation.** `ble_serial_send_byte` gracefully skips TX in that case and logs a warning — the press is lost. There's no queue; users may need to repeat the press if pressed during a reconnect.
- **Don't try to register a GATT server from a FAP.** The Flipper public API only switches between pre-provisioned profiles (HID, Serial, BLE Beacon, …); there is no `register_service` for custom UUIDs. This is why the protocol is NUS-over-Serial-profile, not GATT custom. See spec "Pivot: niente GATT custom".
- **The advert name's leading char comes from the Flipper's device name, not the FAP.** `furi_hal_version_get_ble_local_device_name_ptr()[0]` is prepended. A Flipper named "Dlignone" advertises `DCRTRem Dlignone`. For discovery scripts, match the substring `CRTRem`, not the prefix.
- **`ufbt launch` requires the Flipper to be in qFlipper-detached state.** If qFlipper is running it grabs the USB CLI and the launch hangs. Quit qFlipper first.

## References

- Parent project: [../CLAUDE.md](../CLAUDE.md).
- Design spec (protocol contract): [../docs/superpowers/specs/2026-05-10-flipper-remote-design.md](../docs/superpowers/specs/2026-05-10-flipper-remote-design.md).
- Implementation plan: [../docs/superpowers/plans/2026-05-10-flipper-fap.md](../docs/superpowers/plans/2026-05-10-flipper-fap.md).
- Bridge counterpart (lives in sibling repo): `../../lodge-tools/services/crt-flipper-bridge/` + its own CLAUDE.md.
- Daemon HTTP control surface targeted by the bridge: `../crt/api.py`.

## Language

**Code comments and `FURI_LOG_*` messages are in English.** UI strings on the Flipper display follow the **parent app's Italian convention** for user-facing labels (e.g., `"Comandi"`, `"Elimina video"`, `"OK conferma"`, `"Back annulla"`) — the Flipper is the user's personal hardware in this deployment, not a generic shared device, so Italian labels match the rest of the system surface (TUI, daemon logs surfaced in UI).
