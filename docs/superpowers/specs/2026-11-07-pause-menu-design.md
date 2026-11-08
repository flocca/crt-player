# Menu in pausa sul TV, navigabile col Flipper

Data: 2026-11-07. Stato: design in revisione.

## Perché

Dal divano oggi Giacomo controlla il CRT col Flipper un passo alla volta: prev/next si spostano di un video e saltano in silenzio quelli non pronti, i comandi rari stanno in un menu sul display minuscolo del Flipper, e il Flipper non riceve niente dal daemon (non sa se un comando ha avuto effetto). Vuole che, quando mette in pausa, sul TV compaia un menu che mostri dove si trova nella playlist, gli permetta di scegliere un altro video e di dare i comandi, il tutto navigando col Flipper.

## Cosa si vede e come si usa (deciso con Giacomo)

- **Quando compare:** a ogni pausa, da qualunque origine arrivi (Flipper, TUI). Si chiude riprendendo o scegliendo una voce.
- **Disposizione:** a tutto schermo (bozza "A"). Velo scuro sopra il fotogramma in pausa, che si intravede. In alto titolo del video in pausa, minutaggio `mm:ss / mm:ss` e barra di avanzamento; al centro le righe della playlist intorno al video in pausa, ognuna con un simbolo di stato (▶ in pausa, ○ pronto, ✓ già visto, … in coda/download/codifica con percentuale se disponibile, ! errore; simboli: scelta di Claude); in fondo i comandi: `Stop`, `Elimina video`, `Calibrate`, `Toggle loop`, `Sync now`. Testo grande e in grassetto: lo schermo è piccolo. La bozza navigabile usata nella prova lampo è riprodotta in fondo a questo documento.
- **Tasti mentre il menu è aperto** (la FAP non cambia; è il daemon a reinterpretare gli endpoint):
  - Su (`/control/prev`) e Giù (`/control/next`) spostano l'evidenziazione, che all'apertura parte dal video in pausa.
  - OK (`/control/toggle`) sceglie la voce evidenziata. Sul video in pausa = riprende, quindi OK-OK resta pausa/ripresa come oggi. Su un altro video = fa partire quello. Su un comando = lo esegue.
  - Sinistra e Destra restano `-15s`/`+30s` sul video in pausa; il minutaggio in testa al menu si aggiorna.
  - Tenere premuto OK apre ancora il menu Comandi sul Flipper, che resta com'è.
- **`Elimina video` agisce sempre sul video in pausa**, mai sulla riga evidenziata, e **sul TV chiede conferma**: dopo OK compare "Confermi? OK sì"; un secondo OK elimina, qualsiasi altro tasto annulla. Dal menu Comandi del Flipper resta senza conferma come oggi (toglie il video anche dalla playlist YouTube).
- **Come viene disegnato:** una pagina web nostra, aperta sul Chromecast tramite **DashCast**, che contiene sia il video sia il menu. Niente iscrizione Google Cast da 5 dollari, niente hosting esterno.

## Prova lampo già fatta (2026-11-07)

Pagina statica servita dal Mac sulla LAN, aperta sul Chromecast del CRT con `pychromecast.controllers.dashcast.DashCastController.load_url`. Risultati verificati da Giacomo davanti al TV:

- Senza `force` DashCast carica la pagina in un iframe dentro il proprio guscio HTTPS e la pagina HTTP non arriva mai (contenuto misto bloccato). **Con `force=True` funziona**: il receiver naviga direttamente alla pagina.
- All'avvio dell'app DashCast mostra per qualche secondo il proprio logo con una barra "sto aprendo <url>". Dipende dall'avvio dell'app, non da `force`.
- La pagina ha aperto un canale Server-Sent Events verso il server e mandato le conferme via POST: 26-41 ms fra comando e conferma. Menu, frecce e ripresa percepiti come istantanei.
- `<video>` con MP4 H.264 1024x576 servito in HTTP dalla LAN con supporto Range: riproduzione, pausa, seek a video fermo, ripresa dal punto giusto senza caricamenti.
- Il menu sovrapposto, allargato di 4/3 in orizzontale (`transform: scaleX(1.3333)` su un contenitore largo 75%) per compensare lo schiacciamento 16:9→4:3 dell'hardware di Giacomo, si legge bene ed è proporzionato.
- Ambiente del receiver: user agent `Chrome/90.0.4430.225 CrKey/1.56.500000 DeviceType/Chromecast`, viewport 1280x720 CSS px, `devicePixelRatio` 1.5.

Il codice della prova (usa e getta) era: server Python con `/events` (SSE), `/cmd/<nome>`, `/ack`, `/report` e servizio MP4 con Range; pagina con `<video>` a tutto schermo (`object-fit: fill`) e menu in un `div` sovrapposto.

## Obiettivi

