# 🗺️ Mappa Interattiva - Viaggio Sud-Est Asiatico

App Streamlit per visualizzare e esplorare interattivamente i luoghi del viaggio nel Sud-Est asiatico, filtrati per nazione, categoria e critiche, con ordinamento per citazioni e vicinanza.

## ⚡ Uso Rapido

1. **Accedi**: Inserisci la password (`viaggi2026`)
2. **Filtra**: Seleziona nazione, categoria, intervallo di citazioni
3. **Localizzati**: Clicca "📍 Aggiungi posizione" per GPS, oppure inserisci manualmente lat/lon o nome città
4. **Esplora**: Clicca su un luogo per vedere dettagli, bottoni Zoom e Google Maps
5. **Aggiorna**: Dopo `/aggiorna` nel bot, clicca "🔄 Ricarica dati" per sincronizzare i CSV

## 🎯 Funzionalità

### Mappa Interattiva
- Visualizzazione mappa con marker colorati per categoria (natura, cultura, shopping, cibo, alloggio, altro)
- Zoom automatico sui luoghi selezionati
- Marker personalizzati per la tua posizione
- Pop-up con informazioni rapide su ogni luogo
- Pulsante di geolocalizzazione automatica nel browser (📍 in alto a sinistra della mappa)
- Tile map OpenStreetMap (gratuita, niente API key richiesta)
- Bottone "🔄 Ricarica dati" per aggiornare i CSV dopo `/aggiorna` nel bot

### Filtri Avanzati
- **Nazione**: Seleziona una o più nazioni da visualizzare
- **Categoria**: Filtra per tipo di luogo (natura, cultura, shopping, cibo, alloggio, altro)
- **Citazioni**: Slider per mostrare solo i luoghi citati N volte nei reel
- **Esclusione**: Esclude i luoghi che contengono un termine nel nome, posizione o descrizione
- **Ricerca testuale**: Cerca nel nome e descrizione dei luoghi

### Posizione Utente
- **Bottone "📍 Aggiungi posizione"**: Attiva la geolocalizzazione del browser (GPS). La posizione viene salvata e la mappa si centra su di essa
- **Nessuna**: Non inserire posizione
- **Manuale (Lat/Lon)**: Inserisci latitudine e longitudine direttamente
- **Nome/Link Maps**: Scrivi il nome di una città o incolla un link Google Maps (anche short link)

### Elenco Luoghi
- Ordinamento per:
  - **Citazioni ↓**: Più citati prima (default)
  - **Distanza**: Dal più vicino al più lontano (visibile solo se posizione inserita)
- Espandi ogni luogo per vedere descrizione completa
- Bottoni **Zoom** e **Google Maps** visibili solo se il luogo ha coordinate
- Link cliccabili nella descrizione
- Indicazione della distanza (se posizione inserita)
- Niente spazi vuoti per luoghi senza coordinate

### Gestione Luoghi Esclusi
- Visualizza l'elenco dei luoghi esclusi dal filtro
- Reinclugi singoli luoghi con checkbox
- Zoom su luogo escluso
- Link diretto a Google Maps per ogni escluso

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
  pandas>=1.5.0
  geopy>=2.3.0
  requests>=2.31.0
  ```

### Setup
1. Clona il repository
2. Installa le dipendenze: `pip install -r requirements.txt`
3. Configura le cartelle dati (vedi "Struttura dati" sotto)
4. Cambia la password in `app.py` (linea ~18)
5. Esegui: `streamlit run app.py`

## 📁 Struttura Dati

L'app legge da due fonti (in questo ordine di priorità):

### Locale (Cartella personale)
```
G:\Il mio Drive\Viaggi\Aspettativa\
├── BOT_OUTPUT/
│   ├── Thailandia_bot_output/
│   │   └── places.json
│   ├── Laos_bot_output/
│   │   └── places.json
│   ├── ...
│   └── MyMaps/
│       ├── Thailandia.csv
│       ├── Laos.csv
│       ├── ...
```

L'app carica automaticamente i CSV dalla cartella `MyMaps/` locale (nel quale `build_maps.py` scrive i dati).

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
- **Descrizione**: Testo breve del luogo
- **Lat, Lon**: Coordinate (solo informative)
- **Nazione_CSV**: Nazione di appartenenza

## 🔌 Integrazione con il Bot

I CSV vengono generati da `build_maps.py` ogni volta che il bot aggiorna i luoghi dai reel Instagram. L'app legge i CSV aggiornati automaticamente al ricaricamento della pagina.

## 🎨 Personalizzazione

- **Colori categorie**: Modifica il dizionario `category_colors` in `create_map()` (linee ~263-269)
- **Tile map**: Cambia il parametro `tiles=` nella creazione di `folium.Map()` (linea ~231)
  
### Tile map disponibili (senza API key)
- **OpenStreetMap** (predefinito) - Stile base OSM, completamente gratuito e affidabile
- **Stamen TonerLite** - Versione minimalista, stile mappa stradale
- **Stamen Terrain** - Topografia con rilievi
- **Esri WorldStreetMap** - Stile Esri stradale

**Nota**: CartoDB positron/voyager richiedono API key per deploy in produzione. OpenStreetMap è la scelta più sicura e gratuita.

## 🔐 Sicurezza e Privacy

- **Password**: Salva direttamente nel codice (`app.py`, linea ~18). Per cambiare: modifica `PASSWORD = "..."` e redeploy.
- **Dati**: I CSV contengono solo informazioni pubbliche (reel Instagram pubblici).
- **Geolocalizzazione**: Le coordinate GPS sono elaborate solo nel browser, non inviate a server. Rimangono in `localStorage` fino all'aggiornamento manuale.
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

- **Cache dati**: L'app cache i dati tramite `@st.cache_data`. Se aggiorni i CSV manualmente, clicca il bottone "🔄 Ricarica dati" per aggiornare, oppure usa `Ctrl+Shift+R` per pulire la cache del browser.
- **Geolocalizzazione**: Il bottone "📍 Aggiungi posizione" richiede accesso al GPS del browser. Funziona su:
  - `http://localhost:*` (sviluppo locale)
  - HTTPS (deploy in produzione)
  - Non funziona su HTTP non-localhost per motivi di sicurezza
- **Google Maps**: I link usano il parametro `query=` con il nome del luogo, per compatibilità con il metodo di Telegram.
- **Filtro Citazioni**: Il filtro slider nella sidebar filtra sia la mappa che l'elenco.
- **Esclusione termini**: I luoghi esclusi dal filtro "Escludi termine" rimangono ricaricabili dalla checkbox "Mostra esclusi".

## 📄 Licenza

Uso personale. Dati da reel Instagram pubblici.
