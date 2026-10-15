# "Guarda più tardi" al posto di CRT Queue: ricerca (idea abbandonata)

**Data:** 2026-10-15
**Stato:** idea abbandonata il 2026-10-15, dopo questa ricerca. Nessun codice scritto.
**Scope:** sincronizzare la playlist di sistema "Guarda più tardi" (Watch Later, ID `WL`) al posto della playlist "CRT Queue" indicata da `CRT_YT_PLAYLIST_ID`. Il documento serve a non ripercorrere la stessa strada: dice cosa non funziona, perché, e quali strade restano aperte.

Le conclusioni vengono da cinque filoni di ricerca (API ufficiale, lettura con yt-dlp, rimozione dei video, impatto sul codice, comodità d'uso). Ogni filone è stato poi verificato da un secondo agente incaricato di smentirlo; le correzioni sono già incorporate qui. Nessuna richiesta è stata fatta con l'account YouTube di Giacomo: tutto ciò che richiede la sessione autenticata è segnato come non verificato.

## In breve

- L'API YouTube Data v3, quella usata da `crt/youtube_client.py`, non legge e non modifica "Guarda più tardi". È così almeno da inizio 2021, e niente è cambiato fino a settembre 2026.
- **Non impostare mai `CRT_YT_PLAYLIST_ID=WL`.** Se l'API risponde con una lista vuota, il sync svuota la libreria e cancella la cache (dettagli in [Cosa non fare](#cosa-non-fare)).
- L'unica strada tecnica sono i cookie di una sessione YouTube dell'account: yt-dlp per leggere la playlist, un'API interna non documentata di YouTube (InnerTube) per togliere i video. Funziona, ma:
  - mette a rischio l'account principale, perché "Guarda più tardi" sta lì e non si può usare un account usa e getta;
  - i cookie scadono, con segnalazioni che vanno da poche ore a circa un mese;
  - servono protezioni contro liste vuote o troncate, avvisi quando la sessione muore e modifiche al deploy.
- Il guadagno in comodità è piccolo: nell'app YouTube il pulsante "Salva" sotto il video salva già con un tocco nell'ultima playlist usata.
- La ricerca conferma la decisione A2 della spec [2026-04-21-headless-sync-daemon-design.md](./2026-04-21-headless-sync-daemon-design.md): "playlist dedicata invece che alla Watch Later. Niente cookie, niente rischio captcha".

## Cosa non fare

### Impostare `WL` come ID della playlist

Con il codice attuale l'effetto dipende da cosa risponde l'API, e la documentazione si contraddice su questo punto:

| Risposta di `playlistItems.list?playlistId=WL` | Effetto su crt-player |
|---|---|
| 200 con lista vuota (comportamento descritto dalla documentazione del 2016 e da segnalazioni fino al 2026) | `SyncEngine._apply_diff` considera tolti tutti i video (`crt/sync_engine.py:86-87`). Per ognuno `_remove_item` (`:157`) ferma la riproduzione se è in onda, annulla la codifica, cancella file codificato e sorgente (`_delete_cache_files`, `:214`) e sposta o azzera il cursore. **Libreria e cache spariscono al primo sync.** |
| 401/403 (`watchLaterNotAccessible`, ancora nella documentazione di riferimento) | `crt/youtube_client.py:39` lo trasforma in `YouTubeAuthError`: sync `degraded` con un messaggio fuorviante di errore di autenticazione. |
| 400 (`playlistOperationUnsupported`) o 404 (`playlistNotFound`, visto in passato) | Eccezione generica (`crt/sync_engine.py:69`): sync `degraded`, nessuna cancellazione. |

Quale risposta arrivi oggi non è stato verificato, e non vale la pena scoprirlo.

### Fidarsi di codice o articoli che "funzionavano" con WL

Fino a dicembre 2020 alcuni client leggevano "Guarda più tardi" tramite l'API con dei trucchi. Il componente aggiuntivo YouTube per Kodi, per esempio, usava l'ID `" WL"` o un ID personale ricavato da youtube.com/tv. A metà dicembre 2020 hanno iniziato a fallire con `404 playlistNotFound`, e dal 9-10 febbraio 2021 anche l'inserimento fallisce. Qualsiasi esempio precedente al 2021 non prova niente sul presente.

### Usare il client OAuth di crt-player per chiamare InnerTube

È un'idea che viene spontanea: abbiamo già un token OAuth con scope `youtube`, lo si potrebbe mandare all'API interna. Però:

- le YouTube API Services Developer Policies vietano le API non documentate (III.D.7, "You must not use undocumented APIs without express permission") e le credenziali usate fuori dal proprio progetto (III.D.1.b). Si rischia il progetto GCP, non solo l'account;
- nel 2026 Google sta chiudendo l'uso di OAuth su InnerTube (vedi [Rimozione](#rimozione-dei-video-comando-controldeletecurrent)).

## L'API ufficiale

### Cronologia

| Data | Cosa | Fonte |
|---|---|---|
| 2015-03-11 | I nuovi elementi di "Guarda più tardi" vengono aggiunti in fondo alla lista (dal più vecchio al più nuovo), al contrario di caricamenti, like e cronologia. | revision history |
| 2016-08-11 | Annuncio: dal 12 settembre 2016 `playlists.list` e `playlistItems.list` per la cronologia e "Guarda più tardi" restituiranno liste vuote, sia con `HL`/`WL` sia con gli ID salvati prima. | revision history |
| 2016-09-15 | "now return empty lists"; `relatedPlaylists.watchLater` contiene sempre il valore fisso `WL`. | revision history |
| 2020-09-09 | Il supporto di `playlistItems.insert` e `playlistItems.delete` per queste playlist è "fully deprecated"; `relatedPlaylists.watchLater` sparisce dalla documentazione. | revision history |
| dic 2020 | I trucchi dei client terzi smettono di funzionare (`404 playlistNotFound`). | anxdpanic/plugin.video.youtube#69 |
| 2021-01-28 | Gli errori `watchLaterNotAccessible` vengono tolti dalla documentazione: "users' watch history and watch later lists are, indeed, not accessible via the API, these particular errors are not returned by the API". | revision history |
| 2021-02-09/11 | L'inserimento in WL fallisce con `400 playlistOperationUnsupported` ("The API does not support the ability to insert videos into the specified playlist."); la lettura restituisce una lista vuota anche se la playlist ha video. | Stack Overflow Q66156461, lambdasoup/watchlater#53 |
| 2026-09-14 | Le pagine di riferimento di `playlistItems.list` e `playlists.list` elencano ancora `403 watchLaterNotAccessible` e `400 playlistOperationUnsupported` ("you can't list a video in your watch later playlist"). | docs/playlistItems/list |
| fino al 2026-09-30 | Nessuna voce della revision history ripristina l'accesso. | revision history, letta per intero il 2026-10-15 |

La documentazione macchina (`youtube.v3.json`, revisione 20260430, nel pacchetto `google-api-python-client`) ha ancora un campo `relatedPlaylists.watchLater` marcato deprecato, con una descrizione vecchia che dice che insert e delete funzionano. È probabilmente l'origine delle affermazioni "funziona ancora" che si trovano in giro.

### Altri canali ufficiali di Google

Nessuno si presta a un controllo ogni pochi minuti:

- **Data Portability API:** disponibile in Italia e nell'UE, ma al massimo un'esportazione ogni 24 ore (`FAILED_PRECONDITION` se si chiede prima), con tempi da minuti a giorni. Un'app in stato "Testing" ha accesso per soli 7 giorni. Lo scope `dataportability.youtube.private_playlists` esporta le playlist private, ma la documentazione non nomina mai "Guarda più tardi".
- **Google Takeout:** esporta "Guarda più tardi" come `Watch later-videos.csv` (colonne Video ID e data di aggiunta, secondo parser di terze parti). Non ha un'API, e le esportazioni programmate partono ogni 2 mesi.
- **YouTube Analytics API:** dà solo numeri aggregati sui video del proprio canale, non il contenuto della playlist di chi guarda.

## La strada con i cookie: lettura con yt-dlp

### Come funziona

- `:ytwatchlater` è `YoutubeWatchLaterIE` (`yt_dlp/extractor/youtube/_redirect.py`, descrizione "(requires cookies)"). Si limita a rimandare a `https://www.youtube.com/playlist?list=WL`, che `YoutubeTabIE` tratta come una playlist qualsiasi.
- Senza cookie validi YouTube risponde con un avviso e yt-dlp solleva `ERROR: [youtube:tab] WL: YouTube said: The playlist does not exist.` (riprodotto il 2026-10-15 con yt-dlp 2026.08.19, senza cookie). Non c'è un messaggio esplicito di login richiesto.
- **Elenco senza download, da Python:** `{'extract_flat': 'in_playlist', 'cookiefile': ...}`.
  - Ogni voce ha `id`, `title`, `duration`, `channel`, `live_status` e altro.
  - Non c'è `playlist_index`: la posizione è l'ordine della lista.
  - Non c'è nemmeno l'ID dell'elemento nella playlist (né `setVideoId` né l'equivalente del `playlistItem id` dell'API), quindi non c'è niente da usare per toglierlo.
- **Costo:**
  - 2 richieste fino a 100 video, 3 fino a 200 (1 e 2 con `compat_opts={'no-youtube-unavailable-videos'}`);
  - nessuna chiamata al player, quindi niente runtime JavaScript né PO token;
  - circa 1 secondo dal Mac su una playlist pubblica di 162 video;
  - una pagina di circa 1,5 MB, 0,3 MB compressi.
- **Versione minima:** yt-dlp prima della 2026.07.04 può fermarsi ai primi 100 video (issue #16692, corretta dalla PR #16948 del 2026-06-13). La copia nel `.venv` locale (2026.03.17) ha il difetto; la produzione (2026.08.19) no.
- **Elenco troncato senza errore:** se una pagina successiva risponde 3 volte "Incomplete data received", l'elenco torna parziale con un semplice WARNING. Per il sync sarebbe una rimozione di massa. Le difese sono `extractor_args={'youtube': {'raise_incomplete_data': ['true']}}` e il confronto del numero di voci con `playlist_count`.

### Ordine dei video

L'unica fonte ufficiale (revision history, 2015-03-11) dice che i nuovi video finiscono in fondo, come succede oggi con CRT Queue. In quel caso l'autoplay andrebbe avanti verso i video nuovi. La fonte ha 11 anni e non è stata verificata sull'account.

Se invece la lista arrivasse dal più nuovo al più vecchio, i video nuovi comparirebbero sopra il cursore. L'autoplay andrebbe allora verso quelli più vecchi, e i nuovi si raggiungerebbero solo con "prev", con il loop o con `play/{id}`.

### I cookie

- **Formato:** cookies.txt in formato Netscape; un'esportazione in JSON viene rifiutata. yt-dlp considera la sessione autenticata solo se c'è `LOGIN_INFO` insieme ad almeno uno tra `SAPISID`, `__Secure-1PAPISID` e `__Secure-3PAPISID`.
- **Esportazione** (wiki di yt-dlp, "Exporting YouTube cookies"):
  1. aprire una finestra privata e accedere a YouTube;
  2. nella stessa scheda, l'unica aperta, andare su `https://www.youtube.com/robots.txt`;
  3. esportare i cookie di youtube.com;
  4. chiudere la finestra e non riaprire più quella sessione in un browser.

  YouTube infatti ruota i cookie delle sessioni aperte in una scheda.
- **Durata:** solo segnalazioni aneddotiche:
  - circa un mese, prima di metà 2025;
  - 3-5 giorni anche con la finestra privata (yt-dlp#13964, agosto 2025);
  - 1-2 ore se la sessione resta in uso nel browser (#12009);
  - circa 2 settimane (articolo su dev.to, marzo 2026);
  - "poche ore" (ytmusicapi#813, gennaio 2026).

  Una nota tecnica del 2026 (ALERTua/youtube_automanager) aggiunge che il cookie `PSIDTS` è legato all'IP e che i cookie con DBSC di Chrome durano ore. Bisognerebbe quindi esportare da Firefox e dallo stesso IP pubblico di Chet. È una fonte sola.
- **Quando muoiono:** yt-dlp avvisa "The provided YouTube account cookies are no longer valid", poi la richiesta fallisce come ospite con "The playlist does not exist". Prima di diventare un errore, la sessione morente può rispondere come anonima.
- **Gesti dell'utente che uccidono la sessione:** cambio password ed "Esci da tutte le sessioni" nelle impostazioni dell'account Google.

### Il file dei cookie e il montaggio in sola lettura

- `YoutubeDL.close()`, chiamato anche all'uscita da un blocco `with`, riscrive **sempre** il file `cookiefile` con `open(path, 'w')`, senza gestire errori.
  - La cartella `secrets` è montata in sola lettura, quindi ogni elenco solleverebbe `OSError` (EROFS) in uscita.
  - Quell'errore nasconde quello vero: un controllo sul messaggio "The playlist does not exist" non scatterebbe mai.
- Le soluzioni possibili sono due:
  - una copia scrivibile in `/data/state`, creata all'avvio partendo dall'originale in `secrets`;
  - un `io.StringIO` nuovo per ogni istanza. Riusarlo non funziona, perché yt-dlp lo tronca senza riavvolgerlo. In compenso, così si perdono i cookie aggiornati dal server, e non è chiaro se la sessione ne soffra.
- **Mai in `/data/cache`:** `GET /media/{filename}` (`crt/api.py:186`) serve per nome, senza autenticazione, qualsiasi file di quella cartella. Un cookies.txt contiene una sessione intera dell'account Google, molto più ampia dello scope OAuth `youtube`.
- **Proprietà dei file:** il container gira come root, mentre le cartelle sull'host sono di `flocca`. Un file riscritto dal container diventa di root, e un `scp` successivo dal Mac sullo stesso percorso fallisce. È un altro motivo per tenere l'originale in `secrets` e lavorare su una copia.

### I download devono restare senza cookie

- Ogni istanza di `YoutubeDL` ha il suo insieme di cookie: un'istanza con i cookie per l'elenco non influenza quelle senza cookie di `crt/pipeline.py` (`fetch_title`, `download_video`).
- Con i cookie, yt-dlp 2026.08.19 cambia i client usati per scaricare.
  - Passa a `('web_embedded', 'tv_downgraded', 'web')`, che richiedono tutti il player JavaScript (deno più `yt-dlp-ejs`). Nell'immagine Docker nessuno dei due c'è.
  - Il client senza JavaScript usato oggi (`visionos`) viene scelto solo senza autenticazione.
  - Il ramo master del 2026-10-10 cambia di nuovo le impostazioni predefinite (`tv_samsung`).

  Passare i cookie ai download romperebbe quindi i download che oggi funzionano.
- I blocchi di account documentati riguardano proprio il caricamento intenso di video con l'account (yt-dlp#10085).
- **Sicurezza:** CVE-2026-50019. Con `--downloader curl`, le versioni di yt-dlp precedenti alla 2026.06.09 inviano i cookie anche ai siti verso cui vengono reindirizzate. crt-player non usa quel downloader, ma con i cookie andrebbe fissata una versione minima.

### Rischio per l'account

- Il wiki di yt-dlp avverte: "By using your account with yt-dlp, you run the risk of it being banned (temporarily or permanently)". Consiglia un account usa e getta, che qui non si può usare.
- Un collaboratore del progetto (gamer191, citato in yt-dlp#13964) è più netto: "DO NOT login with an important Google account (such as an account you use for Gmail)".
- Un elenco ogni 5 minuti fa 12-36 richieste di pagina all'ora, molto sotto il limite indicato di circa 2000 video all'ora. Non si sono trovate segnalazioni di account bloccati per il solo elenco, ma i maintainer dicono che il limite reale non lo conosce nessuno.
- Resta comunque un accesso automatizzato, escluso dai termini di servizio di YouTube.

## Rimozione dei video (comando `/control/delete/current`)

Oggi `PlayerCore.delete_current` (`crt/player_core.py:306`) toglie il video dalla libreria. Poi chiama `delete_playlist_item(playlist_item_id)` (`:314`), ma solo se l'ID è presente. Con "Guarda più tardi" quell'ID non esiste: la rimozione da YouTube verrebbe saltata e il sync successivo rimetterebbe il video in coda.

- **API ufficiale:** no. Il supporto è deprecato dal 2020-09-09, e comunque gli ID degli elementi si ottengono solo leggendo la playlist, cosa impossibile.
- **InnerTube con i cookie:** funziona, ed è usata nel 2026 dal componente per Kodi, da Tatsh/youtube-unofficial e da varie estensioni del browser. La chiamata:
  - `POST https://www.youtube.com/youtubei/v1/browse/edit_playlist`;
  - corpo: `{"context": {"client": {...}}, "playlistId": "WL", "actions": [{"action": "ACTION_REMOVE_VIDEO_BY_VIDEO_ID", "removedVideoId": "<id>"}]}`;
  - in caso di successo risponde `"status": "STATUS_SUCCEEDED"`;
  - basta l'ID del video. L'alternativa `ACTION_REMOVE_VIDEO` con `setVideoId` richiede di leggere prima la pagina della playlist, e yt-dlp quel campo non lo espone;
  - intestazioni: tutti i cookie di youtube.com, più `Authorization: SAPISIDHASH <ts>_<sha1("<ts> <SAPISID> https://www.youtube.com")>`, `X-Origin` e `Origin` uguali a `https://www.youtube.com`, `X-Goog-AuthUser: 0` e `X-Goog-PageId` solo per un canale brand account. Le varianti `SAPISID1PHASH`/`SAPISID3PHASH` che yt-dlp manda dal 2025 non risultano obbligatorie.
- **Librerie Python:**
  - nessuna incapsula bene l'operazione per un uso senza browser;
  - `youtube-unofficial` (Tatsh) è la più vicina: asincrona, con un solo maintainer, versione 0.4.0 di aprile 2026;
  - `ytmusicapi` non va bene, perché lavora su YouTube Music, richiede `setVideoId`, e il maintainer considera "Guarda più tardi" fuori ambito;
  - YouTube.js è per Node;
  - yt-dlp non ha funzioni di scrittura.
- **OAuth con il client TV:** il componente per Kodi rimuove video da "Guarda più tardi" con un token OAuth del client di YouTube per TV e un contesto `TVHTML5`.
  - Con il client OAuth di crt-player non è verificato.
  - Gli utenti di ytmusicapi ricevono HTTP 400 con client di tipo TV (2026-08-29), e il maintainer ritiene che Google l'abbia chiuso di proposito (2026-09-01).
  - Si aggiunge il problema di policy descritto in [Cosa non fare](#usare-il-client-oauth-di-crt-player-per-chiamare-innertube).
- **Lista locale di video da ignorare:** non scrive niente su YouTube, ma ha dei costi.
  - "Guarda più tardi" cresce senza limite, con un tetto di 5000 video riportato solo da fonti terze.
  - Salvare di nuovo un video ignorato non lo fa tornare, perché è ancora in playlist.
  - La lista vivrebbe in `/data/state` su Chet, che non ha backup: se si perde, tornano tutti i video eliminati.
- **"Rimuovi video guardati" di YouTube non aiuta:**
  - crt-player trasmette al Chromecast un MP4 locale, quindi YouTube non registra niente nella cronologia;
  - l'opzione di rimozione in blocco non compare nelle pagine ufficiali;
  - `yt-dlp --mark-watched` richiede i cookie ed era segnalato come non funzionante (#8236).

## Impatto su crt-player, se un giorno si riprende l'idea

Il punto d'aggancio è stretto: `SyncEngine` chiama solo `list_playlist_items(playlist_id)` e usa ordine, `video_id`, `title` e `playlist_item_id` delle voci. Un nuovo client basato su yt-dlp si inserirebbe lì. Il lavoro vero sta altrove.

**Codice crt-player:**

- `crt/sync_engine.py`:
  - una protezione che rifiuti un elenco vuoto, troncato o calato di colpo (oggi qualsiasi elenco restituito senza eccezioni viene preso per buono);
  - un intervallo minimo tra i sync forzati, perché Flipper `0x06` e Ctrl+Y fanno partire subito un elenco autenticato;
  - il docstring "YT is master of content and order... never writes back to YT" da aggiornare.
- `crt/youtube_client.py`, `crt/bootstrap.py` e le dipendenze `google-*`: da sostituire, oppure da tenere dietro un selettore della sorgente. `crt/player_core.py` importa `YouTubeAuthError` da lì.
- `crt/player_core.py`, `delete_current`:
  - la condizione sulla rimozione va basata su `video_id` invece che su `playlist_item_id`;
  - l'`except` va allargato agli errori di rete e JSON;
  - oggi `_delete_local` non cancella il file sorgente (`downloaded_path`).
- `crt/library_store.py`: `playlist_item_id` può restare `None`. Un'eventuale lista di video ignorati andrebbe in una chiave opzionale della versione 3: passare alla 4 farebbe scartare tutto lo stato a un daemon riportato indietro.
- `crt/daemon.py`: il collegamento tra client, `SyncEngine` e `PlayerCore` dipende da `config.YT_PLAYLIST_ID` e non è coperto da test.
- `crt/pipeline.py`:
  - nessun filtro sulle dirette: un video in diretta in "Guarda più tardi" farebbe registrare a yt-dlp fino alla fine, bloccando l'unico worker;
  - le anteprime programmate fallirebbero e verrebbero ritentate per sempre (ogni ≤6 h, issue #14), come i video privati o per soli membri.
- **Errori invisibili:**
  - la TUI mostra solo lo stato del sync, non `last_error`;
  - il Flipper non mostra niente;
  - `/control/sync` risponde sempre 202;
  - l'HEALTHCHECK del container controlla solo che `/status` risponda, quindi un sync `degraded` resta "healthy".

  Una sessione scaduta passerebbe inosservata.
- **Privacy:** `/library/items` e `/status` esporrebbero il contenuto privato di "Guarda più tardi" senza autenticazione, a chiunque nella tailnet.
- **Test da rifare:**
  - `tests/test_youtube_client.py`;
  - `tests/test_bootstrap.py`;
  - le parti su `playlist_item_id` ed errori di autenticazione di `tests/test_sync_engine.py`;
  - i test di `delete_current` in `tests/test_player_core.py`;
  - la forma di `/status` in `tests/test_api.py`.

**Deploy (lodge-tools, `services/crt-player/`):**

- `install.sh:24` rifiuta un `.env` senza `CRT_YT_PLAYLIST_ID=PL...` (quindi rifiuta `WL`).
- `install.sh:35-39` copia solo `client_secrets.json` e `oauth_token.json` e applica `chmod 600` solo a `*.json`: un cookies.txt non verrebbe né copiato né protetto.
- `update.sh` non tocca i segreti.
- yt-dlp non ha una versione fissata in `requirements.txt` e resta congelato dalla cache dei layer Docker. Ha già causato 5 giorni di download falliti (2026-08-24). Con "Guarda più tardi" anche il sync dipenderebbe da yt-dlp, e un yt-dlp vecchio potrebbe restituire un elenco vuoto, cioè svuotare la libreria.

**Passaggio da CRT Queue:**

- `state.json` non registra quale playlist rispecchia. Il primo sync toglierebbe quindi i video di CRT Queue assenti da "Guarda più tardi" e metterebbe in coda tutto l'arretrato di "Guarda più tardi", con il relativo costo di CPU e disco.
- L'elenco non ha una data di aggiunta: per partire "solo dai video nuovi" servirebbe salvare gli ID presenti al momento del passaggio.

## Ambiente di produzione

Verificato da lodge-tools in sola lettura il 2026-10-15.

- **Dove gira:** il daemon gira su Chet (Linux Mint) dalla migrazione del 2026-05-22, non sul Raspberry Pi.
- **Montaggi:**
  - `secrets` → `/data/secrets` in sola lettura;
  - `state` → `/data/state` e `cache` → `/data/cache` scrivibili;
  - nessun altro montaggio.
- **Utente:** il container gira come root (uid 0), mentre le cartelle sull'host sono di `flocca` (775).
- **yt-dlp:** 2026.08.19, l'ultima release al momento. L'immagine è stata costruita il 2026-10-14.
- **Runtime JavaScript:** nessuno nell'immagine (né deno, né node, né bun) e niente `yt-dlp-ejs`.
- **Ultime 24 ore di log:**
  - nessun controllo anti-bot, nessun PO token, nessun 429;
  - due 403 isolati su singoli video, risolti al primo tentativo successivo.
- **Cookie:** nessun cookie YouTube o Google già usato da altri servizi su Lodge o Chet. Su Chet ci sono solo profili browser (Firefox e il Chromium del kiosk).
- **Segreti:** arrivano ancora con `lodge crt-player install` dal Mac, via `scp`.
- **Backup:** Chet non ha backup. La copia sul Mac (`~/.local/share/crt-player/`) è l'originale. Il CLAUDE.md di crt-player dice ancora "restic on Pi", che non è più vero.

## Comodità: quanto si guadagnava davvero

- **App per telefono (Android e iOS), pagina del video:** "Salva" salva con un tocco nell'ultima playlist usata, o in "Guarda più tardi" se non ce n'è una, e il popup offre "MODIFICA" (Help Center, risposta 57792, letta il 2026-10-15). Se l'ultima playlist usata è CRT Queue, basta già un tocco.
- **App per telefono, feed (Home, iscrizioni, ricerca):** il menu ⋮ ha "Salva in Guarda più tardi"; una playlist personale richiede "Salva in playlist" e la scelta. È l'unico caso in cui "Guarda più tardi" fa risparmiare un tocco.
- **Desktop:** dalla pagina del video, "Salva" apre l'elenco delle playlist, dove "Guarda più tardi" e le playlist personali costano un clic ciascuna. Il pulsante "Guarda più tardi" al passaggio del mouse sulle miniature risulta tolto da Home e Iscrizioni da marzo 2026 (resoconto di terze parti, nessuna conferma ufficiale).
- **App per TV:** "Salva video" seguito dalla scelta della playlist; nessuna scorciatoia solo per "Guarda più tardi".
- **Shorts:** da luglio 2026 il pulsante Salva degli Shorts salva in una playlist di sistema "Saved Shorts", quindi non aiuta in nessuno dei due casi (fonti non ufficiali).

## Strade ancora aperte

1. **Restare su CRT Queue e usare "Salva" dalla pagina del video:** zero lavoro. Il popup "MODIFICA" permette di cambiare playlist quando serve.
2. **Una scorciatoia di condivisione:**
   - su iOS un Comando Rapido con "Mostra nel foglio di condivisione", su Android l'app HTTP Shortcuts;
   - la scorciatoia manderebbe il link a un nuovo endpoint del daemon, che aggiunge il video a CRT Queue con `playlistItems.insert`;
   - costo: 50 unità di quota per video, su 10.000 al giorno; il sync ne usa circa 288;
   - è lo stesso scope `youtube` già in uso;
   - sarebbe il primo endpoint con bisogno di autenticazione, perché oggi l'API del daemon non ne ha e conta solo su Tailscale.
3. **Se un giorno si volesse riprovare con "Guarda più tardi":**
   1. rileggere la [revision history dell'API](https://developers.google.com/youtube/v3/revision_history) per vedere se Google ha ripristinato l'accesso a WL. È l'unico cambiamento che renderebbe semplice la cosa;
   2. altrimenti, prima di toccare crt-player, una prova di 14 giorni su Chet, fuori da tutti i montaggi di crt-player:
      - uno script separato, con yt-dlp alla stessa versione della produzione, legge "Guarda più tardi" ogni 5 minuti con cookie esportati da una finestra privata di Firefox, dallo stesso IP pubblico di Chet;
      - registra esito, numero di voci contro `playlist_count`, presenza di `LOGIN_INFO`, nomi (non valori) dei cookie cambiati e primi e ultimi ID;
      - il primo giorno, a mano: salvare due video di prova e vedere dove compaiono, spostarli, toglierne uno;
      - se la sessione regge 14 giorni, si progetta; se muore in pochi giorni, si chiude;
   3. in qualsiasi progetto, le protezioni elencate sopra: rifiuto di elenchi vuoti o troncati, cookie solo per l'elenco, copia scrivibile fuori da `/data/cache`, avviso quando la sessione muore, versione minima di yt-dlp.

## Un rischio che esiste anche senza "Guarda più tardi"

`SyncEngine._apply_diff` non ha protezioni contro un elenco vuoto: qualsiasi sorgente che restituisca zero video senza sollevare un'eccezione svuota libreria e cache. Con l'API ufficiale su CRT Queue oggi non succede, perché i problemi di autenticazione sollevano un errore. Una protezione (rifiutare un elenco vuoto o calato di colpo, chiedendo una seconda conferma) costerebbe poco e coprirebbe anche guasti futuri.

## Domande rimaste senza risposta

- Cosa restituisce oggi `playlistItems.list?playlistId=WL`: 200 vuoto, 403 o 400.
- Che forma ha un elenco autenticato di "Guarda più tardi", e cosa restituisce quando la playlist è davvero vuota (zero voci o un avviso).
- Quanto durano i cookie su Chet con un controllo ogni 5 minuti.
- In che ordine arrivano i video nuovi sull'account di Giacomo.
- Se il limite di 5000 video vale anche per "Guarda più tardi".
- Se l'account usa un brand account o più canali: ogni canale ha il suo "Guarda più tardi".

## Fonti principali

API ufficiale:

- YouTube Data API, revision history (voci 2015-03-11, 2016-08-11, 2016-09-15, 2020-09-09, 2021-01-28; letta fino al 2026-09-30): https://developers.google.com/youtube/v3/revision_history
- Riferimento `playlistItems.list` (aggiornato 2026-09-14): https://developers.google.com/youtube/v3/docs/playlistItems/list
- Data Portability API, accesso periodico: https://developers.google.com/data-portability/user-guide/time-based
- YouTube API Services Developer Policies: https://developers.google.com/youtube/terms/developer-policies

yt-dlp:

- Wiki di yt-dlp, esportazione dei cookie e avviso sul blocco degli account: https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies
- Durata dei cookie e consigli dei maintainer: https://github.com/yt-dlp/yt-dlp/issues/13964, https://github.com/yt-dlp/yt-dlp/issues/12009, https://github.com/yt-dlp/yt-dlp/issues/14872
- Correzione della paginazione (versione 2026.07.04): https://github.com/yt-dlp/yt-dlp/pull/16948
- Client usati con i cookie e runtime JavaScript: https://github.com/yt-dlp/yt-dlp/wiki/EJS, https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide

Rimozione e accesso tramite InnerTube:

- Componente YouTube per Kodi, accesso a "Guarda più tardi" via InnerTube: https://github.com/anxdpanic/plugin.video.youtube (`resources/lib/youtube_plugin/youtube/client/data_client.py`)
- Rimozione via InnerTube in Python: https://github.com/Tatsh/youtube-unofficial
- OAuth con client TV su InnerTube e le sue restrizioni del 2025-2026: https://github.com/sigma67/ytmusicapi/issues/813
- Fine dell'API per "Guarda più tardi" vista dagli sviluppatori: https://stackoverflow.com/q/66156461, https://github.com/lambdasoup/watchlater/issues/53

Comodità d'uso:

- Help Center YouTube, salvare in playlist e "Guarda più tardi": https://support.google.com/youtube/answer/57792, https://support.google.com/youtube/answer/56101
