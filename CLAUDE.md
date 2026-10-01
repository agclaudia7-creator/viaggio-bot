# Progetto: Assistente itinerari viaggio Sud-Est Asiatico

## Contesto
Viaggio di 6 mesi nel Sud-Est asiatico (partenza tra ~2 mesi). L'itinerario si decide in itinere,
a volte con settimane di anticipo, a volte giorno per giorno in base a mood/necessità.
Uso principale da **cellulare e web**: niente prompt lunghi da scrivere o incollare (valutare questionari adattivi con pochi tap).
Risposte sempre brevi, schematiche, facili da consultare (panoramica sintetica, dettaglio solo su richiesta, ~150 parole max).

## Cartelle
- **Codice**: `G:\Il mio Drive\Viaggi\Bot_Code\` (progetto aperto in VS Code da qui; il telefono lo copia con rclone). Niente segreti qui.
- **Config con token/chiavi**: solo locali, in `~/.viaggio_bot/` (PC: `C:\Users\clo4e\.viaggio_bot\`, telefono: `/data/data/com.termux/files/home/.viaggio_bot/`), oppure env `BOT_CONFIG_DIR` (`build_places.CONFIG_DIR`). Lì anche `bots_config.termux.json` / `api_config.termux.json` = copie per il telefono.
- **Dati**: `G:\Il mio Drive\Viaggi\Aspettativa\`. 
  - `FONTI/<Nazione>/` = tutto ciò che serve a Gemini Notebook per quella nazione: `<Nazione>.txt` (logistica), `<Nazione>_culture.txt` (curiosità) generati dal bot, + eventuali PDF/documenti/note dell'utente sulla stessa nazione, tutto nella stessa cartella (Notebook non distingue le sottocartelle: comodo avere lì tutte le fonti di una nazione). File di istruzioni `00_LEGGIMI_come_sono_creati_i_file.txt` e `ISTRUZIONI_PER_GEMINI_NOTEBOOK.txt` stanno in `FONTI/` (radice), non ripetuti per nazione.
  - `BOT_OUTPUT/<Nazione>_bot_output/` = dati di lavoro del bot: `<Nazione>_bot.txt` (reel grezzi), `<Nazione>_bot_immagini.json` (testo immagini), `places.json` (fonte di verità), cache, duplicati. NON per Notebook: lì Notebook non legge.
  - `MyMaps/<Nazione>/` = CSV per le mappe Google (uno per livello: bassissima, bassa, media, alta, altissima).
  - `_archivio_bot/` = backup e versioni vecchie (ignora).
  - `LEGGI_PRIMA.txt` (radice) = guida sulla struttura di Aspettativa.
  - `build_places.output_dir(txt)` trova la cartella output in BOT_OUTPUT. `build_places.notebook_path(out_dir)` calcola il percorso in `FONTI/<Nazione>/<Nazione>.txt`.

## Pipeline
1. **[FATTO] Ingestione**: reel Instagram inviato a un bot Telegram dedicato per nazione -> `script.py` (PC o telefono) estrae la caption via RapidAPI (`api_config.json`, più API con fallback) -> salva Link + Caption in `BOT_OUTPUT/<Nazione>_bot_output/<Nazione>_bot.txt` su Google Drive.
   Config bot in `bots_config.json`. Se l'API restituisce `taken_at`/`taken_at_timestamp`, si salva anche `Data: aaaa-mm-gg` (data di pubblicazione, nessuna richiesta in più) sotto la Caption; `parse_reels` la stacca dal blocco prima di leggere la caption (altrimenti la regex con `re.S` la inghiottirebbe).
   - Messaggio con la parola `post` fuori dal link (es. `post https://instagram.com/p/...`): `extract_image_urls` prende le immagini dalla stessa risposta RapidAPI (formati `edge_sidecar_to_children` (bulk-scraper), `carousel_media` con `image_versions2` (instagram39) o `media_url` (instagram-social, `body`); per i video solo la copertina; max `MAX_POST_IMAGES`), le scarica subito (i link Instagram scadono) e Gemini ne legge il testo (1 richiesta per post). Il testo va in un file a parte, `<Nazione>_bot_immagini.json` ({shortcode: testo}) accanto al txt (`build_places.save_image_text`): il txt resta solo Link + Caption e non viene mai riscritto. `parse_reels` aggiunge `\nTesto immagini: …` alla caption. RapidAPI non fa OCR. Per i reel video c'è solo la copertina.
     Se il post è già nel txt del bot (`saved_block`): senza `post` risponde "già salvato"; con `post`, se il json non ha ancora il suo testo, lo legge (1 credito RapidAPI), lo salva nel json e poi parte l'aggiornamento (la caption cambiata fa rianalizzare il post). Righe `Testo immagini:` dentro il txt (vecchio formato) vengono ancora lette.
     **Tracciamento errori** (`save_error`, `load_errors_log`): se l'estrazione del testo (Gemini) fallisce, viene registrato in `<Nazione>_bot_errori.json` {shortcode: {type, timestamp, error}} accanto al txt. L'utente usa `/errori` per visualizzare i post con errori e `/retry` (bottoni) per ritentarli. Una volta riuscito, l'errore viene automaticamente rimosso dal log. Vedi `ERRORI_TRACKING.md` per i dettagli.
   - Messaggio con la parola `video` fuori dal link: `extract_video_url` (`video_url` / `video_versions` / `media_url` mp4) + `extract_video_text` = Gemini legge solo le scritte in sovrimpressione (niente parlato, scelta dell'utente), video inline fino a 20 MB, oltre con la Files API. Salvato nello stesso `_immagini.json` (unito con " / " se ci sono anche `post`). Testato su bulk-scraper: reel letto in ~30 s.
     Testato su instagram-public-bulk-scraper (carosello di 7 immagini, 3,5 s). Le altre API potrebbero non restituire le immagini: in quel caso il bot salva solo la caption e lo dice. Errori tracciati come sopra.
   - **Risparmio crediti RapidAPI** (`script.py`, dati persistiti in `api_config.json` per API): circuit breaker generale (`record_api_result`, `FAIL_STREAK_THRESHOLD`=3, `COOLDOWN_SECONDS`=1800) — dopo 3 fallimenti di fila (rete/429/403/errore HTTP) l'API va in pausa 30 min ed è saltata in `get_working_api_response`; un 200 senza caption non conta come fallimento. Flag `no_images`/`no_video` (`record_media_result`, `MEDIA_MISS_THRESHOLD`=3) — se un'API non restituisce immagini/video 3 volte di fila con `post`/`video`, viene provata per ultima in quei casi (resta utilizzabile normalmente per la sola caption); si sblocca da sola se torna a funzionare. Visibile da Telegram con `INFO`.
2. **[FATTO] Pulizia/raggruppamento**: `build_places.py` legge un `*_bot.txt` (oppure `--all` = tutti i txt di `bots_config.json`; se la quota Gemini finisce, le nazioni successive vengono rigenerate solo dalla cache), estrae i luoghi con **Gemini** (SDK `google-genai`, modello di default `gemini-3.5-flash-lite`, tier gratuito), raggruppa i duplicati e tiene traccia di tutti i reel che citano ogni luogo. **Sintetizza i testi culturali**: per i luoghi di categoria "cultura", dopo il raggruppamento chiama `synthesize_cultural_place_text()`, che sintetizza con Gemini le descrizioni e i consigli in paragrafi discorsivi coerenti, salvando `_notes_synth`/`_tips_synth` in `places.json` (per la tab Cultura di `app.py`). Usa `call_gemini_json` a lotti di 30 luoghi (cache per place id in `culture_place_synth_cache.json`, hash di note+tips). **Attenzione**: una prima versione usava `google.generativeai` (SDK deprecato) con una chiamata Gemini PER OGNI luogo (non a lotti) — su rete con firewall/proxy che intercetta TLS quella libreria falliva la verifica del certificato in modo massiccio e bloccava l'intero `/aggiorna` per minuti PRIMA ancora di salvare `places.json` (bug visto e corretto: la sintesi non arrivava mai a destinazione, le tab mostravano il ripiego a note grezze concatenate — es. Batu Caves con 23 frasi quasi identiche in fila). Se altre parti del codice iniziano a importare `google.generativeai` invece di `google.genai`, è lo stesso campanello d'allarme.
   - Output in `BOT_OUTPUT/<nome>_bot_output/`: `places.json`, `places.md`, `tips.json`, `extraction_cache.json`, `aliases.json` (unioni manuali), `duplicate_checks.json` (giudizi su possibili doppioni).
   - Ogni luogo tiene anche `last_mention_date` (data del reel più recente che lo cita, dai mentions) mostrata in places.md come "ultimo reel: aaaa-mm-gg": utile per capire quanto sono vecchie le informazioni (i reel salvati prima di questa modifica non hanno la data).
   - Per Gemini Notebook (non legge i .md): `write_notebook` salva una copia .txt di places.md / tips.md, con un'intestazione "Aggiornato al: aaaa-mm-gg" (data di generazione del file, non dei reel), in `Aspettativa/FONTI/<Nazione>/<Nazione>.txt` (senza `_bot`; percorso calcolato da `notebook_path`; la vecchia cartella `Gemini_Notebook` non esiste più). La guida `00_LEGGIMI_come_sono_creati_i_file.txt` (testo in `NOTEBOOK_GUIDE`) sta una sola volta in `FONTI/` (non ripetuta per nazione), riscritta a ogni aggiornamento: se cambia il formato di places.md va aggiornata. `FONTI/<Nazione>/` è pensata per contenere anche le fonti originali dell'utente su quella nazione (guide/PDF), tutte insieme ai file generati dal bot: comodo per Notebook, che non distingue le sottocartelle e va comunque a cercare le fonti per nazione. La guida istruisce Notebook a fidarsi dei file per i gusti dell'utente (stato/voto/note, che il web non conosce) e a verificare online orari/prezzi/chiusure quando la domanda riguarda un piano concreto, specialmente per nightlife/mercati/locali stagionali. Rigenerati anche dai feedback dell'assistente (passano da `write_markdown`). In Notebook le fonti vanno ricaricate a mano.
   - Informazioni culturali separate: durante `write_notebook` viene creato anche `<Nazione>_culture.txt` in `Aspettativa/FONTI/<Nazione>/`, che raccoglie tutti i tips specifici dei luoghi (consigli pratici, tradizioni locali), i contro/criticità e i consigli generali non legati a un luogo. **Ogni informazione è tracciata con il/i reel che l'ha generata** (link Instagram con numero): puoi così risalire alla fonte originale se serve approfondire. Organizzato per città e tema, facilita ricerche culturali in Notebook senza confondere con le informazioni logistiche (orari, prezzi, coordinate).
   - In `Aspettativa/FONTI/` (cartella radice) c'è anche `ISTRUZIONI_PER_GEMINI_NOTEBOOK.txt`: istruzioni su come Notebook deve ragionare e rispondere (stile schematico per itinerari, discorsivo per cultura, quando verificare online, come proporre link da aggiungere). Va incollato in Notebook insieme ai file della nazione.
   - Per luogo estrae anche: voto del creator (`creator_ratings`), giorni consigliati, contro (`cons`), consigli specifici (`tips`). I consigli generali hanno la nazione e finiscono anche in fondo a places.md.
   - Cache per reel: rilanciarlo analizza solo i reel nuovi, quelli estratti con un `PROMPT_VERSION` diverso (incrementarlo quando si cambia il prompt) o quelli con la caption cambiata (hash `_h` in cache; le voci vecchie senza `_h` non vengono rifatte). `status`/`rating`/`user_notes` in places.json vengono conservati.
   - Raggruppamento: nomi normalizzati + fuzzy match (soglia 0.86) con città/nazione compatibili.
     I possibili doppioni (0.70–0.86) li giudica Gemini (`resolve_duplicates`, una richiesta per run, solo coppie nuove): gli uguali finiscono in `aliases.json` (gruppi transitivi, si tiene il nome più citato), tutti i giudizi in `duplicate_checks.json` (mai richiesti di nuovo). Per annullare un'unione: togliere la riga da aliases.json e mettere `false` in duplicate_checks.json.
     Nel confronto si ignorano parole generiche (`GENERIC_WORDS`: restaurant, park, food centre…) e il nome di città/nazione; città sinonime unificate con `CITY_ALIASES` (Malacca=Melaka, George Town→Penang).
   - Bot di soli consigli (`"type": "tips"` in `bots_config.json`, oggi `Solo_Traveler_bot`; da riga di comando `--tips`): niente luoghi né mappa, prompt `TIPS_PROMPT` (versione `TIPS_PROMPT_VERSION`) -> `tips.json` + `tips.md` raggruppati per argomento (`TIPS_TOPICS`).
     Doppioni: `group_tips` confronta il testo (fuzzy match 0.86 come i luoghi, solo tra consigli dello stesso argomento) e unisce chi supera la soglia (`mentions`/`mention_count` come i luoghi); le coppie ambigue (0.70–0.86) le giudica Gemini una volta sola (`resolve_tips_duplicates`), giudizi in `tips_duplicate_checks.json` (mai richiesti di nuovo). I consigli generali non legati a un luogo nei bot nazione (`collect_tips`, in fondo a places.md) NON hanno ancora questo controllo, solo dedup su testo identico: miglioria futura.
3. **[FATTO] Geocoding + CSV per Google MyMaps**: `build_maps.py` legge tutti i `places.json` (+ `blog_places.json`, vedi punto 6), geocoding con Nominatim (OSM, gratis, 1 req/s, cache in `MyMaps/geocode_cache.json`). Output in `MyMaps/<Nazione>/` (una cartella per nazione, ogni nazione ha una sua mappa MyMaps a parte invece di un layer condiviso — coi 9+ nazioni attuali un'unica mappa avrebbe superato il limite di 10 layer): dentro, fino a 4 CSV per nazione divisi per citazioni — `<Nazione>_bassa/media/alta/altissima.csv` (limiti MyMaps: 10 layer/mappa, 2000 righe/layer).
   - Divisione in livelli (`split_citation_tiers`/`tier_count`), in due passaggi: **bassissima** = citati da una sola fonte (×1, tipicamente la maggioranza: 572/728 in Thailandia), separata subito perché da sola schiaccerebbe qualunque soglia sul resto (provato anche con percentili e quarti del range lineare su TUTTI i valori insieme, incluso ×1: soglie sempre schiacciate sul valore più comune, es. 711/11/4/2 in Thailandia, il ×17 massimo non bastava a spalmare i livelli). I restanti (×2+) si dividono con lo stesso ragionamento ma sul loro proprio minimo/massimo: soglie in scala **logaritmica** (non lineari, non percentili, non calibrate per ottenere gruppi di uguale numero — provato anche con equi-numerosità per rango: divideva in gruppi uguali ma senza senso di importanza reale, confini decisi a caso a parità di citazioni. L'importanza è per natura sbilanciata — pochi luoghi eccezionali, tanti normali — quindi le fasce alte devono restare piccole, non bilanciate). Il **numero di fasce** (1-4, `tier_count`) tra i ×2+ si adatta da solo: una fascia in più ogni volta che le loro citazioni raddoppiano tra min e max, così le nazioni con poca varietà tra i ×2+ (es. Laos) scendono a meno fasce invece di lasciarne una vuota in mezzo. Risultato tipico: Thailandia 572 bassissima / 125 bassa / 14 media / 11 alta / 6 altissima (fino a ×17, ora davvero isolato); Malesia e Singapore 4 fasce sopra bassissima; Laos/Cambogia/Indonesia solo bassissima+bassa; Brunei/Myanmar/Vietnam (tutto ×1) solo bassissima. Csv vuoti non vengono creati; quelli non più popolati da un aggiornamento precedente vengono rimossi.
   - Righe ordinate per categoria secondo `CATEGORY_ORDER` (stesso ordine fisso in ogni CSV di ogni nazione/livello): necessario perché MyMaps assegna i colori nell'ordine con cui incontra i valori in un layer, quindi senza un ordine fisso la stessa categoria avrebbe colori diversi tra layer/nazioni diverse.
   - Colonna `Posizione`: sempre testo "nome, città, nazione" che Google cerca all'import. Le coordinate mescolate al testo nella stessa colonna davano righe in errore in MyMaps. `Lat`/`Lon` = coordinate Nominatim (solo se il luogo è entro 60 km dalla città), solo informative.
   - Colonna `Descrizione` (`description`/`synthesize_descriptions`): i luoghi citati da più reel avevano tante descrizioni/consigli quasi identici (stesso contenuto, parole diverse da creator a creator) semplicemente accodati, molto ridondante da leggere. Per i luoghi con note/consigli/contro ridondanti (`_needs_synthesis`), Gemini li riscrive in un blocco sintetico senza ripetizioni (una sola richiesta per run, solo per i luoghi nuovi/cambiati) prima di generare i CSV; risultato in cache in `MyMaps/description_cache.json` (richiamato di nuovo solo se note/consigli/contro del luogo cambiano). Tocca solo il CSV: `places.json`/places.md/culture.txt restano con i dati grezzi per reel.
   - **Doppioni tra fonti diverse** (`collect_places`/`merge_cross_source_duplicates`): `places.json` (reel) e `blog_places.json` (punto 6) assegnano id separati, quindi lo stesso luogo nominato in modo diverso dalle due fonti (es. "Batu Caves" dai reel vs "Batu cave" da un blog, categorie pure diverse) non veniva mai confrontato e restava duplicato nel CSV (bug visto e corretto, vedi feedback utente). `collect_places()` ora unisce (`_merge_place`) anche due voci con lo stesso id dentro lo stesso `places.json` (prima si scartava la meno citata perdendone tips/note). `merge_cross_source_duplicates()` poi confronta TUTTI i luoghi raccolti con lo stesso fuzzy match di `group_places` (sopra `FUZZY_MERGE` unione diretta, tra `FUZZY_SUGGEST` e `FUZZY_MERGE` giudica Gemini con lo stesso `DUP_PROMPT`, cache in `MyMaps/cross_duplicate_checks.json`, mai richiesto di nuovo); resta come principale il luogo con più citazioni. Il merge dei dati fa poi scattare `synthesize_descriptions` (sopra) sulla descrizione unita. Anche questo tocca solo i dati per il CSV, non i `places.json` originali.
   - `truststore` (pip) fa usare a Python i certificati di Windows: senza, dietro firewall aziendale le richieste HTTPS falliscono con SSLError.
   - MyMaps non si risincronizza da solo: per aggiornare una nazione si elimina il layer e si reimporta il CSV (da Google Drive).
   - `Geocoder.reverse_country(lat, lon)`: nazione (nome come in `COUNTRY_CODES`, es. "Thailandia") dalle coordinate, via reverse geocoding Nominatim; usato da `blog_import.py` per capire a quale nazione appartiene un luogo, indipendentemente dal bot che l'ha ricevuto. Cartella `MyMaps/` calcolata da `build_maps.mymaps_dir()`, fratella di `BOT_OUTPUT/` (o accanto alle cartelle `*_output` nel vecchio schema) — usata anche da `blog_import.py`.
4. **[FATTO] Automazione** in `script.py`: dopo ogni reel salvato parte un timer (`UPDATE_DELAY_SECONDS` = 3 min, si riavvia a ogni reel) che lancia `build_places.process_file` sul txt del bot e poi `build_maps.build_maps()` (non per i bot di consigli); il bot risponde con un riepilogo. Comando `/aggiorna` per farlo subito. Un solo aggiornamento alla volta (`update_lock`). Gemini viene chiamato solo per i reel non in cache.
   Il processo del bot deve avere `GEMINI_API_KEY` tra le variabili d'ambiente.
   I bot usano `drop_pending_updates=False`: i messaggi inviati a portatile spento (Telegram li tiene 24 ore) vengono elaborati all'avvio.
5. **[FATTO] Assistente Telegram** (`assistant.py`), integrato in ogni bot nazione (non nei bot di consigli): `script.py` chiama `assistant.register(app, update_lock, home_file=<places.json del bot>)`, che restituisce l'handler del testo. Nei bot nazione, un messaggio con link Instagram salva il reel; `INFO` resta; il resto (o la nota in attesa dopo 📝) va all'assistente. Facoltativo: bot a parte con `"type": "assistant"` (senza `txt_file_path`).
   - `/consiglia` = questionario a bottoni (zona → tempo → mood → budget; prima la nazione se il places.json del bot ne ha più di una, bottone "🌏 Altra nazione" per le altre); oppure testo libero con parole chiave (cerca in tutte le nazioni). `/aiuto` = istruzioni.
   - Gemini sceglie max 5 luoghi da places.json (solo `da_fare`) tenendo conto del profilo (voti ≥4, scartati/voti ≤2, note); senza chiave/quota ripiega sui più citati.
   - Risposta = un messaggio con le proposte numerate (ordinate dal più citato, "(🎬 N)" accanto al nome) + bottoni 1–5 e "🔄 Altri"; toccando un numero arriva la scheda completa del luogo (tutte le descrizioni, costi, giorni, voti, consigli, contro, mie note) con i suoi bottoni (la lista resta per sceglierne altri).
   - Bottoni per luogo: ✅ visitato (poi chiede il voto), ⭐ voto 1–5, 📝 nota, link Maps e un 🎬 per ogni reel (`MAX_REEL_BUTTONS` = 5 per messaggio, poi "➕ Altri reel (N)" manda i 5 successivi). L'utente non vuole bottoni che scartano/eliminano luoghi (tolto ❌). Salvati in tutti i places.json che contengono il luogo + places.md rigenerato (scritture sotto `update_lock`).
   - Decisione: Telegram come interfaccia principale (unico modo gratuito con bottoni + feedback scritti); Gemini Notebook resta per le guide PDF e come riserva a portatile spento (fonti da ricaricare a mano).
   - Il portatile viaggia con l'utente: i bot funzionano solo quando è acceso e online. Hosting 24/7 valutato (Google Cloud e2-micro + rclone per Drive, Oracle Cloud, Apps Script) e scartato: il bot gira sul PC o sul telefono (Termux, sotto). Senza bot: MyMaps, Gemini Notebook (fonti = file sciolti in `Aspettativa/`).
   - I tocchi sui bottoni fatti a PC spento vengono salvati all'avvio (`q.answer()` troppo vecchio viene ignorato).
   - **Uso da cellulare (Android, Termux):** attivazione a comando (WiFi, per non consumare dati mobili), non sempre acceso. `IDLE_STOP_SECONDS` (variabile d'ambiente, 0 = disattivato, il default per il PC): se >0 un handler a bassa priorità (`touch_activity`, group=-1) segna ogni update; `main()` controlla ogni 5s e, senza attività per N secondi, esegue subito gli aggiornamenti ancora in coda (`flush_pending_updates`, usa `pending_args` salvato da `schedule_update`) e ferma i bot (`updater.stop`/`stop`/`shutdown`), poi il processo termina da solo (comodo per uno script che fa anche `rclone sync` finale). Limite noto: Telegram tiene i messaggi in coda solo 24 ore, quindi va comunque attivato almeno una volta al giorno.
     Setup telefono (funzionante): Termux + `rclone` (remote `gdrive`, client_id condiviso: crearne uno proprio prima della partenza). `avvia_bot.sh` sta in Bot_Code (quindi si aggiorna da solo) e sul telefono si lancia con `bash ~/bot/avvia_bot.sh` o dal widget Termux:Widget (`~/.shortcuts/`). Fa: `rclone copy` del codice (**mai sync**: cancellava i config locali e script.py ricreava i placeholder) → sposta i config in `~/.viaggio_bot` e adegua i percorsi allo schema nuovo (una volta, idempotente) → migra il vecchio schema locale → `rclone copy --update` telefono→Drive e poi Drive→telefono (vince il file più recente: niente perdite se una sessione è stata chiusa prima del caricamento) → `IDLE_STOP_SECONDS=300` + wake-lock → `python script.py` (con check che `GEMINI_API_KEY` sia impostato) → `rclone copy --update` telefono→Drive. Filtri rclone: solo `BOT_OUTPUT/`, `MyMaps/` e i `.txt` dentro `FONTI/` (niente PDF/documenti sul telefono, anche se stanno insieme ai .txt dentro `FONTI/<Nazione>/`). Migrazione idempotente: vecchi .txt sciolti nella cartella principale del telefono spostati dentro `FONTI/<nome>/`; vecchie cartelle `<Nazione>_bot_output/` sciolte (o `<Nazione>_bot.txt` accanto) spostate dentro `BOT_OUTPUT/`. Con `copy` le cancellazioni (es. CSV rimossi da build_maps) non si propagano: accettato. `GEMINI_API_KEY` in `~/.bashrc` (generata da https://aistudio.google.com/apikey). Bot PC e telefono mai attivi insieme (stesso token = conflitto getUpdates). Vedi `TERMUX_TROUBLESHOOTING.md` per errori da Termux e setup del widget.
     `/spegni` (tutti i bot, anche sul PC): esegue gli aggiornamenti in coda e ferma il processo (`stop_requested`).
6. **[FATTO] Import da blog esterni** (`blog_import.py`, chiamato da `handle_blog_link` in `script.py`): messaggio con la parola `blog` fuori dal link (in qualsiasi bot nazione o in Solo Traveler) -> scarica la pagina (no JavaScript) e classifica i link `<a>` trovati:
   - **Google Maps/short link maps.app.goo.gl/goo.gl**: coordinate lette dall'URL (o dallo short link risolto) o geocodate per nome (Nominatim); la nazione del luogo NON è quella del bot che ha ricevuto il messaggio ma viene letta dalle coordinate (`Geocoder.reverse_country`), perché un blog può parlare di più nazioni insieme. Se il reverse geocoding fallisce (es. errore di rete) la nazione resta vuota ("Senza nazione" nel CSV) invece di ricadere su quella del bot ricevente: si autocorregge rimandando lo stesso blog, niente etichette sbagliate silenziose (bug visto e corretto, vedi Note aperte). Categoria (`guess_category`) indovinata per parole chiave nel nome (es. "Wat/Temple" -> cultura, "Waterfall" -> natura, "Market" -> shopping), "altro" se nessuna combacia (le categorie da reel invece le assegna Gemini dalla caption). Salvati in `blog_places.json` nella cartella `_output` del bot ricevente (dedup per URL); `build_maps.collect_places()` li legge insieme ai `places.json` e finiscono nella cartella/CSV della loro vera nazione (punto 3).
   - **Sottopagine dello stesso blog** (stesso dominio *e* stesso primo pezzo di percorso della pagina di partenza, es. `/posts/...`: generalizza bene perché i blog di viaggio raccolgono gli articoli sotto un prefisso comune, escludendo menu/categorie/pagine di servizio che stanno altrove): titolo + link salvati in `Aspettativa/FONTI/<Nazione-del-bot-ricevente>/<dominio-blog>_link_guide.txt` (righe accumulate, dedup per URL) — da dare a Gemini Notebook per farli importare esplicitamente come fonte (Notebook non segue i link da solo). Il link del blog mandato al bot va invece in `Aspettativa/FONTI/<Nazione-del-bot-ricevente>/blog_sorgenti.txt` (`aaaa-mm-gg - link`, dedup per URL): un file a parte, elenco di tutti i blog mandati, non delle loro sottopagine.
   - Tutto il resto (altri siti/blog, alloggi, social, anchor alla stessa pagina) viene ignorato: Gemini Notebook può già proporre alloggi a prescindere, non serve tracciarli.
   - Risposta Telegram: subito un "⏳ sto analizzando..." (con molti link Maps il reverse geocoding, 1 al secondo, può richiedere qualche minuto), poi il riepilogo (liste troncate a 10 elementi + "…(+N)" per non superare il limite di lunghezza dei messaggi Telegram). Se sono stati aggiunti luoghi Maps riparte lo stesso timer di aggiornamento dei reel (`schedule_update`).
7. **[FATTO] App web di consultazione** (`app.py`, Streamlit, vedi anche `README.md`): quattro tab —
   🗺️ Mappa, 🍜 Cibo (punto 8), 🏛️ Cultura (punto 8), 💬 Chat Gemini — pensata per guardare i dati senza
   aprire Telegram (es. da PC quando il bot non serve un'azione, o per condividere la vista con altri).
   Protetta da password fissa in `PASSWORD` (variabile in cima al file). Posizione utente e filtri
   (nazione/categoria/citazioni/esclusione) in sidebar, condivisi da tutte le tab.
   - **Tab 🗺️ Mappa**: legge i CSV da `MyMaps/<Nazione>/<Nazione>_<livello>.csv` (cartella fratella di
     `BOT_OUTPUT/`, stesso output del punto 3; se non trova dati locali ripiega sui CSV pubblicati su
     GitHub Pages), NON `places.json` (quello lo usa `assistant.py` per il bot Telegram).
     - **Posizione utente**: bottone "📍 Rileva automaticamente" via `streamlit-js-eval` (`get_geolocation`, componente Streamlit vero con round-trip JS↔Python; **non** usare `components.html` con `window.parent.streamlit.setComponentValue`, API inesistente che fallisce silenziosamente — bug visto e corretto), oppure inserimento manuale lat/lon o nome/link Maps (quest'ultimo cache-ato in `session_state` per non rifare la richiesta di rete a ogni interazione).
     - **Ordinamento elenco**: per citazioni (default), per distanza dalla posizione utente, o per distanza dal luogo su cui è stato cliccato "🔍 Zoom" (il riferimento si cerca nel dataframe completo, non in quello filtrato, così resta valido anche se un filtro successivo lo escluderebbe dall'elenco).
     - **Zoom su un luogo**: oltre a centrare la mappa, il suo marker diventa più grande/dorato con bordo nero e un pin a stella sopra, per distinguerlo dagli altri pallini (altrimenti identici per categoria/colore). Bottone "🎯 Centra mappa" (sopra la mappa, sempre visibile) azzera lo zoom e fa ricalcolare i bounds su tutti i luoghi che rispettano i filtri attuali. Stesso stile "evidenziato" riusabile per più luoghi insieme (`highlight_nomi` in `create_map`), usato dalla tab Cibo per mostrare dove si mangia un piatto senza spostare la vista.
     - **Descrizione nel popup/elenco**: sintetizzata da Gemini quando il luogo ha più fonti ridondanti (vedi punto 3, `synthesize_descriptions`), altrimenti grezza.
   - **Tab 🏛️ Cultura**: due sotto-tab. "🏛️ Templi e Luoghi Culturali" legge `places.json` direttamente
     (`load_places_data()`, filtrato su `category == "cultura"`) e mostra `_notes_synth`/`_tips_synth`
     (punto 2) per ogni luogo, con voti/costi/reel numerati. "💡 Consigli Culturali" legge
     `culture_topics.json` (punto 8), raggruppato per argomento con ricerca testuale.
   - **Tab 💬 Chat Gemini**: legge i file **.txt/.md** da `FONTI/<Nazione>/` per le nazioni selezionate
     (`load_fonti_sources`, ogni file troncato a 10.000 caratteri; PDF/immagini nella stessa cartella
     solo elencati, NON letti — richiederebbe l'upload a Gemini, non implementato).
     `NATION_FOLDER_MAPPING` traduce i nomi di nazione del filtro CSV ("Malesia"/"Singapore", un bot per
     nazione reale) nel nome della cartella FONTI che li raccoglie insieme (`Singapore&Malesia`, un bot
     per entrambe): va aggiornato a mano se si aggiunge un'altra combinazione nazione/bot non 1:1.
     Risponde con `build_places.call_gemini_text` (SOLO dai file caricati, niente internet); cronologia
     in `st.session_state.chat_history`, persa al refresh. Client Gemini condiviso per sessione via
     `get_gemini_client()` (`@st.cache_resource`).
   - Deploy indipendente dal bot Telegram (Streamlit Cloud o locale): può girare anche a portatile/telefono spenti, a patto che i CSV siano aggiornati (via Drive o GitHub Pages).
8. **[FATTO] Piatti e cultura per argomento** (`build_dishes.py` / `build_culture.py`, script separati da
   `build_places.py`/`build_maps.py`, da lanciare a mano con `--all` dopo un aggiornamento importante dei
   reel: non ancora collegati al timer automatico del bot). Stessa logica in entrambi: una sola richiesta
   Gemini per lotto (50 luoghi/80 voci), cache che salta il lavoro se i dati non cambiano (hash dei
   blocchi di testo), **nessuna informazione da internet**: solo riorganizzazione del testo già inviato
   dall'utente via Telegram, letto da `places.json`/`tips.json` (niente nuove chiamate RapidAPI).
   - **`build_dishes.py`** -> `dishes.json` nella cartella `_bot_output`: piatti/bevande tipici citati
     nelle note/consigli/contro dei luoghi di categoria "cibo" (lavora a livello di singola mention, non
     sui campi aggregati del luogo, per tenere l'esatta corrispondenza voce -> reel). Un lotto di 50
     luoghi per chiamata Gemini (`DISH_PROMPT`), che unisce già i duplicati dentro al lotto (es. lo
     stesso piatto citato in 3 ristoranti diversi); tra lotti diversi la fusione resta solo per nome
     normalizzato esatto (limite noto, v1). Scarta cibi/bevande generici ovunque nel mondo (caffè, uova,
     toast) a meno di un nome/preparazione locale specifica. Ogni piatto porta: nome, alias, descrizione
     sintetica, luoghi dove si trova, nazioni coinvolte (`countries`, per il filtro nazione dell'app),
     reel fonte e `thumbnail_url`.
   - **`thumbnail_url` (`attach_thumbnails`)**: UNICA eccezione in tutto il progetto alla regola "niente
     internet", voluta esplicitamente dall'utente e limitata alla sola foto (nome/descrizione/luoghi
     restano sempre e solo dai reel). Prima Wikipedia (`_wiki_thumbnail`: `opensearch` + REST summary,
     no API key, foto Wikimedia Commons); se non trova nulla, fallback su Openverse
     (`_openverse_thumbnail`, aggrega Wikimedia Commons/Flickr Commons/altre fonti libere, no API key,
     limite account anonimo 20/min-200/giorno, usato solo come fallback quindi raramente ci si avvicina).
     Scartata l'alternativa "genera l'immagine con Gemini" (Imagen/Nano Banana): **non è nel tier
     gratuito** (serve fatturazione, ~$0.03-0.15/immagine secondo risoluzione — verificato su
     ai.google.dev/gemini-api/docs/pricing), e sarebbe comunque un'illustrazione sintetica non una foto
     vera. Cache condivisa tra tutte le nazioni in `BOT_OUTPUT/dish_thumbnails_cache.json` (non per bot:
     molti piatti si ripetono, es. satay in Thailandia e Singapore&Malesia), una ricerca sola per nome
     normalizzato, mai ripetuta. **Importante**: gli errori di rete/rate-limit di ENTRAMBE le fonti
     ritornano `None` e NON vengono messi in cache (si ritenta al giro dopo), a differenza del "non
     trovato" (`""`, cache per sempre) — bug visto e corretto: la prima run su ~180 piatti ha quasi
     azzerato la copertura perché Wikipedia throttla dopo poche decine di richieste rapide, e il codice
     iniziale cachava gli errori come "non trovata" in modo permanente. Pausa di 2s dopo Wikipedia e
     1.5s dopo Openverse tra le richieste, per cortesia verso le API pubbliche. Risultato tipico: ~35%
     di copertura solo-Wikipedia, più alto con il fallback Openverse; fallisce ancora quando il "nome"
     del piatto estratto da Gemini è in realtà una descrizione invece del nome vero (es. "noodles con
     polpette di pesce" invece di un nome ricercabile) — nessuna delle due fonti trova nulla in quel caso.
   - **`build_culture.py`** -> `culture_topics.json`: stessi tips/cons di `write_culture()` (punto 2),
     ma raggruppati per ARGOMENTO (`CULTURE_TOPICS`: cibo e bevande, vestiario e rispetto, religione e
     tradizioni, festività, lingua, sicurezza e truffe, trasporti, soldi e contrattazione, mentalità e
     usi locali, altro) invece che per città/luogo. **Scarta la logistica di un singolo luogo** (orari,
     prezzi, come arrivarci: quella resta nella scheda del luogo) e tiene solo ciò che ha valore
     culturale/di curiosità — senza questo filtro il raggruppamento era inutilizzabile (666 voci
     grezze -> 73 utili su Thailandia, il resto finiva tutto nel bidone "altro"). Un lotto di 80 voci
     per chiamata (`CULTURE_PROMPT`); stesso limite di fusione solo-dentro-il-lotto di `build_dishes.py`.
   - **Quota Gemini**: entrambi usano `build_places.call_gemini_json` (funzione condivisa, estratta da
     `extract_with_llm`): stesso retry/backoff sul limite al minuto (429 senza "PerDay" in risposta) e
     `QuotaExhausted` sul limite giornaliero. Se un lotto fallisce (quota o altro errore), la cache NON
     viene salvata (altrimenti quel lotto non verrebbe più ritentato finché i dati non cambiano — bug
     visto e corretto: un 429 a metà `--all` aveva perso ~1/3 dei piatti di una nazione finché non si è
     svuotata a mano la cache per quella nazione). Sulla quota giornaliera esaurita, le nazioni successive
     in `--all` saltano Gemini e rigenerano solo dalla cache (stesso pattern di `build_places.main`).

## Evoluzione 1 (futuro)
- Ricerca web autonoma (senza un link specifico dell'utente) e PDF: i PDF restano fonti manuali per Notebook, i link da blog sono gestiti dal punto 6.
- Tracciamento luoghi visitati + feedback per adattare i consigli futuri (campi già previsti in places.json).
- Fusione cross-lotto di piatti/consigli duplicati in `build_dishes.py`/`build_culture.py` (oggi si uniscono solo dentro lo stesso lotto Gemini, vedi punto 8): da valutare se diventa fastidioso sulle nazioni con tanti dati.
- "Crea il tuo itinerario": funzionalità non ancora progettata, da affrontare dopo aver consolidato cibo/cultura.
- **[FATTO, v1]** Chatbot nell'app (tab 💬 Chat Gemini, punto 7) che risponde SOLO sui file `.txt/.md` di `FONTI/<Nazione>/`. Limiti noti da affinare: PDF/immagini nella stessa cartella non vengono letti (solo elencati); nessuna persistenza della cronologia tra sessioni; nessun uso di `places.json`/`dishes.json`/`culture_topics.json` come fonte (solo i file testuali di FONTI).
- Per ogni luogo, informazioni storiche/politiche/culturali non già nella descrizione: l'utente vuole SOLO dati dai reel inviati via Telegram, non ricerche su internet (niente Wikipedia/grounding) — quindi va alimentata dagli stessi dati già raccolti (es. estendendo `build_culture.py` a legare le voci anche al luogo, non solo all'argomento), non da fonti esterne. L'unica eccezione concessa finora è la miniatura dei piatti (punto 8, solo immagine, non testo): non estenderla ad altro senza chiederlo esplicitamente.

## Comandi Telegram

### Bot nazione (con estrazione luoghi e assistente)

**Salvataggio e analisi contenuti:**
- Link Instagram (reel/post/story) — salva il link e la caption
- Link Instagram + **"post"** — estrae testo da qualsiasi media nel post (immagini se carosello, video se video singolo, entrambi se post misto). Gemini legge il testo.
- Link Instagram + **"video"** — estrae solo testo dal video (testi a schermo, non immagini)
- Link blog — scarica la pagina e classifica i link Maps trovati; salva anche titoli sottopagine dello stesso dominio
- **Multipli link separati da `;` o righe vuote** — elabora sequenzialmente fino a 50 link in un messaggio. Separatori:
  - Con `;`: `link1 post; link2; link3 video` (tutto in una riga)
  - Con righe vuote: ogni link su una riga, gruppi separati da righe vuote
  - Ogni link ha i suoi modificatori indipendenti (post/video). Messaggio progressivo durante l'elaborazione, aggiornamento places/CSV una volta sola alla fine.

**Assistente consigli:**
- `/consiglia` — questionario a bottoni (zona → tempo → mood → budget; prima la nazione se il bot ne ha più)
- `/aiuto` — mostra le istruzioni

**Gestione errori e aggiornamenti:**
- `/errori` — visualizza i post con errori durante la lettura del testo (immagini/video non estratti)
- `/skip <shortcode>` — rimuove manualmente un errore ritenuto legittimo (es. post senza immagini)
- `/retryall` — ritenta TUTTI gli errori manualmente (chiede di reinviare link con `post`/`video`)
- `/autoretry` — ritenta TUTTI gli errori automaticamente ricreando i comandi originali:
  - Legge il `requested_media` salvato per ogni errore (quale media era stato richiesto)
  - Ricostruisce il comando originale: `{link} post` se era "both", `{link} video` se era "video", `{link} post` se era "images", solo `{link}` se era "none"
  - Rilancia il comando come se l'utente lo avesse rinviato — tutto il flusso (salvataggio reel, estrazione media, gestione errori) funziona identico
  - Se il reel è già completamente salvato (anche da un precedente tentativo manuale), l'errore viene considerato risolto e rimosso automaticamente
- `/annulla` — cancella il processo di retry in corso
- `/aggiorna` — forza un aggiornamento immediato di places.json/CSV (per default parte ogni 3 min dopo un reel)

**Sistema:**
- `/spegni` — esegue gli aggiornamenti in coda e ferma il bot
- `INFO` — risposta speciale che non va all'assistente (usata per visualizzare lo stato delle API)

### Bot consigli (`"type": "tips"` in config)
Solo `/spegni` (niente luoghi, niente mappa, niente assistente).

### Bot assistente (`"type": "assistant"` in config)
`/consiglia`, `/aiuto`, `/spegni` (niente estrazione reel, niente mappa).

## Decisioni prese
- Estrazione dati con LLM (le caption sono testo libero multilingua, regex insufficiente).
- Gemini scelto rispetto a Ollama per qualità/semplicità. Quota gratuita: i modelli Flash-Lite hanno molte più richieste/giorno dei Flash completi; controllare i limiti attuali su Google AI Studio perché cambiano spesso.
- Le caption sono pubbliche, quindi nessun problema di privacy col tier gratuito.

## Convenzioni
- Codice Python, testi e messaggi utente in **italiano**.
- **Mai** scrivere token/chiavi nel codice o in file committati: usare variabili d'ambiente (`GEMINI_API_KEY`) o file di config esclusi da git (`.gitignore`: `bots_config.json`, `api_config.json`, `*_output/`).
- Modifiche piccole e incrementali; mantenere gli script eseguibili da riga di comando su Windows.

## Tracciamento errori parziali
- Ogni errore in `_errori.json` ha un campo `requested_media` ("images", "video", "both", "none") che indica quale media era richiesto quando è fallito.
- **Comportamento `/autoretry` per media non trovati**:
  - `images_missing` / `video_missing`: ritenta UNA SOLA VOLTA (1 credito API) per verificare se il media è stato aggiunto
  - Se ancora non trova media → cambia tipo a `images_not_found` / `video_not_found` e rimane nella lista errori (non spreca Gemini)
  - Se trova media → estrae il testo con Gemini e rimuove l'errore
  - `images` / `video` (fallimenti di estrazione): ritenta normalmente con API + Gemini
- **Decisione dell'utente**: con `/skip <shortcode>` puoi rimuovere manualmente un errore ritenuto legittimo (es. post senza immagini)
- Il txt viene sempre salvato (caption è obbligatoria); il `.json immagini` si popola solo se il testo è estratto con successo

## Note aperte
- `bots_config.json` e `api_config.json` contengono ancora token Telegram e chiave RapidAPI in chiaro: da rigenerare (BotFather / RapidAPI) e spostare in variabili d'ambiente. Restano solo locali su ogni dispositivo (`~/.viaggio_bot`), non vanno mai copiati su Drive/cloud: contengono anche `txt_file_path`, diverso per dispositivo.
  I valori di default in `script.py` (`load_bots_config` / `load_api_config`, scritti solo se i file mancano) sono placeholder (`METTI_QUI_…`): se il bot dice "token rejected", i config non sono nel posto giusto.
- Il bot "Singapore&Malesia_bot" nel config ha lo stesso link `t.me` di quello Thailandia, ma i token sono diversi: è solo il campo `Telegram_bot` da correggere.
- `build_places.py` testato su Singapore&Malesia (104 reel -> 273 luoghi). Output su Drive: `G:\Il mio Drive\Viaggi\Aspettativa\BOT_OUTPUT\<Nazione>_bot_output\`.
  Backup della prima versione: `extraction_cache_v1_backup.json`, `places_v1_backup.json`.
- `parse_reels` ignora il testo dopo l'ultimo separatore `-----` (reel che il bot sta ancora scrivendo): i txt devono sempre finire col separatore.
- `Solo_Traveler_bot_output`: i vecchi output "a luoghi" sono in `*_luoghi_backup.*`; i 3 reel vanno rianalizzati con Gemini (`python build_places.py --all` con `GEMINI_API_KEY`) per avere gli argomenti dei consigli.
- `build_maps.py` cancella da `MyMaps/` i CSV di nazioni che non hanno più luoghi.
- Gemini può a volte scrivere in `country` il nome di una città invece della nazione (visto con "Luang Prabang" invece di "Laos" in Laos_bot_output, creava un CSV/layer fantasma): se `build_maps` segnala più nazioni del previsto, controllare `country` nei vari `places.json`/`extraction_cache.json` (va corretto in entrambi, altrimenti il prossimo aggiornamento lo ricrea dalla cache).
- Stesso tipo di errore visto anche in `blog_places.json` (Thailandia_bot_output aveva 119 luoghi di tutte le nazioni etichettati "Thailandia"): causato da una versione precedente del codice che, se il reverse geocoding falliva, usava la nazione del bot ricevente come ripiego. Corretto (niente più ripiego, vedi punto 6 della pipeline); i dati vecchi vanno ri-geocodati a mano una volta (`Geocoder.reverse_country` su ogni voce) se capita di nuovo con dati creati prima di questa correzione.
- `Aspettativa/BOT_OUTPUT/MyMaps/` è un percorso abbandonato (CSV piatti vecchio schema, mai più scritti da `build_maps.py` dopo che `mymaps_dir()` è passato a `Aspettativa/MyMaps/`, fratella di `BOT_OUTPUT/`): una vecchia versione di `app.py` leggeva da lì, mostrando dati/coordinate non più aggiornati (bug visto e corretto — un luogo geocodato male in `MyMaps/geocode_cache.json` sembrava "non corretto" dopo aver sistemato la cache, perché l'app leggeva ancora i CSV vecchi). Cartella sicura da cancellare; ha anche un suo `geocode_cache.json` separato, mai usato da `build_maps.py` reale: se correggi un luogo, assicurati di editare quello in `Aspettativa/MyMaps/`, non quello dentro `BOT_OUTPUT/`.
- I `print()` con emoji in `build_places.py`/`build_maps.py` (es. nei messaggi di errore quando una chiamata Gemini fallisce) crashavano con `UnicodeEncodeError` sulla codepage cp1252 di default della console Windows, interrompendo tutto l'aggiornamento per un singolo errore transitorio (es. Gemini 503). Corretto forzando `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` in cima a entrambi i file.
- **Collisione di id tra luoghi con città diverse ma stesso nome+nazione** (`group_places`, visto con "Batu Caves" a Kuala Lumpur vs Selangor — stesso posto reale, Gemini estrae la città in modo incoerente tra reel diversi): l'id è `country_name` senza città, quindi due gruppi tenuti separati da `compatible()` (città diverse = non compatibili, niente fusione) finivano con l'id IDENTICO, sovrascrivendosi a vicenda in `preserve_state`/`description_cache.json`. Corretto: dopo aver generato i gruppi, un id duplicato viene disambiguato aggiungendo la città SOLO ai gruppi in collisione (gli altri mantengono l'id storico, nessuna perdita di stato/cache per il resto). Da notare: in questo caso specifico il risultato finale nel CSV resta comunque un'unica riga, perché un terzo luogo (lo stesso "Batu Caves" importato da blog, città vuota) fa da ponte in `merge_cross_source_duplicates` — essendo compatibile con entrambe le città (campo vuoto = compatibile con tutto), unisce per transitività i due gruppi che altrimenti `compatible()` avrebbe tenuto separati. Non è garantito che funzioni sempre così: se càpita un luogo con città davvero incompatibili e nessun "ponte", resteranno due righe/pin distinti nel CSV — da valutare caso per caso se infastidisce (si potrebbe allentare `compatible()` per i casi di nome identico, ma non ancora fatto: rischio di unire luoghi omonimi in città diverse per errore).
- **`google.generativeai` (SDK deprecato) rotto su questa rete**: usato per un periodo sia in `synthesize_cultural_place_text()` (build_places.py) che nella tab 💬 Chat Gemini di `app.py`, in entrambi i casi con chiamate dirette al modello invece che tramite `call_gemini_json`/`call_gemini_text`. Su questa rete (firewall/proxy che intercetta TLS, lo stesso motivo per cui serve `truststore` altrove) quella libreria falliva la verifica del certificato SSL in modo massiccio e bloccava il processo per minuti (in `build_places.py`, PRIMA ancora di salvare `places.json`; in `app.py`, la chat restava bloccata su "Sto pensando..." senza mai rispondere). `truststore.inject_into_ssl()` non lo risolve: patcha solo il modulo `ssl` di Python, non il trasporto nativo usato da `google.generativeai`. **Entrambi corretti**: ora usano `google.genai` (SDK nuovo, via REST, funziona bene con `truststore`) tramite `build_places.call_gemini_json`/`call_gemini_text` (helper condivisi con lo stesso retry/backoff; `call_gemini_text` per risposte libere non-JSON, usata dalla chat). `app.py` crea il client con `get_gemini_client()` (`@st.cache_resource`, un solo client per sessione). Se si trova altro codice con `import google.generativeai`, va migrato allo stesso modo.