1. Menu in pausa come descritto sopra, con risposta ai tasti percepita come istantanea.
2. Pausa, ripresa, seek, prev/next, scelta di un video e fine naturale del video funzionano almeno come oggi, inclusa la ripresa dal punto salvato dopo uno Stop o un riavvio del daemon.
3. Il logo DashCast compare raramente: solo quando l'app va riaperta.
4. Nessuna modifica alla FAP del Flipper né al bridge (`../homelab/services/crt-flipper-bridge/bridge.py`, `COMMAND_TABLE` invariata).

## Non obiettivi

- Canale di ritorno verso il Flipper (frame RX `last_result`/`status` della spec v1): resta fuori.
- Indicazioni a schermo durante la riproduzione (titolo, avanzamento): la nuova architettura le renderebbe possibili, ma non fanno parte di questo lavoro.
- Receiver Google Cast registrato (5 dollari): scartato per ora; la pagina va scritta in modo che passare a un receiver nostro voglia dire cambiare solo dove è ospitata.
- Cambiare il menu Comandi del Flipper o la sua mappa tasti.
- Supporto a più Chromecast.
- Controllo da Google Home, dalla notifica del telefono o da altre app Cast: con DashCast non c'è una sessione media Cast e questi controlli spariscono. Giacomo non li usa: non è una regressione da compensare.

## Anti-obiettivi (sarebbe un fallimento anche se funziona)

