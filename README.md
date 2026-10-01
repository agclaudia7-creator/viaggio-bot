# 🗺️ Mappa Interattiva - Viaggio Sud-Est Asiatico

App Streamlit per visualizzare e esplorare interattivamente i luoghi del viaggio nel Sud-Est asiatico, filtrati per nazione, categoria e critiche, con ordinamento per citazioni e vicinanza.

## ⚡ Uso Rapido

1. **Accedi**: Inserisci la password (`viaggi2026`)
2. **Filtra**: Seleziona nazione, categoria, intervallo di citazioni (filtri condivisi tra tutte le tab)
3. **Localizzati**: Clicca "📍 Rileva automaticamente" per il GPS del browser, oppure inserisci manualmente lat/lon o nome città/link Maps
4. **Esplora**: quattro tab — 🗺️ Mappa (luoghi), 🍜 Cibo (piatti tipici), 🏛️ Cultura (templi + consigli), 💬 Chat Gemini (domande sulle fonti)
5. **Aggiorna**: Dopo `/aggiorna` nel bot, clicca "🔄 Ricarica dati" per sincronizzare i CSV
6. **Chat**: Nella tab 💬 Chat Gemini, fai domande sulle tue fonti di viaggio — Gemini risponde basandosi SOLO su quel che hai salvato

## 🎯 Funzionalità

L'app ha quattro tab: **🗺️ Mappa** (luoghi), **🍜 Cibo** (piatti/bevande tipici), **🏛️ Cultura** (templi + consigli per argomento) e **💬 Chat Gemini** (domande assistite sulle fonti locali). Posizione e filtri nella sidebar sono condivisi da tutte.

