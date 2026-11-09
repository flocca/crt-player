# Schermata televideo per pausa e fermo

Data: 2026-11-08. Stato: approvata da Giacomo.

Sostituisce il menu in pausa descritto in [2026-11-07-pause-menu-design.md](2026-11-07-pause-menu-design.md) (anello di righe e comandi, velo all'85%, Sinistra/Destra = seek) e il nero dopo Stop. Tutto il resto di quella spec (pagina nostra su DashCast, posizione, chiusura dopo 30 minuti, calibrazione nella pagina, reinterpretazione degli endpoint) resta valido.

## Perché

Il menu in pausa è in produzione dal 2026-11-08 e funziona, ma Giacomo ci ha trovato tre limiti:

- **La lista e i comandi stanno sullo stesso anello**: per arrivare a `Sync` da un video servono fino a una decina di pressioni, e Sinistra/Destra sono sprecate per un seek che in pausa serve poco.
- **Dopo Stop il TV è nero**: non dice se è finita la coda, se il sync ha tolto il video o se il player è solo fermo, e da fermo Su/Giù fanno partire subito un video saltando in silenzio quelli non pronti, cioè il difetto che il menu in pausa doveva togliere.
- **Il menu è una pagina da monitor**: testo bianco fisso su un velo quasi opaco, poco legato al CRT su cui gira.

Vuole una schermata unica per pausa e fermo, creativa e adatta al CRT. Dopo tre consulenze (report in `~/.claude/state/handoff/crt-player/2026-11-08-pause-menu-v2/`) e una prova sul TV con una pagina usa e getta, ha scelto **un televideo**: la playlist come pagina 100 di un televideo privato, con dietro l'anteprima muta del video evidenziato.

## La prova sul TV (2026-11-08)

Pagina usa e getta servita dal Mac, aperta con DashCast sul Chromecast Ultra "TV in Ufficio", telemetria ogni 10 s. Risultati completi in `~/.claude/state/handoff/crt-player/2026-11-08-pause-menu-v2/tv-test-results.md`. Quelli che vincolano il progetto:

- **Due `<video>` caricati insieme non perdono fotogrammi**, ma **il Chromecast ignora `muted` sul secondo**: con il video in pausa caricato, alcune anteprime suonavano anche se la pagina le dava mute a volume zero. Con un solo `<video>` caricato il muto funziona. Le anteprime servite da una copia senza traccia audio (`ffmpeg -map 0:v -c copy -an`) sono state mute.
- **Un `<video>` nascosto lascia il suo ultimo fotogramma sul piano video**: tornando sul video in pausa si vedeva l'ultima immagine dell'anteprima precedente. Risolto scaricando l'anteprima (`removeAttribute("src")` + `load()`), non nascondendola.
- **Un video caricato e messo subito in pausa può non disegnare il fotogramma**: dietro il televideo restava nero finché il video non è partito un istante a volume zero ed è stato rimesso in pausa (nella stessa prova è cambiato anche il video, quindi la causa non è isolata; il rimedio non costa nulla).
- **La pagina gira a 24-30 fps**, non 60. Le animazioni CSS sopra un video vanno a scatti: lo zoom lento della fase notte sembrava uno scorrimento orizzontale a scatti. Niente animazioni sul video.
- **Il fuso del receiver è locale** (`Europe/Rome`): l'orologio si prende da `Date` nella pagina.
- **Effetti al cambio di riga rifiutati**: sia uno sfarfallio di 150 ms (impercettibile) sia neve piena + rotolamento verticale di 500 ms (non piaciuto). Il cambio è secco.
- **Impaginazione approvata**: solo i margini di calibrazione `CRT_MARGIN_*`, senza il margine di sicurezza 5%/8% del menu di oggi, più l'1,5% di margine a sinistra; 18 righe che riempiono l'altezza (font Bedstead a circa 35,5 px CSS); 40 colonne che riempiono la larghezza con un `letter-spacing` calcolato. Velo dietro il televideo: **70%** (30% e 55% illeggibili). Rosso e blu sul tubo: "vanno bene per ora".

Il codice della prova resta come riferimento di ciò che Giacomo ha visto (non è codice di prodotto): `/Users/flocca/.claude/state/handoff/crt-player/2026-11-08-pause-menu-v2/tvlab/page.html`, scene `ttxmixp` (pausa) e `ttxp` (fermo).

## Cosa si costruisce

Una schermata a pagina televideo, la stessa in due stati:

- **In pausa**: c'è un video caricato e fermo. La schermata compare a ogni pausa, come il menu di oggi.
- **Da fermo**: nessun video caricato. La schermata compare al posto del nero, da qualunque strada si arrivi (Stop, fine coda, sync, calibrazione, pagina riaperta, tasto a pagina chiusa).

Le due facce differiscono solo in quattro cose: cosa c'è dietro la riga del video in pausa, la riga di stato del player (riga 4), il comando `Stop` e il simbolo ▶. Tasti, aspetto e regole sono identici.

## Obiettivi

1. Schermata come descritta qui, in pausa e da fermo, con risposta ai tasti percepita come istantanea.
2. Dal divano: vedere tutta la playlist con lo stato di ogni video, scegliere un video, dare i comandi rari con Sinistra/Destra invece di scorrere fino in fondo.
3. Da fermo il TV dice perché si è fermato e cosa si può fare, e nessun tasto fa partire un video senza che sia stato scelto.
4. Tutto ciò che il menu di oggi garantisce resta: pausa e ripresa istantanee, posizione mai persa, logo DashCast raro, nessun cambio alla FAP e al bridge.

## Non obiettivi

- Pagina 200 "STATO" (messaggi d'errore dei download, coda, Chromecast, prossimo sync): dopo. Nella seconda riga dei comandi resta il posto per un futuro `Stato`.
- Etichette del menu Comandi della FAP (`flipper_app/crt_remote_app.c:51-55`: "Calibrate", "Toggle loop", "Sync now") e della home (`< -15s`, `> +30s`): restano. Si apre una issue da fare al prossimo riflash (`Calibra`, `Loop`, `Sync`, home con "< > comandi").
- Zapping a canali (concetto B delle consulenze): messo da parte.
- Cambiare cosa fanno i tasti durante la riproduzione (Su/Giù passano al video pronto adiacente come oggi, Sinistra/Destra `-15s`/`+30s`, OK mette in pausa).
- Rotazione automatica delle sottopagine o qualsiasi movimento da solo della schermata.

## Anti-obiettivi (sarebbe un fallimento anche se funziona)

- **Un'anteprima con l'audio.** Stop e pausa vogliono dire silenzio.
- **Il fotogramma sbagliato dietro il video in pausa** (residuo di un'anteprima precedente) o nero dove dovrebbe esserci il fotogramma. Fa eccezione il secondo circa di nero mentre la pagina ricarica il video in pausa dopo un'anteprima o la calibrazione (vedi "Anteprime"): con un solo decoder video non c'è altro modo.
- **La pausa che diventa lenta**: schermo nero, logo o ritardo percepibile fra pausa, schermata e ripresa.
- **Perdere la posizione** (issue #7, #12, #13): ripresa che riparte indietro o dall'inizio.
- **Cancellare senza conferma un video diverso da quello che si sta guardando.** Con la schermata aperta ogni `Elimina`, da TV o da Flipper, chiede conferma sulla riga evidenziata.
- **Un OK al buio che fa partire un video**: dalla fase notte il primo tasto sveglia e basta.
- **Movimento da solo**: la schermata cambia solo per un tasto, un cambio di libreria o la fase notte.
- **Effetti di transizione o animazioni sopra il video.**
- **Burn-in**: testo fisso a piena luminosità per mezz'ora sul tubo.
- **La schermata sul TV mentre il daemon crede altro, o viceversa** (anti-obiettivo della spec precedente, invariato).

## Vincoli

Quelli della spec precedente restano (Chromecast Ultra con Chrome 90, DashCast con `force=True`, una sola pagina statica servita da uvicorn su `:8765`, segnale 16:9 schiacciato in 4:3 dall'hardware, margini di overscan, deploy da `../homelab`). In più:

- **Geometria**: tutto quello che si disegna sta nel box largo 75% con `scaleX(1.3333)`. La griglia è di 40 colonne per 18 righe dentro i soli margini di calibrazione, più l'1,5% a sinistra (approvata sul TV).
- **Font**: Bedstead (ricostruzione del set teletext SAA5050), licenza CC0 dichiarata dall'autore; va citato nel repo. Da verificare che abbia tutti i glifi usati (▶ ○ ✓ … ! ▌ ▓ ░ ▄); per quelli che mancano, scegliere sostituti che si leggano.
- **Un solo decoder video** (scoperto nella prova sul TV del 2026-11-08): quando l'anteprima o la calibrazione caricano un file, il video principale resta nero e muto finché la pagina non lo ricarica. L'anteprima ha comunque un file senza traccia audio (vedi "Anteprime").
- **Prestazioni**: niente animazioni CSS sopra i video; la neve è l'unico elemento che si ridisegna di continuo e si ferma di notte.
- **Ridisegni frequenti**: la pipeline notifica a ogni punto percentuale di download e codifica (`crt/pipeline.py:297-299, 328-330` → `crt/daemon.py:129-131` → `refresh_menu`). La pagina deve applicare la vista senza ricaricare l'anteprima se video evidenziato e file non sono cambiati, e senza svegliare la fase notte.

## La pagina (decisa con Giacomo)

Estetica teletext generica (standard ETS 300 706, non un servizio reale): monospazio Bedstead su nero, pochi colori saturi, mosaico solo nella testata.

### Righe

| Riga | Contenuto |
|---|---|
| 1 | Testata: `P100` giallo, `CRT QUEUE` bianco, sottopagina `1/2` gialla, data e ora con secondi (`dom 08 nov 21:34:05`) allineate a destra. |
| 2 | Linea di mosaico blu (`▄` per 40 colonne). |
| 3 | `PLAYLIST` ciano a sinistra, `19 video` ciano a destra. |
| 4 | Riga di stato del player (vedi sotto). |
| 5-14 | Dieci righe di video. |
| 15 | Vuota. |
| 16 | Prima riga comandi: comandi sul video. |
| 17 | Seconda riga comandi: comandi del player. |
| 18 | Riga verde: stato del sistema, aiuto, avvisi brevi e conferma. |

### Righe dei video

`simbolo numero titolo destra`: simbolo, numero a due cifre (posizione nella playlist), titolo troncato con `…` per stare nelle colonne disponibili, a destra 8 colonne allineate a destra (erano 7: l'ottava serve a `-1:05:00`, deciso con Giacomo il 2026-11-08 dopo la prova sul TV).

- **Simboli**: ▶ solo sul video caricato (in pausa); ○ pronto; ✓ già visto; … in coda, in download o in codifica; ! errore. Oggi ▶ segue il cursore (`crt/pause_menu.py:36-37`): nella nuova schermata segue solo il video caricato, quindi da fermo nessuna riga lo porta.
- **Colonna destra** (decisa con Giacomo il 2026-11-08, sentito il consulente), in ordine di precedenza: la percentuale (`43%`) se in download o in codifica; per un video iniziato e non finito (`playback_position > 0`), compreso quello in pausa (▶), il tempo che manca col meno davanti (`-33:00`, `-1:05:00` sopra l'ora; `-00:00` se la posizione supera la durata); negli altri casi la durata (`09:12`, `1:05:30`), anche per i visti (in grigio come la riga), per quelli in coda appena la pipeline l'ha letta e per quelli in errore se un tentativo precedente l'aveva letta; vuota se la durata non è ancora nota. Formato `mm:ss`, `h:mm:ss` sopra l'ora, come la riga 4. Il tempo che manca è la differenza dei due valori già troncati al secondo, così la riga ▶ dice lo stesso della riga 4 (`00:52 / 09:12` → `-08:20`); sopra le 10 ore che mancano diventa `-11h59`, perché `-11:59:00` non sta in 8 colonne *(scelte di Claude)*.
- **Durata nella libreria** *(scelte di Claude)*: un campo `duration` per video, letto con il titolo prima del download (`fetch_title` ha già le informazioni di yt-dlp) e ricavato con ffprobe all'avvio per i video già in cache; la riga 4 prende la durata da lì invece che dal `<video>`.
- **Testo di stato per i non pronti**, nel titolo: `In coda · Titolo`, `Download · Titolo` (con `43%` a destra), `Codifica · Titolo`, `Errore nel video · Titolo`.
- **Colori**: pronto bianco; visto grigio; in coda, download, codifica ciano; errore magenta (il rosso come testo su CRT si legge male); riga evidenziata nero su giallo; riga in attesa di conferma `Elimina` nero su rosso.

### Sottopagine

Dieci video per sottopagina, pagine fisse come nel teletext (1-10, 11-20, ...). La sottopagina mostrata è quella della riga evidenziata, e `n/m` in testata dice dove si è. Nessuna rotazione automatica.

### Riga 4: stato del player

- **In pausa**: il video in pausa, sempre, anche se l'evidenziazione è su un'altra sottopagina: `▌▌ 12:34 / 45:00  ▓▓▓▓▓░░░░░░░░░` (barra di 14 colonne a blocchi). La durata viene dalla libreria (`QueueItem.duration`, vedi "Durata nella libreria"), e dal `<video>` della pagina finché la libreria non la conosce.
- **Da fermo**: l'avviso d'arrivo per 5 secondi (vedi "Arrivo"), poi vuota.
- **Playlist vuota**: `La playlist è vuota`, fisso.

### Riga 18: stato e aiuto

`Sync 21:10  Loop attivo   OK = Riprendi`, in verde.

- `Sync hh:mm` dall'ultimo `last_sync_at` (oggi arriva solo a `/status`, `crt/sync_engine.py:83`: va passato alla vista) convertito all'ora locale nella pagina; `Sync mai` se non c'è ancora stato un sync; `Sync errore` in magenta quando il sync è in errore (`state == "degraded"`, `crt/sync_engine.py:70-77`).
- `Loop attivo` solo quando il loop è attivo.
- `OK = <comando selezionato>` a destra.
- **Avvisi brevi** (2 secondi, poi la riga torna com'era): `Loop attivo`, `Loop disattivato`, `Sync avviato`, `Sync non disponibile`, `Non ancora pronto`, `Calibrazione non pronta`.
- **`Errore nel video`** (il `<video>` caricato ha dato errore): resta fino al primo tasto, come oggi (`crt/player_core.py:39`, `_on_media_error`).
- **Conferma di `Elimina`**: `Eliminare dalla playlist? OK = sì`, in rosso, finché arriva un tasto.

### Comandi

Due righe fisse, sempre nella stessa posizione:

- **Riga 16, comandi sul video evidenziato**: `Riprendi`/`Riproduci`, `Elimina`, e `Stop` solo in pausa.
- **Riga 17, comandi del player**: `Calibra`, `Loop`, `Sync`, `Spegni`.

Sinistra e Destra percorrono i comandi come un'unica sequenza (dall'ultimo della riga 16 si passa al primo della 17 e viceversa). La sequenza ricomincia dall'inizio dopo l'ultimo comando *(scelta di Claude: coerente con Su/Giù)*. `Stop` compare e scompare senza spostare la riga 17.

**Colori fissi per comando** (nella prova erano per posizione e scalavano quando mancava `Stop`: va corretto): `Riprendi`/`Riproduci` verde, `Elimina` rosso, `Stop` giallo, `Calibra` ciano, `Loop` magenta, `Sync` blu, `Spegni` bianco. Il comando selezionato è nero su fondo del suo colore, gli altri sono colorati su nero.

### Sfondo

Dietro la pagina c'è sempre qualcosa che dipende solo dalla riga evidenziata, sotto un **velo nero al 70%**:

| Riga evidenziata | Dietro |
|---|---|
| il video in pausa | il suo fotogramma vero (il `<video>` principale fermo) |
| un altro video pronto | la sua anteprima muta (vedi "Anteprime") |
| un video pronto la cui copia senza audio non c'è ancora | nero |
| un video in coda, in download, in codifica o in errore | neve attenuata dal velo |
| playlist vuota | nero |

Il cambio di riga è secco: nessuna transizione. Tornando sulla riga del video in pausa dopo un'anteprima c'è circa un secondo di nero, il tempo di ricaricarlo.

### Bozza

```
 IN PAUSA (07 caricato)                     DA FERMO (dopo Stop sul 07)
P100 CRT QUEUE  1/2  dom 08 nov 21:34:05   P100 CRT QUEUE  1/2  dom 08 nov 21:36:12
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄   ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
PLAYLIST                        19 video   PLAYLIST                        19 video
▌▌ 02:52 / 11:39  ▓▓▓▓░░░░░░░░░░
○ 01 Un trucco per evitare di …   -03:12   ○ 01 Un trucco per evitare di …   -03:12
✓ 02 Caracciolo a Otto e Mezzo…    12:05   ✓ 02 Caracciolo a Otto e Mezzo…    12:05
○ 03 Classifico le fonti di PR…   -07:40   ○ 03 Classifico le fonti di PR…   -07:40
○ 04 Binance $6 BILLION HACK (…   -01:51   ○ 04 Binance $6 BILLION HACK (…   -01:51
○ 05 Come leggere per imparare…    08:44   ○ 05 Come leggere per imparare…    08:44
○ 06 Generare l'icona di un'ap…   -03:37   ○ 06 Generare l'icona di un'ap…   -03:37
▶ 07 I DEM vogliono bloccare l…   -08:47   ○ 07 I DEM vogliono bloccare l…   -08:47   ← evidenziata (nero su giallo)
○ 08 Riesce Astra a capire qua…   -10:31   ○ 08 Riesce Astra a capire qua…   -10:31
○ 09 Caracciolo a Otto e Mezzo…   -02:08   ○ 09 Caracciolo a Otto e Mezzo…   -02:08
○ 10 Caracciolo a Repubblica: …  1:05:30   ○ 10 Caracciolo a Repubblica: …  1:05:30

Riprendi Elimina Stop                      Riprendi Elimina
Calibra Loop Sync Spegni                   Calibra Loop Sync Spegni
Sync 21:10  Loop attivo    OK = Riprendi   Sync 21:10  Loop attivo    OK = Riprendi
dietro: fotogramma fermo del 07            dietro: anteprima muta del 07
```

## Tasti (deciso con Giacomo)

La FAP e il bridge non cambiano: il daemon reinterpreta gli endpoint quando la schermata è a video, come già fa oggi in pausa.

| Tasto Flipper | Endpoint | Schermata a video (pausa o fermo) |
|---|---|---|
| Su / Giù | `prev` / `next` | spostano l'evidenziazione di una riga; dall'ultimo video si torna al primo e viceversa. Il comando selezionato torna al primo. |
| Sinistra / Destra | `seek/back/15` / `seek/forward/30` | spostano la selezione nei comandi. Il seek in pausa non esiste più. |
| OK | `toggle` | esegue il comando selezionato sulla riga evidenziata. |
| Comandi → Stop | `stop` | in pausa come il comando `Stop`; da fermo non fa nulla (`did_action=false`, nuovo `reason` `already_stopped`). |
| Comandi → Elimina video | `delete/current` | come OK su `Elimina`: chiede conferma sulla riga evidenziata. Un secondo `delete/current` mentre la conferma è aperta **annulla** (doppia pressione o ritentativo del bridge non devono cancellare). |
| Comandi → Calibrate, Toggle loop, Sync now | `calibrate`, `loop/toggle`, `sync` | come i comandi `Calibra`, `Loop`, `Sync` della schermata. |

Durante la riproduzione i tasti fanno quello che fanno oggi; `delete/current` resta immediato sul video in onda.

**Primo comando**: `Riprendi` se il video evidenziato ha una posizione salvata (`playback_position > 0`), `Riproduci` altrimenti; sul video in pausa è sempre `Riprendi`. È preselezionato all'apertura e torna selezionato a ogni cambio di riga, così pausa → OK riprende come oggi. Un video `done` ha posizione 0 (la fine naturale la azzera, `crt/player_core.py:628`) e quindi legge `Riproduci`.

### Cosa fa ogni comando

- **`Riprendi`/`Riproduci`**: sul video in pausa riprende. Su un altro video pronto salva la posizione del video in pausa (se c'è) e fa partire quello evidenziato dalla sua posizione salvata. Su un video non pronto mostra `Non ancora pronto` e non fa altro. La schermata sparisce quando parte il video.
- **`Elimina`**: sulla riga evidenziata, qualunque sia. La riga passa a nero su rosso e la riga 18 chiede `Eliminare dalla playlist? OK = sì`. Solo un OK (`toggle`) elimina (dalla cache locale e dalla playlist YouTube, come `delete_current` oggi); qualsiasi altro tasto annulla e basta, senza fare la sua azione *(scelta di Claude: oggi un Su/Giù annulla e sposta)*. Se la riga è il video in pausa, il player va prima da fermo. Se è un altro video, il video in pausa resta in pausa. Oggi esiste solo la cancellazione del cursore, che prima ferma la riproduzione (`crt/player_core.py:583-589`): serve un percorso per id. Questo rovescia la regola "Elimina agisce sempre sul video in pausa" della spec precedente, perché ora la conferma nomina la riga.
- **`Stop`** (solo in pausa): salva la posizione, scarica il video in pausa e passa da fermo. La schermata resta, con la stessa riga evidenziata, che ora mostra `Riprendi` e dietro la sua anteprima.
- **`Calibra`**: come oggi (`crt/player_core.py:766`). Alla fine (un tasto qualsiasi o 60 secondi) si torna alla schermata esattamente com'era, in pausa o da fermo, con la stessa evidenziazione. Il tasto che chiude la calibrazione non fa altro. Oggi la fine di una calibrazione partita da fermo va a nero (`crt/player_core.py:817-819`): il modello della schermata va conservato durante la calibrazione.
- **`Loop`** e **`Sync`**: eseguono e lasciano la schermata, con l'avviso breve nella riga 18.
- **`Spegni`**: in pausa e da fermo. Salva la posizione, chiude DashCast subito (quello che `_close_if_idle` fa dopo 30 minuti, `crt/player_core.py:925-942`) e il TV torna allo sfondo del Chromecast.

## Pagina chiusa (deciso con Giacomo, salvo dove indicato)

Quando DashCast non è aperto (dopo la chiusura per inattività, dopo `Spegni`, dopo che un'altra app ha preso il TV, all'avvio del daemon), i tasti di navigazione **aprono la pagina e mostrano la schermata da fermo**, applicando al modello l'azione del tasto. Si vede il logo DashCast una volta, all'apertura.

| Endpoint a pagina chiusa | Effetto |
|---|---|
| `prev` / `next` | apre la schermata con l'evidenziazione spostata di una riga a partire dal cursore |
| `seek/back/*` / `seek/forward/*` | apre la schermata con la selezione dei comandi spostata |
| `toggle` (OK) | se il video del cursore è pronto, lo fa partire dalla sua posizione come oggi; se non lo è, apre la schermata sul cursore con `Non ancora pronto`. Non salta più in avanti al primo video pronto, come fa oggi (`crt/player_core.py:390-399`). |
| `calibrate` | apre la pagina sulla calibrazione, come oggi (`crt/player_core.py:796-797`); alla fine, schermata da fermo |
| `delete/current` | apre la schermata da fermo sulla riga del cursore, già in attesa di conferma (riga rossa): niente cancellazioni alla cieca |
| `stop` | non fa nulla (`did_action=false`, `already_stopped`) |
| `loop/toggle`, `sync` | fanno il loro lavoro senza aprire la pagina: non tolgono il TV a un'altra app |
| `play/{video_id}` (Invio nella TUI) | apre la pagina e fa partire quel video *(scelta di Claude: è una scelta esplicita)* |

## Arrivo alla schermata da fermo (deciso con Giacomo)

| Da dove | Riga evidenziata | Avviso in riga 4 (5 s) |
|---|---|---|
| Stop (comando, byte del Flipper o TUI) | il video appena fermato | nessuno |
| Fine della coda (`crt/player_core.py:649-652`) | il primo video non `done`, in qualsiasi stato; se sono tutti visti, l'ultimo | `Fine della coda` |
| Il sync toglie il video in onda (`crt/player_core.py:552-555`) | il successore, dove il sync ha già spostato il cursore (`crt/sync_engine.py:183-191`) | `Video rimosso dalla playlist` |
| Fine di una calibrazione partita da fermo | quella di prima | nessuno |
| La pagina si riconnette da fermo (`crt/player_core.py:862-864`) | quella di prima, vista rimandata intera | nessuno |
| Tasto a pagina chiusa | il cursore, poi l'azione del tasto | nessuno |

## Anteprime (deciso con Giacomo, salvo dove indicato)

- **Copia senza audio per ogni video**, fatta dalla pipeline subito dopo la codifica (remux senza ricodifica, pochi secondi, circa il 40% del file codificato; su Chet la cache occupa 2,3 GB con 374 GB liberi). Per i video già in cache, la copia si fa all'avvio del daemon. Finché la copia manca, dietro la riga c'è nero, e OK fa comunque partire il video. La copia va trattata come il file codificato in tutte e tre le pulizie: `cleanup_temp_files` (altrimenti sparisce dopo 24 ore), la rimozione dal sync (`crt/sync_engine.py:177`) ed `Elimina` (`crt/player_core.py:605-612`). Le anteprime vengono sempre dalla copia, in pausa e da fermo *(scelta di Claude: una regola sola, e da fermo il muto funzionerebbe anche sul file normale)*.
- **Finestra**: i 20 secondi che precedono la posizione salvata, in loop; per un video senza posizione o già visto, i primi 20 secondi. Così l'anteprima ricorda dove si era rimasti e non anticipa niente. *(proposta da Fable, provata sul TV)*
- **Sempre muta**, anche via `muted` sull'elemento, oltre alla copia senza audio.
- **Una sola anteprima alla volta.** Quando la riga evidenziata non ne ha bisogno, l'anteprima viene **scaricata** (`src` tolto e `load()`), mai solo nascosta: altrimenti il suo ultimo fotogramma resta sul piano video.
- **Il video principale caricato in pausa** (riconnessione, ripresa da pagina appena aperta, ritorno dalla calibrazione, ritorno sulla sua riga dopo un'anteprima) parte un istante a volume zero e si rimette in pausa, così il Chromecast ne disegna il fotogramma. Il muto va messo prima di caricarlo: messo subito prima del play si sente un attimo di audio.
- **Dopo un'anteprima o la calibrazione il video principale ha perso il decoder**: la pagina lo ricarica dallo stesso file alla posizione della pausa quando si torna sulla sua riga, su Riprendi e alla riconnessione (circa un secondo di nero, poi il fotogramma).
- **Ridisegni**: l'anteprima si ricarica solo se cambiano il video evidenziato o il suo file.

## Fase notte (deciso con Giacomo)

Uguale in pausa e da fermo.

- Parte dopo **5 minuti senza tasti**. I ridisegni di libreria (percentuali, sync) non contano come tasti e non svegliano.
- Resta solo la **testata al 35% di luminosità**. L'orologio con i secondi continua ed è l'unico movimento. Testo e testata si spostano di qualche pixel ogni 30 secondi (nella prova: 3 px). Il resto della pagina sparisce.
- **Velo al 90%**. L'anteprima viene scaricata e la neve si ferma. Il video in pausa resta sotto il velo; se un'anteprima gli aveva preso il decoder, la pagina lo ricarica.
- **Il primo tasto di navigazione sveglia e basta** (`prev`, `next`, `toggle`, `seek/*`), senza eseguire la sua azione: al buio non si vede cosa è evidenziato. Da lì la schermata torna com'era, anteprima compresa. I comandi espliciti (menu Comandi del Flipper: `stop`, `delete/current`, `calibrate`, `loop/toggle`, `sync`; Invio nella TUI) svegliano **e** agiscono, perché chi li dà ha scelto sul display del Flipper o sul Mac, non al buio sul TV *(scelta di Claude)*. Poiché è il daemon a interpretare i tasti, il daemon deve sapere se la pagina è in fase notte.
- La chiusura per inattività resta a 30 minuti (`CRT_RECEIVER_IDLE_CLOSE_S`), contati **dall'ultimo tasto** sulla schermata e non più dall'ingresso in pausa o da fermo come oggi (`_inactive_since`, `crt/player_core.py:925-942`), così chi sta scorrendo la lista non si vede chiudere la pagina sotto gli occhi *(scelta di Claude)*. La notte dura quindi al massimo 25 minuti.

## Neve

Canvas piccolo (nella prova 160x90 con `image-rendering: pixelated`, circa 12 fotogrammi al secondo, grigi fra 40 e 160, mai bianco pieno) a tutto schermo sotto il velo. Si ridisegna solo mentre è a video e la schermata è sveglia.

## Cambi di libreria con la schermata a video

- **Percentuali e stati** si aggiornano da soli nelle righe.
- **Una riga che diventa pronta**: dietro, la neve lascia il posto all'anteprima, senza effetti.
- **Il sync toglie la riga evidenziata**: l'evidenziazione va sul cursore, o sulla prima riga se il cursore non c'è (come `PauseMenu._fix_selection`, `crt/pause_menu.py:62-70`), e la riga 4 mostra `Video rimosso dalla playlist` per 5 secondi.
- **Il file di un video pronto sparisce dal disco**: come oggi il video torna in coda (`crt/player_core.py:281-284`) e la riga torna ciano.
- **Il sync aggiunge video**: compaiono in fondo, il conteggio in riga 3 cresce.

## TUI e `/status` (deciso con Giacomo)

- Da fermo, Ctrl+N/Ctrl+B passano per gli stessi endpoint e quindi spostano l'evidenziazione sul TV invece di far partire un video, come i tasti del Flipper.
- La lista della TUI marca la riga evidenziata sul TV, accanto al `>>` del cursore (`tui_client/ui.py:91-94`).
- **Invio su una riga della lista fa partire quel video** con `/control/play/{video_id}` (`crt/api.py:134`; il client c'è già, `tui_client/data_provider.py:43-46`, senza chiamanti).
- `/status.player` conserva `menu_open`, che ora è vero ogni volta che la schermata è a video (anche da fermo; nessuno lo legge oggi), e aggiunge `screen: "none" | "paused" | "stopped"` e `selected_video_id`. Nessun campo esistente sparisce (`crt/api.py:33`).

## Cosa cambia nel daemon e nella pagina (indicazioni, non prescrizioni)

- `PauseMenu` (`crt/pause_menu.py`) diventa il modello della schermata nei due stati: video da una parte, comandi dall'altra, con il video caricato (o nessuno) come dato. Le guardie `self.state == "paused" and self.menu is not None` in `next`/`prev`/`toggle`/`seek_relative` si allargano alla schermata da fermo. `_blank()` lascia il posto all'invio della schermata da fermo, con l'avviso d'arrivo passato da chi chiama.
- I comandi di trasporto restano senza sospensioni (gotcha nel `CLAUDE.md`): anche la cancellazione per id fa la parte lenta altrove, come `delete_current`.
- Ogni nuova sottoscrizione SSE riceve la schermata intera (regola già in vigore, `_on_receiver_connect`).
- La pagina (`crt/receiver_page/index.html`) perde il menu di oggi e guadagna il televideo, il secondo `<video>` per le anteprime e la neve; la calibrazione resta com'è.
- I test di `PauseMenu` e di `PlayerCore` sulla pausa, su Stop (`test_stop_blanks_page_*`) e sui tasti da fermo (`test_toggle_when_idle_*`) vanno riscritti sulle nuove regole.
- `CLAUDE.md` va aggiornato (menu in pausa, Stop e nero, seek in pausa, anteprime e copie senza audio, gotchas del Chromecast trovati nella prova).
- Issue esistenti: #20 (fotogramma che non si ridisegna dopo un seek in pausa) decade, perché il seek in pausa sparisce; #21 (`Cache-Control: no-cache` su `/media`) vale anche per le copie delle anteprime; #22 (checklist di verifiche) resta valida.

## Lasciato a chi costruisce

- Formato dei messaggi daemon → pagina (un tipo per la schermata che porta tutta la vista, o estensione di `menu`), e quali dati calcola il daemon (righe, simboli, colori) e quali la pagina (orologio, ora locale del sync, durata, impaginazione).
- Dove vive il timer della fase notte, purché il daemon sappia se la pagina è in notte quando arriva un tasto.
- Nome e posizione delle copie senza audio, e come la pipeline le recupera all'avvio senza rallentare la coda.
- Come includere il font (inline in `index.html` o route statica) e la nota di licenza.
- Sostituti per i glifi che Bedstead non ha.
- Come la pagina calcola font, altezza di riga e `letter-spacing` per riempire 40x18 (la prova ha un modo che funziona).

## Issue collegate

- #23: etichette della FAP allineate alla TV al prossimo riflash (vedi "Non obiettivi").
