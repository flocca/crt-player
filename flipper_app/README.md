# crt_remote — Flipper FAP

Telecomando per crt-player via BLE Nordic UART (NUS).

## Build & flash

```bash
cd flipper_app
ufbt              # build → dist/crt_remote.fap
ufbt launch       # flash + start su Flipper connesso via USB
ufbt cli log      # leggi i log seriali del Flipper
```

## Uso

1. Sul Flipper: Apps → Tools → CRT Remote.
2. La app attiva il profilo BLE Serial (NUS).
3. Sull'homeserver: il `crt-flipper-bridge` (vedi repo `lodge-tools`) si connette automaticamente al MAC del Flipper.
4. Pulsanti: Up=next, Down=prev, OK=play/pause, Back-long=stop, Right=loop, Left=sync, OK-long=calibrate.
5. La riga di stato mostra lo stato del player.

## Spec

`docs/superpowers/specs/2026-05-10-flipper-remote-design.md`.

## Plan implementativo

`docs/superpowers/plans/2026-05-10-flipper-fap.md`.