### Mappa Interattiva
- Visualizzazione mappa con marker colorati per categoria (natura, cultura, shopping, cibo, alloggio, altro)
- Zoom automatico sui luoghi selezionati: il pallino diventa più grande/dorato e viene affiancato da un pin a stella, per distinguerlo subito dagli altri
- Marker personalizzati per la tua posizione
- Pop-up con informazioni rapide su ogni luogo (incluso la distanza, se hai impostato una posizione)
- Bottone di geolocalizzazione del plugin Leaflet (📍 in alto a sinistra della mappa, solo per centrare la vista — non è legato alla posizione usata per ordinare l'elenco, vedi sotto)
- Bottone "🎯 Centra mappa" (sopra la mappa): azzera lo zoom su un luogo specifico e ricentra/riadatta la vista su tutti i luoghi che rispettano i filtri attuali
- Tile map OpenStreetMap (gratuita, niente API key richiesta)
- Bottone "🔄 Ricarica dati" per aggiornare i CSV dopo `/aggiorna` nel bot

### Filtri Avanzati
- **Nazione**: Seleziona una o più nazioni da visualizzare
- **Categoria**: Filtra per tipo di luogo (natura, cultura, shopping, cibo, alloggio, altro)
- **Citazioni**: Slider per mostrare solo i luoghi citati N volte nei reel
- **Esclusione**: Esclude i luoghi che contengono un termine nel nome, posizione o descrizione
- **Ricerca testuale**: Cerca nel nome e descrizione dei luoghi

### Posizione Utente
- **Bottone "📍 Rileva automaticamente"**: chiede il permesso di geolocalizzazione al browser (componente `streamlit-js-eval`) e usa le coordinate GPS restituite per centrare la mappa
- **Nessuna**: Non inserire posizione
- **Manuale (Lat/Lon)**: Inserisci latitudine e longitudine direttamente
- **Nome/Link Maps**: Scrivi il nome di una città o incolla un link Google Maps (anche short link); il risultato viene ricordato finché non cambi testo, senza richieste di rete ripetute a ogni interazione

### Elenco Luoghi
- Ordinamento per:
  - **Citazioni ↓**: Più citati prima (default)
  - **Distanza (dalla mia posizione)**: Dal più vicino al più lontano (visibile solo se hai impostato una posizione)
  - **Distanza (da \<luogo\>)**: Dal più vicino al più lontano rispetto al luogo su cui hai cliccato "🔍 Zoom" (visibile solo dopo aver zoommato un luogo con coordinate)
- Espandi ogni luogo per vedere descrizione completa
- Bottoni **Zoom** e **Google Maps** visibili solo se il luogo ha coordinate
- Link cliccabili nella descrizione
- Indicazione della distanza nei pop-up della mappa (se hai impostato una posizione)
- Niente spazi vuoti per luoghi senza coordinate

### Gestione Luoghi Esclusi
- Visualizza l'elenco dei luoghi esclusi dal filtro
- Reinclugi singoli luoghi con checkbox
- Zoom su luogo escluso
- Link diretto a Google Maps per ogni escluso

### Tab 🍜 Cibo
- Elenco dei piatti/bevande tipici (`dishes.json`, generato da `build_dishes.py`, vedi sotto), filtrato
  sulle stesse nazioni scelte in sidebar, ordinato per numero di citazioni, con ricerca per nome
- Miniatura accanto al nome (lista e scheda): foto da Wikimedia Commons, con Openverse come fallback
  gratis (anche lui senza chiave API) se Wikipedia non trova nulla — l'**unica** eccezione nel progetto
  alla regola "niente dati da internet", vale solo per l'immagine, non per nome/descrizione/luoghi
  (quelli restano sempre e solo dai reel). Valutata e scartata l'alternativa "genera l'immagine con
  Gemini": non è nel tier gratuito (richiede fatturazione, $0.03-0.15 a immagine) e sarebbe comunque
  un'illustrazione sintetica, non una foto vera. Niente foto se il piatto non si trova in nessuna delle
  due fonti (es. nomi troppo locali o descrittivi invece che il nome vero del piatto)
- Click su un piatto -> scheda con alias, descrizione sintetica e l'elenco dei luoghi dove si trova
- Bottone "📍 Evidenzia questi posti sulla mappa": i luoghi del piatto diventano dorati nella tab Mappa
  (stesso stile del luogo zoommato), senza spostare la vista — basta aprire la tab Mappa dopo

### Tab 🏛️ Cultura
- Divisa in due sottotab:
  - **🏛️ Templi e Luoghi Culturali**: Elenco di tutti i luoghi categoria "cultura" con descrizioni approfondite:
    - Descrizione sintetizzata da Gemini (senza ripetizioni, un paragrafo unico discorsivo)
    - Consigli pratici sintetizzati (es. "Visita al mattino presto... Copri spalle e ginocchia...")
    - Criticità evidenziate con ⚠️
    - Voti, costi, link ai reel (numerati: "Link 1", "Link 2", "Link 3", expander per i restanti)
  - **💡 Consigli Culturali**: Consigli e curiosità raggruppati per ARGOMENTO: cibo e bevande, vestiario e rispetto, religione e tradizioni, festività, lingua, sicurezza e truffe, trasporti, soldi e contrattazione, mentalità e usi locali
- Scarta la logistica di un singolo luogo (orari, prezzi, come arrivarci): quella resta nella scheda del luogo nella tab Mappa

### Tab 💬 Chat Gemini
- Chat interattiva, cronologia tenuta solo nella sessione (si perde al refresh/riavvio)
- Legge i file **.txt/.md** da `FONTI/<Nazione>/` per le nazioni selezionate in sidebar (ogni file
  troncato a 10.000 caratteri se più lungo). I PDF/immagini nella stessa cartella vengono elencati ma
  **non letti** (solo un segnaposto col nome del file: leggerli richiederebbe l'upload a Gemini, non
  ancora implementato). `NATION_FOLDER_MAPPING` traduce "Malesia"/"Singapore" (nomi usati dal filtro
  nazione, uno per CSV) nella cartella `FONTI/Singapore&Malesia/` (nome del bot che le raccoglie
  insieme): se in futuro si aggiunge un'altra nazione con lo stesso schema va aggiunta qui a mano.
- Gemini risponde SOLO basandosi su questi file (niente info da internet) — usa `google.genai` tramite
  `build_places.call_gemini_text` (vedi CLAUDE.md: una versione precedente usava `google.generativeai`,
  deprecato, che su reti con firewall/proxy TLS poteva bloccare la chat all'infinito)
- Expander "📁 File caricati come fonti" mostra quali file vengono usati
- Utile per domande su logistica, cultura, curiosità non in places.json

### Autenticazione
- Password di accesso (configurabile in `app.py`)

## 🛠️ Installazione

### Requisiti
- Python 3.8+
- Dipendenze (vedi `requirements.txt`):
  ```
  streamlit>=1.28.0
  folium>=0.14.0
  streamlit-folium>=0.9.0
  streamlit-js-eval>=0.1.7
  pandas>=1.5.0
  geopy>=2.3.0
  requests>=2.31.0
  google-genai>=0.3.0
  ```
- **Per la Chat Gemini**: Variabile d'ambiente `GEMINI_API_KEY` (generata da https://aistudio.google.com/apikey)

### Setup
1. Clona il repository
2. Installa le dipendenze: `pip install -r requirements.txt`
3. Configura le cartelle dati (vedi "Struttura dati" sotto)
4. Cambia la password in `app.py` (variabile `PASSWORD`, vicino all'inizio del file)
5. Esegui: `streamlit run app.py`

## 📁 Struttura Dati

L'app legge da due fonti (in questo ordine di priorità):

### Locale (Cartella personale)
```
G:\Il mio Drive\Viaggi\Aspettativa\
├── BOT_OUTPUT/
│   ├── Thailandia_bot_output/
│   │   ├── places.json
│   │   ├── dishes.json          <- tab Cibo (build_dishes.py)
│   │   └── culture_topics.json  <- tab Cultura (build_culture.py)
│   ├── Laos_bot_output/
│   │   └── places.json
│   └── ...
└── MyMaps/                        <- fratella di BOT_OUTPUT/, NON dentro
    ├── geocode_cache.json
    ├── description_cache.json
    ├── Thailandia/
    │   ├── Thailandia_bassissima.csv
    │   ├── Thailandia_bassa.csv
    │   ├── Thailandia_media.csv
    │   ├── Thailandia_alta.csv
    │   └── Thailandia_altissima.csv
    ├── Laos/
    │   └── ...
    └── ...
```

L'app carica automaticamente tutti i CSV dentro `MyMaps/<Nazione>/` (`build_maps.py` scrive un file per fascia di citazioni; non tutte le nazioni hanno tutte le fasce, vedi `build_maps.py` per i dettagli). **Attenzione**: una vecchia versione dell'app leggeva da `BOT_OUTPUT/MyMaps/` (percorso ormai abbandonato, con CSV piatti non più aggiornati): se trovi quella cartella con dati vecchi, è sicuro cancellarla.

### Online (GitHub Pages)
Se non trova file locali, carica dai CSV su GitHub Pages (`gh-pages` branch):
```
https://raw.githubusercontent.com/agclaudia7-creator/viaggio-bot/gh-pages/
```

## 📊 Formato Dati (CSV)

Ogni CSV contiene:
- **Nome**: Nome del luogo
- **Città**: Città in cui si trova
- **Posizione**: Descrizione completa della posizione (testo che Google Maps cerca)
- **Categoria**: Una di {natura, cultura, shopping, cibo, alloggio, altro}
- **Citazioni**: Numero di reel che citano il luogo
- **Descrizione**: 
  - Citazioni (#) + Costi/voto creator/giorni consigliati
  - Descrizione del luogo (sintetizzata senza ripetizioni se citato da molti reel)
  - Consigli/criticità
  - Link ai reel che lo citano
  - Per i luoghi culturali: ulteriormente sintetizzata da Gemini in `build_places.py` -> `_notes_synth` e `_tips_synth` (salvati in places.json per la tab Cultura)
- **Lat, Lon**: Coordinate (solo informative)
- **Nazione_CSV**: Nazione di appartenenza (dedotta dalla sottocartella di `MyMaps/`, non dal nome del file)

Un luogo citato sia nei reel che in un blog importato (vedi pipeline bot, punto 6) non compare più come due righe separate ("Batu Caves" e "Batu cave" erano un esempio reale): `build_maps.merge_cross_source_duplicates` le confronta e unisce (fuzzy match + Gemini per i casi ambigui) prima di scrivere i CSV.

## 🍜🏛️ Piatti e Cultura (`build_dishes.py` / `build_culture.py`)

Due script separati da `build_places.py`/`build_maps.py`, stessa logica (una richiesta Gemini a lotto,
cache che salta il lavoro se i dati non cambiano, **nessuna informazione da internet**: solo
riorganizzazione del testo già inviato via Telegram, letto da `places.json`/`tips.json`):

- `python build_dishes.py --all` -> `dishes.json` per nazione: piatti/bevande tipici citati nelle note
  dei luoghi di categoria "cibo", con alias, descrizione sintetica, luoghi dove si trovano e reel fonte.
  Aggiunge anche `thumbnail_url` da Wikimedia Commons, con Openverse come fallback (unica eccezione a
  "niente internet", solo per la foto, vedi tab Cibo sopra): cache condivisa tra nazioni in
  `BOT_OUTPUT/dish_thumbnails_cache.json`, una ricerca sola per piatto, con pausa di cortesia tra le
  richieste (gli errori di rete/rate-limit non vengono cachati, si ritentano al giro dopo)
- `python build_culture.py --all` -> `culture_topics.json` per nazione: consigli/criticità raggruppati
  per argomento invece che per città, scartando la logistica di un singolo luogo

Non sono ancora collegati al timer automatico del bot (`UPDATE_DELAY_SECONDS`): vanno rilanciati a mano
dopo un aggiornamento importante dei reel. Limite noto: i duplicati (stesso piatto/consiglio con parole
diverse) si uniscono solo dentro lo stesso lotto Gemini, non tra lotti diversi — su nazioni con molti
dati può restarne qualcuno.

## 🔌 Integrazione con il Bot

I CSV vengono generati da `build_maps.py` ogni volta che il bot aggiorna i luoghi dai reel Instagram. L'app legge i CSV aggiornati automaticamente al ricaricamento della pagina.

## 🎨 Personalizzazione

- **Colori categorie**: Modifica il dizionario `category_colors` dentro `create_map()`
- **Tile map**: Cambia il parametro `tiles=` nella creazione di `folium.Map()` dentro `create_map()`
  
### Tile map disponibili (senza API key)
- **OpenStreetMap** (predefinito) - Stile base OSM, completamente gratuito e affidabile
- **Stamen TonerLite** - Versione minimalista, stile mappa stradale
- **Stamen Terrain** - Topografia con rilievi
- **Esri WorldStreetMap** - Stile Esri stradale

**Nota**: CartoDB positron/voyager richiedono API key per deploy in produzione. OpenStreetMap è la scelta più sicura e gratuita.

## 🔐 Sicurezza e Privacy

- **Password**: Salva direttamente nel codice (`app.py`, variabile `PASSWORD`). Per cambiare: modifica `PASSWORD = "..."` e redeploy.
- **Dati**: I CSV contengono solo informazioni pubbliche (reel Instagram pubblici).
- **Geolocalizzazione**: Richiesta tramite `streamlit-js-eval`, che esegue `navigator.geolocation` nel browser e restituisce le coordinate solo alla sessione Streamlit corrente (`st.session_state`); non vengono salvate su disco né inviate altrove.
- **Niente account**: Non richiede login, solo password di accesso.
- **Storage**: Niente salvataggio di password utente o dati personali.

## 🚀 Deploy

### Locale
```bash
streamlit run app.py
```

### Streamlit Cloud (online)
1. Pusha il repository su GitHub
2. Vai su [streamlit.io](https://streamlit.io/cloud) e collega il repo
3. Configura la cartella dati:
   - Monta la cartella di Google Drive con `rclone` o carica i CSV direttamente nel repo
   - Oppure usa la versione online da GitHub Pages (niente setup locale)

## 📝 Note

- **Cache dati**: L'app cache i dati tramite `@st.cache_data`. Se aggiorni i dati nel bot, clicca "🔄 Ricarica dati" per sincronizzare, oppure usa `Ctrl+Shift+R` per pulire la cache del browser.
- **Geolocalizzazione**: Il bottone "📍 Rileva automaticamente" richiede accesso al GPS del browser. Funziona su `http://localhost:*` (sviluppo) e HTTPS (produzione); non su HTTP non-localhost per motivi di sicurezza.
- **Sintesi culturale**: I testi dei templi (descrizioni e consigli) vengono sintetizzati da Gemini in `build_places.py` quando il bot fa `/aggiorna`. Niente elaborazione al runtime in Streamlit.
- **Deduplicazione**: Testi molto simili (fuzzy match 85%) vengono considerati duplicati e rimossi automaticamente (es. "272 gradini colorati" ripetuto 4 volte diventa 1 volta sola).
- **Google Maps**: I link usano il parametro `query=` con il nome del luogo, per compatibilità con il metodo di Telegram.
- **Filtro Citazioni**: Il filtro slider nella sidebar filtra sia la mappa che l'elenco.
- **Esclusione termini**: I luoghi esclusi rimangono ricaricabili dalla checkbox "Mostra esclusi".
- **Chat Gemini**: Legge i file da `FONTI/<Nazione>/` e risponde SOLO con le informazioni trovate lì. Utile per domande che non hanno risposta in places.json (es. consigli di viaggio generici, storia della nazione, etc.).

## 📄 Licenza

Uso personale. Dati da reel Instagram pubblici.
