# crt_remote — Flipper FAP

Telecomando per crt-player via BLE Serial (profilo Flipper proprietario, fork
con MAC custom da Momentum FW — vedi `libs/serial_profile.{c,h}`).

## Build & flash

```bash
cd flipper_app
ufbt              # build → dist/crt_remote.fap
ufbt launch       # flash + start su Flipper connesso via USB
ufbt cli log      # leggi i log seriali del Flipper
```

## Uso

1. Sul Flipper: Apps → Tools → CRT Remote.
2. La app attiva il profilo BLE Serial con MAC derivato e advertise come
   `<X>CRTRem <NAME>` — dove `<X>` è il primo char del device name (es. `F`
   per "Flipper", quindi tipicamente `FCRTRem <NAME>`) e `<NAME>` è il nome
   del tuo Flipper (es. "Dlignone"). Il prefisso esatto dipende dal char
   iniziale del device — cerca la **substring `CRTRem`** per essere robusto.
3. Sull'homeserver: il `crt-flipper-bridge` (vedi repo `lodge-tools`) si
   connette al MAC della FAP. Questo MAC **non** è quello del menu Bluetooth
   del Flipper — è derivato dal MAC BLE base così (vedi `libs/serial_profile.c`):
   - byte 2 incrementato di 1
   - byte 0 XOR `(mac_xor & 0xFF)` (default `mac_xor = 0x0042` → byte 0 ^= `0x42`)
   - byte 1 XOR `((mac_xor >> 8) & 0xFF)` (default → byte 1 ^= `0x00`, no-op)
   - byte 3/4/5 invariati

   In pratica, col default `mac_xor` cambia solo byte 0 (^0x42) e byte 2 (+1).
   Scopri il MAC effettivo via `bluetoothctl scan on` cercando un device il cui
   nome contiene `CRTRem`. Vedi `services/crt-flipper-bridge/CLAUDE.md` nel repo
   lodge-tools per il pairing iniziale (Numeric Comparison).
4. Pulsanti: Up=next, Down=prev, OK=play/pause, Back-long=stop, Right=loop,
   Left=sync, OK-long=calibrate.
5. La riga di stato sul Flipper mostra solo lo stato BLE
   (starting/active/failed) — non lo stato del player. Il path RX dal bridge
   (status/last_result) è definito nella spec ma **non implementato in questa
   versione della FAP** (nessuna callback registrata e bridge in no-op).
   Re-abilitabile quando serve, vedi spec.

## Spec

`docs/superpowers/specs/2026-05-10-flipper-remote-design.md`.

## Plan implementativo

`docs/superpowers/plans/2026-05-10-flipper-fap.md`.
