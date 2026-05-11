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
   **"CRTRem Dlignone"** (sostituisci "Dlignone" col nome del tuo device).
3. Sull'homeserver: il `crt-flipper-bridge` (vedi repo `lodge-tools`) si
   connette al MAC della FAP. Questo MAC **non** è quello del menu Bluetooth
   del Flipper — è derivato (`base ^ mac_xor`, byte 2 incrementato; default
   `mac_xor = 0x0042`). Scoprilo via `bluetoothctl scan on` cercando il device
   con nome `CRTRem ...`. Vedi `services/crt-flipper-bridge/CLAUDE.md` nel
   repo lodge-tools per il pairing iniziale (Numeric Comparison).
4. Pulsanti: Up=next, Down=prev, OK=play/pause, Back-long=stop, Right=loop,
   Left=sync, OK-long=calibrate.
5. La riga di stato sul Flipper mostra solo lo stato BLE
   (starting/active/failed) — non lo stato del player (feedback RX
   disabilitato per limitazione del firmware, vedi spec).

## Spec

`docs/superpowers/specs/2026-05-10-flipper-remote-design.md`.

## Plan implementativo

`docs/superpowers/plans/2026-05-10-flipper-fap.md`.