- La pausa che diventa lenta: comparsa del menu o ripresa con ritardi percepibili, schermo nero o logo DashCast fra pausa e ripresa.
- Perdere la posizione: ripresa che riparte indietro o dall'inizio dopo pausa, Stop, riavvio del daemon o chiusura dell'app (sono i bug già risolti nelle issue #7, #12, #13 col player di Google: non devono tornare).
- `Elimina video` che colpisce un video diverso da quello in pausa.
- Il menu rimasto sul TV mentre il daemon crede di essere in un altro stato (o viceversa).
- Testo illeggibile o tagliato sul CRT perché disegnato per un monitor.
- Due percorsi di riproduzione da mantenere in parallelo (player di Google e pagina nostra).

## Vincoli

- **Hardware:** il CRT usa il Chromecast "TV in Ufficio", un **Chromecast Ultra**: niente Android, solo app Cast. Browser del receiver: Chrome 90, quindi niente funzioni web più recenti (niente `:has()`, container query, `structuredClone`, ecc.).
- **DashCast:** app ID `84912283`, receiver pubblico di terzi, avviato da pychromecast con `force=True`. Ogni `load_url` riavvia l'app (logo). La pagina va caricata una volta per sessione e tutto il resto deve avvenire al suo interno.
- **Rete:** il daemon gira in Docker su Chet (`network_mode: host`, porta 8765 raggiungibile dalla LAN 192.168.178.0/24, come già oggi per gli MP4). La pagina e i video sono serviti in HTTP dallo stesso uvicorn su `:8765`, all'indirizzo LAN scelto da `_get_local_ip()`. Il Chromecast non è sulla tailnet; Chet non ha slot `tailscale serve` liberi.
- **Geometria:** il segnale è 16:9 e l'hardware lo schiaccia in 4:3. Il video è già pre-allargato dall'encoder; ciò che la pagina disegna sopra va allargato di 4/3 in orizzontale. Il menu deve stare dentro i margini di overscan calibrati (`CRT_MARGIN_*`).
- **Deploy:** `lodge crt-player update` dal repo `../homelab` (fa `git reset --hard origin/main` su Chet): il codice va pushato su `main` prima.
- Lingua dell'interfaccia sul TV: italiano, come TUI e FAP.

## Comportamento atteso (deciso con Giacomo, salvo dove indicato)

- **Apertura dell'app:** il daemon apre DashCast sulla pagina al primo play quando l'app non è già aperta. Da lì in poi pausa, menu, cambio video, seek, fine video e calibrazione avvengono dentro la pagina, senza un nuovo `load_url`.
- **App tenuta aperta:** dopo uno Stop la pagina resta aperta e mostra uno schermo nero, così il play successivo è immediato e senza logo.
- **Chiusura per inattività:** dopo 30 minuti senza riproduzione (fermo o in pausa) il daemon salva la posizione e chiude l'app (`quit_app`), come fa oggi a fine sessione. Il play successivo riapre DashCast (logo) e riprende dal punto salvato. *(30 minuti confermati da Giacomo; va bene renderlo configurabile con una variabile d'ambiente.)*
- **Riavvio del daemon** (es. `lodge crt-player update`): come oggi, lo spegnimento ordinato salva la posizione e chiude l'app; al primo play dopo il riavvio ricompare il logo. Niente ripresa dello stato dalla pagina rimasta aperta. *(scelta di Claude)*
- **Navigazione nel menu:** *(confermata da Giacomo: con 19 video servono fino a ~10 pressioni per arrivare ai comandi, va bene perché i comandi veloci restano sul Flipper)* l'evidenziazione percorre un anello: le righe della playlist in ordine, poi i cinque comandi, poi di nuovo la prima riga; Su dal primo video porta all'ultimo comando. La lista mostra una finestra di righe che scorre seguendo l'evidenziazione. I comandi restano disegnati su una riga in fondo e l'evidenziazione si sposta di parola in parola.
- **Quali righe si possono scegliere:** i video `ready` (○) e quelli già visti `done` (✓, che `prepare_for_play` riporta a `ready` se la cache c'è, altrimenti li rimette in coda come oggi). OK su una riga in coda, in download/codifica o in errore non fa niente e mostra per un paio di secondi "non ancora pronto". *(scelta di Claude)*
- **Comandi dal menu:** `Stop` ferma (schermo nero, posizione salvata); `Elimina video` (dopo la conferma) fa quello che fa oggi `/control/delete/current` sul video in pausa; `Calibrate` mostra il pattern di calibrazione dentro la pagina; `Toggle loop` e `Sync now` eseguono e lasciano il menu aperto, con l'esito visibile (stato del loop, sync avviato). *(comportamento dopo ogni comando: scelta di Claude)*
- **Calibrazione:** *(scelta di Claude)* è uno stato a sé, non un video della playlist: la sua fine non segna niente come `done` e la sua durata non tocca la posizione del video. Se parte mentre un video sta andando (Comandi del Flipper o `Ctrl+T` nella TUI), prima il video va in pausa con la posizione salvata. Il pattern resta finché arriva un tasto qualsiasi (prev, next, toggle, seek) o per 60 secondi, poi si torna al menu in pausa sul video di prima. Il tasto che chiude la calibrazione non fa altro.
- **Pausa dalla TUI:** si comporta come quella dal Flipper (il menu compare). Gli altri comandi della TUI, mentre il menu è aperto, hanno la stessa reinterpretazione degli endpoint. La TUI in sé non cambia. *(scelta di Claude)*
- **Un'altra app prende il Chromecast** (qualcuno ci trasmette da un telefono): il daemon se ne accorge, salva la posizione e si comporta come dopo una chiusura per inattività.

## Cosa cambia nel daemon (indicazioni, non prescrizioni)

- Oggi la riproduzione passa dal Default Media Receiver: `ChromecastManager.cast_url` (`play_media`), pausa/seek via `media_controller`, posizione da `live_current_time` (`adjusted_current_time`), fine naturale da `idle_reason == "FINISHED"`, sessione persa da `is_session_lost`. Con la pagina, queste informazioni arrivano dalla pagina stessa: è la parte più grossa del lavoro.
- pychromecast resta per discovery, avvio di DashCast, `quit_app` e per accorgersi che l'app attiva non è più DashCast.
- Le regole già scritte nel `CLAUDE.md` del repo su posizione e ripresa (snapshot su stop, salvataggio periodico ogni `CRT_SAVE_INTERVAL_S`, flush allo spegnimento, ripresa da `playback_position`) restano valide: cambia solo da dove si legge la posizione.
- La calibrazione oggi è una clip MP4 da 60 s mandata al player di Google: alla fine viene scambiata per la fine del video in pausa (che viene segnato `done` e si passa al successivo) e il salvataggio periodico scrive sul video la posizione della clip. Spostandola dentro la pagina questi due difetti non devono ripresentarsi.
- `/status` deve continuare a dare stato, video e posizione corretti alla TUI.
- Test: `tests/test_player_core.py`, `tests/test_chromecast*`/integrazione vanno adeguati; la pagina non ha test automatici oggi.

## Lasciato a chi costruisce

- Protocollo fra pagina e daemon (SSE + POST come nella prova, WebSocket, altro), formato dei messaggi, frequenza dei rapporti di posizione.
- Come il daemon tiene lo stato "menu aperto" e la posizione dell'evidenziazione, e chi disegna cosa (la pagina riceve lo stato completo o solo eventi).
- Come la pagina rispetta i margini di overscan e quante righe di playlist mostra (indicazione: circa 6-7 righe ben leggibili, come nella prova).
- Come si comporta la pagina se perde il canale col daemon (indicazione: riprovare a riconnettersi da sola).
- Se tenere il codice della pagina in un file statico nel pacchetto `crt/` o generarlo.
- Aggiornamento di `CLAUDE.md` (sezioni architettura, gotchas sul player di Google che non valgono più) a fine lavoro.

## Bozza della disposizione

```
┌────────────────────────────────────────────┐
│ ▶ Titolo del video in pausa                │
│ 12:34 / 32:50                              │
│ ████████████░░░░░░░░░░░░░░░░░░░░░░         │
│   ✓ Video già visto prima                  │
│   ✓ Un altro video già visto               │
│ ▌▶ Titolo del video in pausa             ▐ │  ← evidenziato all'apertura
│   ○ Prossimo video, pronto                 │
│   … Video in download 43%                  │
│   ! Video con errore                       │
│ ────────────────────────────────────────── │
│ Stop · Elimina video · Calibrate · Toggle loop · Sync now
└────────────────────────────────────────────┘
       (velo scuro sopra il fotogramma in pausa)
```
