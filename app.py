import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from pathlib import Path
import json
from urllib.parse import quote
import os
import io
from math import radians, cos, sin, asin, sqrt
import re
import requests
from streamlit_js_eval import get_geolocation

from build_culture import CULTURE_TOPICS

# Configurazione pagina
st.set_page_config(page_title="Mappa Viaggi", layout="wide", initial_sidebar_state="expanded")

# Password per l'app
PASSWORD = "viaggi2026"

# Controllo autenticazione
def check_password():
    """Controlla se l'utente ha inserito la password corretta."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.markdown("""
        <div style='text-align: center; padding: 50px;'>
            <h1>🗺️ Mappa Interattiva - Viaggio Sud-Est Asiatico</h1>
            <p style='font-size: 18px; color: #666;'>Accedi per visualizzare la mappa</p>
        </div>
        """, unsafe_allow_html=True)

        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            password = st.text_input("🔐 Password", type="password", placeholder="Inserisci password")
            if st.button("🔓 Accedi", use_container_width=True):
                if password == PASSWORD:
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("❌ Password errata. Riprova!")

        st.stop()

# Verifica password
check_password()

st.title("🗺️ Mappa Interattiva - Viaggio Sud-Est Asiatico")

# URL GitHub Pages per i CSV (branch gh-pages)
GITHUB_PAGES_URL = "https://raw.githubusercontent.com/agclaudia7-creator/viaggio-bot/gh-pages"
CSV_FILES = {
    "Thailandia": f"{GITHUB_PAGES_URL}/Thailandia.csv",
    "Laos": f"{GITHUB_PAGES_URL}/Laos.csv",
    "Cambogia": f"{GITHUB_PAGES_URL}/Cambogia.csv",
    "Indonesia": f"{GITHUB_PAGES_URL}/Indonesia.csv",
    "Malesia": f"{GITHUB_PAGES_URL}/Malesia.csv",
    "Singapore": f"{GITHUB_PAGES_URL}/Singapore.csv",
    "Luang Prabang": f"{GITHUB_PAGES_URL}/Luang%20Prabang.csv",
}

@st.cache_data
def load_places_data():
    """Carica tutti i places.json da tutte le nazioni."""
    all_places = []

    # Prova locale prima
    BOT_OUTPUT_DIR = Path("G:/Il mio Drive/Viaggi/Aspettativa/BOT_OUTPUT")
    if BOT_OUTPUT_DIR.exists():
        for folder in BOT_OUTPUT_DIR.glob("*_bot_output"):
            places_file = folder / "places.json"
            if places_file.exists():
                try:
                    with open(places_file, 'r', encoding='utf-8') as f:
                        places = json.load(f)
                        for place in places:
                            place['source_nation'] = folder.name.replace('_bot_output', '')
                        all_places.extend(places)
                except Exception as e:
                    st.warning(f"Errore nel caricamento di {places_file}: {e}")

    return all_places

@st.cache_data
def load_dishes_data():
    """Carica tutti i dishes.json (build_dishes.py --all) da tutte le nazioni: piatti/bevande tipici
    estratti dalle note dei luoghi di categoria "cibo", con i luoghi dove si trovano."""
    all_dishes = []
    BOT_OUTPUT_DIR = Path("G:/Il mio Drive/Viaggi/Aspettativa/BOT_OUTPUT")
    if BOT_OUTPUT_DIR.exists():
        for folder in BOT_OUTPUT_DIR.glob("*_bot_output"):
            dishes_file = folder / "dishes.json"
            if dishes_file.exists():
                try:
                    with open(dishes_file, 'r', encoding='utf-8') as f:
                        dishes = json.load(f)
                        for d in dishes:
                            d['_key'] = f"{folder.name}:{d['id']}"  # univoco anche tra bot diversi
                        all_dishes.extend(dishes)
                except Exception as e:
                    st.warning(f"Errore nel caricamento di {dishes_file}: {e}")
    return all_dishes

@st.cache_data
def load_culture_data():
    """Carica tutti i culture_topics.json (build_culture.py --all) da tutte le nazioni: consigli e
    criticità culturali raggruppati per argomento invece che per città/luogo."""
    all_items = []
    BOT_OUTPUT_DIR = Path("G:/Il mio Drive/Viaggi/Aspettativa/BOT_OUTPUT")
    if BOT_OUTPUT_DIR.exists():
        for folder in BOT_OUTPUT_DIR.glob("*_bot_output"):
            culture_file = folder / "culture_topics.json"
            if culture_file.exists():
                try:
                    with open(culture_file, 'r', encoding='utf-8') as f:
                        all_items.extend(json.load(f))
                except Exception as e:
                    st.warning(f"Errore nel caricamento di {culture_file}: {e}")
    return all_items

@st.cache_data
def load_csv_data():
    """Carica i dati dai CSV di MyMaps (locale o GitHub Pages)."""
    all_data = []

    # Prova locale prima. build_maps.mymaps_dir() scrive in Aspettativa/MyMaps/<Nazione>/<Nazione>_<livello>.csv
    # (una sottocartella per nazione, più file divisi per fascia di citazioni): NON dentro BOT_OUTPUT/MyMaps,
    # che è un percorso vecchio/abbandonato rimasto con un CSV piatto non più aggiornato dal 24/09.
    MYMAPS_DIR = Path("G:/Il mio Drive/Viaggi/Aspettativa/MyMaps")
    if MYMAPS_DIR.exists():
        for csv_file in MYMAPS_DIR.glob("*/*.csv"):
            try:
                df = pd.read_csv(csv_file, encoding='utf-8')
                nation = csv_file.parent.name   # cartella = nazione
                df['Nazione_CSV'] = nation
                all_data.append(df)
            except Exception as e:
                st.warning(f"Errore nel caricamento di {csv_file}: {e}")

    # Se non ci sono file locali, carica da GitHub Pages
    if not all_data:
        st.info("📶 Caricamento dati da GitHub Pages...")
        for nation, url in CSV_FILES.items():
            try:
                df = pd.read_csv(url)
                df['Nazione_CSV'] = nation
                all_data.append(df)
            except Exception as e:
                st.warning(f"Errore nel caricamento da GitHub Pages ({nation}): {e}")

    if all_data:
        return pd.concat(all_data, ignore_index=True)
    return pd.DataFrame()

def get_google_maps_url(lat, lon, name):
    """Crea URL Google Maps per aprire il luogo (metodo query come Telegram)."""
    # Usa il metodo query (nome del luogo) che funziona anche offline
    return f"https://www.google.com/maps/search/{quote(name)}"

def make_links_clickable(text):
    """Rende i link nella descrizione cliccabili in HTML."""
    if not isinstance(text, str):
        return text
    # Regex per trovare URL http/https
    url_pattern = r'(https?://[^\s<>"{}|\\^`\[\]]*)'
    text = re.sub(url_pattern, r'<a href="\1" target="_blank" style="color: #3498db;">🔗 Link</a>', text)
    return text

def extract_coords_from_maps_url(url):
    """Estrae coordinate da URL Google Maps (risolve anche short link)."""
    try:
        # Se è uno short link, risolvilo
        if "maps.app.goo.gl" in url or "goo.gl" in url:
            response = requests.head(url, allow_redirects=True, timeout=5)
            url = response.url

        # Regex per estrarre coordinate dal link risotto
        coord_pattern = r'/@([-+]?\d+\.\d+),([-+]?\d+\.\d+)'
        match = re.search(coord_pattern, url)
        if match:
            lat = float(match.group(1))
            lon = float(match.group(2))
            return lat, lon
    except Exception as e:
        pass
    return None, None

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calcola la distanza in km tra due coordinate usando la formula di Haversine."""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    km = 6371 * c
    return km

def create_map(df_filtered, user_lat=None, user_lon=None, show_distance=False, zoom_luogo_nome=None,
               highlight_nomi=None):
    """Crea mappa Folium con i dati filtrati. highlight_nomi: set di nomi da evidenziare come lo zoom
    (usato dalla tab Cibo per mostrare dove si mangia un piatto), senza spostare centro/bounds."""
    # Calcola il centro della mappa basato sui dati disponibili
    valid_coords = df_filtered.dropna(subset=['Lat', 'Lon'])

    if len(valid_coords) == 0:
        st.warning("Nessun luogo con coordinate per la visualizzazione sulla mappa.")
        return None

    # Se è stato selezionato un luogo specifico per lo zoom
    if zoom_luogo_nome:
        luogo_zoom = df_filtered[df_filtered['Nome'] == zoom_luogo_nome]
        if len(luogo_zoom) > 0:
            row = luogo_zoom.iloc[0]
            center_lat = row['Lat']
            center_lon = row['Lon']
            bounds = [
                [center_lat - 0.02, center_lon - 0.02],
                [center_lat + 0.02, center_lon + 0.02]
            ]
        else:
            # Fallback se il luogo non esiste
            center_lat = valid_coords['Lat'].mean()
            center_lon = valid_coords['Lon'].mean()
            bounds = None
    # Determina centro e bounds della mappa
    elif user_lat is not None and user_lon is not None:
        # Centra sulla posizione dell'utente
        center_lat = user_lat
        center_lon = user_lon

        # Calcola il bounding box dei luoghi + posizione utente
        all_lats = list(valid_coords['Lat']) + [user_lat]
        all_lons = list(valid_coords['Lon']) + [user_lon]

        min_lat, max_lat = min(all_lats), max(all_lats)
        min_lon, max_lon = min(all_lons), max(all_lons)

        # Aggiungi padding al bounding box (5%)
        lat_padding = (max_lat - min_lat) * 0.05
        lon_padding = (max_lon - min_lon) * 0.05

        bounds = [
            [min_lat - lat_padding, min_lon - lon_padding],
            [max_lat + lat_padding, max_lon + lon_padding]
        ]
    else:
        # Centra sui luoghi
        center_lat = valid_coords['Lat'].mean()
        center_lon = valid_coords['Lon'].mean()

        all_lats = list(valid_coords['Lat'])
        all_lons = list(valid_coords['Lon'])

        min_lat, max_lat = min(all_lats), max(all_lats)
        min_lon, max_lon = min(all_lons), max(all_lons)

        # Aggiungi padding al bounding box (5%)
        lat_padding = (max_lat - min_lat) * 0.05
        lon_padding = (max_lon - min_lon) * 0.05

        bounds = [
            [min_lat - lat_padding, min_lon - lon_padding],
            [max_lat + lat_padding, max_lon + lon_padding]
        ]

    # Crea mappa con OpenStreetMap (niente API key richiesta)
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=8,
        tiles="OpenStreetMap"
    )

    # Applica fit_bounds per zoommare automaticamente sui contenuti
    if bounds:
        m.fit_bounds(bounds)

    # Aggiungi bottone per geolocalizzazione automatica
    folium.plugins.LocateControl().add_to(m)

    # Aggiungi marker per la posizione dell'utente (se fornita)
    if user_lat is not None and user_lon is not None:
        folium.Marker(
            location=[user_lat, user_lon],
            popup="📍 La tua posizione",
            icon=folium.Icon(color='blue', icon='user', prefix='fa'),
            tooltip="La tua posizione attuale"
        ).add_to(m)

    # Colori per categoria
    category_colors = {
        'natura': '#2ecc71',
        'cultura': '#e74c3c',
        'shopping': '#f39c12',
        'cibo': '#9b59b6',
        'alloggio': '#3498db',
        'altro': '#95a5a6'
    }

    # Aggiungi marker
    for idx, row in valid_coords.iterrows():
        category = row.get('Categoria', 'altro').lower()
        color = category_colors.get(category, '#95a5a6')

        # Calcola distanza se richiesto
        distance_text = ""
        if show_distance and user_lat is not None and user_lon is not None:
            dist = haversine_distance(user_lat, user_lon, row['Lat'], row['Lon'])
            distance_text = f"<p><b>Distanza:</b> {dist:.1f} km</p>"

        # Rendi link cliccabili nella descrizione
        descrizione = make_links_clickable(row.get('Descrizione', 'N/A'))

        # Crea popup HTML
        popup_text = f"""
        <div style='font-family: Arial; width: 280px;'>
            <h4>{row['Nome']}</h4>
            <p><b>Categoria:</b> {row.get('Categoria', 'N/A')}</p>
            <p><b>Città:</b> {row.get('Città', 'N/A')}</p>
            <p><b>Citazioni:</b> {row.get('Citazioni', 'N/A')}</p>
            {distance_text}
            <p><b>Descrizione:</b><br>{descrizione[:200]}...</p>
            <p>
                <a href="{get_google_maps_url(row['Lat'], row['Lon'], row['Nome'])}"
                   target="_blank" style='color: #3498db; text-decoration: none;'>
                   📍 Vedi su Google Maps
                </a>
            </p>
        </div>
        """

        is_zoomed = (zoom_luogo_nome is not None and row['Nome'] == zoom_luogo_nome) or \
                   (highlight_nomi is not None and row['Nome'] in highlight_nomi)

        folium.CircleMarker(
            location=[row['Lat'], row['Lon']],
            radius=16 if is_zoomed else 8,
            popup=folium.Popup(popup_text, max_width=300),
            color='#000000' if is_zoomed else color,
            fill=True,
            fillColor='#ffd700' if is_zoomed else color,
            fillOpacity=1.0 if is_zoomed else 0.7,
            weight=4 if is_zoomed else 2,
            opacity=1.0 if is_zoomed else 0.9
        ).add_to(m)

        # Simbolo diverso (stella) sopra il luogo zoommato, per riconoscerlo subito tra gli altri pallini
        if is_zoomed:
            folium.Marker(
                location=[row['Lat'], row['Lon']],
                icon=folium.Icon(color='red', icon='star', prefix='fa'),
                tooltip=row['Nome']
            ).add_to(m)

    return m

# Carica dati
places = load_places_data()
df = load_csv_data()
dishes_all = load_dishes_data()
culture_all = load_culture_data()

if df.empty:
    st.error("Nessun dato trovato. Verifica che i file CSV siano presenti in MyMaps/")
    st.stop()

# ============= SIDEBAR - TUA POSIZIONE =============
st.sidebar.title("📍 La tua Posizione")

# Inizializza session state per geolocalizzazione
if "user_lat_session" not in st.session_state:
    st.session_state.user_lat_session = None
    st.session_state.user_lon_session = None

user_lat = st.session_state.user_lat_session
user_lon = st.session_state.user_lon_session

# Bottone per geolocalizzazione automatica (il browser chiede il permesso la prima volta)
if st.sidebar.button("📍 Rileva automaticamente", use_container_width=True):
    st.session_state.geoloc_requested = True

if st.session_state.get("geoloc_requested"):
    geoloc_result = get_geolocation()
    coords = geoloc_result.get("coords") if isinstance(geoloc_result, dict) else None
    if coords:
        user_lat = coords["latitude"]
        user_lon = coords["longitude"]
        st.session_state.user_lat_session = user_lat
        st.session_state.user_lon_session = user_lon
        st.session_state.geoloc_requested = False
        st.sidebar.success(f"✅ Posizione: {user_lat:.4f}, {user_lon:.4f}")
    else:
        st.sidebar.info("⏳ In attesa del permesso di geolocalizzazione del browser...")

# Opzione 1: Input manuale
input_type = st.sidebar.radio("Come inserire la posizione?", ["Nessuna", "Manuale (Lat/Lon)", "Nome/Link Maps"])

if input_type == "Manuale (Lat/Lon)":
    col_lat, col_lon = st.sidebar.columns(2)
    with col_lat:
        user_lat = st.number_input("Latitudine", value=None, format="%.6f", help="Es. 13.7563")
    with col_lon:
        user_lon = st.number_input("Longitudine", value=None, format="%.6f", help="Es. 100.5018")

elif input_type == "Nome/Link Maps":
    location_input = st.sidebar.text_input(
        "Nome luogo o link Google Maps",
        placeholder="Es. Bangkok oppure maps.app.goo.gl/...",
        help="Scrivi il nome di una città o incolla un link di Google Maps (anche short link)"
    )
    if location_input:
        # Risolve le coordinate solo quando il testo cambia: senza questa cache ogni interazione
        # sulla pagina (filtri, zoom, ...) rifaceva la richiesta di rete (lenta, rischio rate limit).
        if st.session_state.get("_last_location_input") != location_input:
            resolved = None
            try:
                # Prova a estrarre coordinate da link Google Maps (anche short link)
                lat, lon = extract_coords_from_maps_url(location_input)
                if lat is None:
                    # Se non trovo coordinate nel link, provo a geocodificare il nome
                    from geopy.geocoders import Nominatim
                    geocoder = Nominatim(user_agent="viaggio_bot")
                    location = geocoder.geocode(location_input)
                    if location:
                        lat, lon = location.latitude, location.longitude
                resolved = (lat, lon) if lat is not None and lon is not None else None
            except Exception as e:
                st.sidebar.error(f"Errore: {e}")
            st.session_state._last_location_input = location_input
            st.session_state._last_location_coords = resolved

        resolved = st.session_state.get("_last_location_coords")
        if resolved:
            user_lat, user_lon = resolved
            st.sidebar.success(f"✅ Posizione: {user_lat:.4f}, {user_lon:.4f}")
        else:
            st.sidebar.warning(f"❌ Posizione '{location_input}' non trovata")

# ============= SIDEBAR - FILTRI =============
st.sidebar.divider()
st.sidebar.title("🔍 Filtri")

# Filtro Nazione
nazioni = sorted(df['Nazione_CSV'].unique())
# Di default seleziona Malesia e Singapore
default_nazioni = [n for n in nazioni if n in ["Malesia", "Singapore"]]
if not default_nazioni:
    default_nazioni = nazioni[:1] if nazioni else []
selected_nazioni = st.sidebar.multiselect(
    "Seleziona Nazione/i",
    options=nazioni,
    default=default_nazioni,
    help="Scegli una o più nazioni da visualizzare"
)

if not selected_nazioni:
    st.warning("Seleziona almeno una nazione dal filtro nella sidebar.")
    st.stop()

df_filtered = df[df['Nazione_CSV'].isin(selected_nazioni)]

# Inizializza variabili per lo zoom su luogo specifico
if "zoom_luogo" not in st.session_state:
    st.session_state.zoom_luogo = None

# Filtro Categoria
categorie = sorted(df_filtered['Categoria'].unique())
selected_categorie = st.sidebar.multiselect(
    "Seleziona Categoria/e",
    options=categorie,
    default=categorie,
    help="Filtra per tipo di luogo"
)

if selected_categorie:
    df_filtered = df_filtered[df_filtered['Categoria'].isin(selected_categorie)]

# Filtro Citazioni (PRIMA della mappa, non dopo!)
if len(df_filtered) > 0:
    citazioni_min, citazioni_max = st.sidebar.slider(
        "Citazioni",
        min_value=int(df_filtered['Citazioni'].min()),
        max_value=int(df_filtered['Citazioni'].max()),
        value=(int(df_filtered['Citazioni'].min()), int(df_filtered['Citazioni'].max()))
    )
    df_filtered = df_filtered[
        (df_filtered['Citazioni'] >= citazioni_min) &
        (df_filtered['Citazioni'] <= citazioni_max)
    ]

# Filtro Esclusione
esclusione = st.sidebar.text_input(
    "🚫 Escludi termine",
    value="Borneo",
    placeholder="Es. Borneo",
    help="Esclude luoghi che contengono questo termine nel nome, posizione o descrizione"
)

# Inizializza session_state per i luoghi reinclusti
if "reinclusti" not in st.session_state:
    st.session_state.reinclusti = set()

# Salva i luoghi esclusi prima di filtrarli
df_esclusi = pd.DataFrame()
if esclusione:
    mask_nome = df_filtered['Nome'].str.contains(esclusione, case=False, na=False)
    mask_posizione = df_filtered['Posizione'].str.contains(esclusione, case=False, na=False)
    mask_descrizione = df_filtered['Descrizione'].str.contains(esclusione, case=False, na=False)

    # Esclude se il termine compare in nome, posizione O descrizione
    mask_esclusione = mask_nome | mask_posizione | mask_descrizione
    df_esclusi = df_filtered[mask_esclusione].copy()

    # Rimuovi dai filtrati quelli non reinclusti
    df_filtered = df_filtered[~mask_esclusione | df_filtered['Nome'].isin(st.session_state.reinclusti)]

# Mostra elenco esclusi
show_esclusi = st.sidebar.checkbox("📋 Mostra esclusi", value=False)
if show_esclusi and len(df_esclusi) > 0:
    with st.sidebar.expander(f"🚫 Esclusi ({len(df_esclusi)})"):
        checkbox_changed = False
        for idx, row in df_esclusi.iterrows():
            nome = row['Nome']
            is_reincluded = nome in st.session_state.reinclusti

            col1, col2, col3 = st.columns([0.6, 0.2, 0.2])

            with col1:
                if st.checkbox(
                    f"✓ {nome}",
                    value=is_reincluded,
                    key=f"reincl_{nome}_{idx}"
                ):
                    if nome not in st.session_state.reinclusti:
                        st.session_state.reinclusti.add(nome)
                        checkbox_changed = True
                else:
                    if nome in st.session_state.reinclusti:
                        st.session_state.reinclusti.discard(nome)
                        checkbox_changed = True

            with col2:
                if pd.notna(row.get('Lat')) and pd.notna(row.get('Lon')):
                    if st.button("🔍", key=f"zoom_{nome}_{idx}", help="Zoom su questo luogo"):
                        st.session_state.zoom_luogo = nome
                        st.rerun()

            with col3:
                if pd.notna(row.get('Lat')) and pd.notna(row.get('Lon')):
                    st.link_button(
                        "📍",
                        get_google_maps_url(row['Lat'], row['Lon'], nome),
                        help="Apri su Google Maps"
                    )

        if checkbox_changed:
            st.rerun()

# ============= MAIN CONTENT =============
tab_mappa, tab_cibo, tab_cultura = st.tabs(["🗺️ Mappa", "🍜 Cibo", "🏛️ Cultura"])

with tab_mappa:
    # Bottone per ricaricare i dati (rifresca la cache di Streamlit)
    if st.button("🔄 Ricarica dati", help="Aggiorna i dati dai CSV. Usa questo dopo /aggiorna nel bot."):
        st.cache_data.clear()
        st.rerun()

    col1, col2 = st.columns([2, 1])

    with col1:
        col1a, col1b = st.columns([3, 1])
        with col1a:
            st.subheader(f"📌 Mappa - {len(df_filtered)} Luoghi")
        with col1b:
            if st.button("🎯 Centra mappa", use_container_width=True,
                          help="Centra la mappa su tutti i luoghi che rispettano i filtri attuali"):
                st.session_state.zoom_luogo = None
                st.session_state.highlight_nomi = None
                st.rerun()

        if len(df_filtered) > 0:
            show_distance = user_lat is not None and user_lon is not None
            map_obj = create_map(df_filtered, user_lat, user_lon, show_distance, st.session_state.zoom_luogo,
                                 st.session_state.get("highlight_nomi"))
            if map_obj:
                st_folium(map_obj, width=600, height=600)
        else:
            st.warning("Nessun luogo corrisponde ai filtri selezionati.")

    with col2:
        # Filtro Nome (ricerca testuale)
        nome_ricerca = st.text_input(
            "🔍 Cerca",
            placeholder="Nome, descrizione...",
            help="Ricerca nel nome e descrizione"
        )
        if nome_ricerca:
            mask_nome = df_filtered['Nome'].str.contains(nome_ricerca, case=False, na=False)
            mask_descrizione = df_filtered['Descrizione'].str.contains(nome_ricerca, case=False, na=False)
            df_filtered = df_filtered[mask_nome | mask_descrizione]

        # Luogo con lo zoom attivo (il pin evidenziato sulla mappa): cercato nel df completo, non in
        # df_filtered, altrimenti un filtro/ricerca applicato dopo lo zoom lo farebbe sparire come riferimento.
        zoom_coords = None
        if st.session_state.zoom_luogo:
            zrow = df[df['Nome'] == st.session_state.zoom_luogo]
            if len(zrow) > 0 and pd.notna(zrow.iloc[0]['Lat']) and pd.notna(zrow.iloc[0]['Lon']):
                zoom_coords = (zrow.iloc[0]['Lat'], zrow.iloc[0]['Lon'])

        # Modalità di ordinamento
        ordinamento_options = ["Citazioni ↓"]
        if user_lat is not None and user_lon is not None:
            ordinamento_options.append("Distanza (dalla mia posizione)")
        if zoom_coords is not None:
            ordinamento_options.append(f"Distanza (da {st.session_state.zoom_luogo})")

        ordinamento = st.radio(
            "📊 Ordinamento",
            options=ordinamento_options,
            horizontal=True
        )

        if ordinamento == "Distanza (dalla mia posizione)":
            rif_lat, rif_lon = user_lat, user_lon
        elif zoom_coords is not None and ordinamento == f"Distanza (da {st.session_state.zoom_luogo})":
            rif_lat, rif_lon = zoom_coords
        else:
            rif_lat, rif_lon = None, None

        if rif_lat is not None:
            df_filtered['Distanza_km'] = df_filtered.apply(
                lambda row: haversine_distance(rif_lat, rif_lon, row['Lat'], row['Lon'])
                if pd.notna(row['Lat']) and pd.notna(row['Lon']) else float('inf'),
                axis=1
            )
            df_filtered = df_filtered.sort_values('Distanza_km')
        else:
            # Default: ordina per Citazioni dal più alto al più basso
            df_filtered = df_filtered.sort_values('Citazioni', ascending=False)

        st.subheader(f"📋 Elenco ({len(df_filtered)})")

        # Container scrollabile con altezza fissa (come la mappa)
        with st.container(height=450):
            if len(df_filtered) > 0:
                for idx, row in df_filtered.iterrows():
                    with st.expander(f"📍 {row['Nome']} ({row['Categoria']})"):
                        # Bottoni Zoom e Google Maps solo se ci sono coordinate
                        if pd.notna(row['Lat']) and pd.notna(row['Lon']):
                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button(f"🔍 Zoom", key=f"zoom_main_{row['Nome']}_{idx}", use_container_width=True):
                                    st.session_state.zoom_luogo = row['Nome']
                                    st.rerun()
                            with col2:
                                st.link_button(
                                    "📍 Google Maps",
                                    get_google_maps_url(row['Lat'], row['Lon'], row['Nome']),
                                    use_container_width=True
                                )
                            st.divider()

                        # Descrizione
                        descrizione = make_links_clickable(row.get('Descrizione', 'N/A'))
                        st.markdown(f"**Descrizione:**\n{descrizione}", unsafe_allow_html=True)
            else:
                st.info("Nessun luogo disponibile")

with tab_cibo:
    # Piatti/bevande tipici (build_dishes.py): filtrati sulle stesse nazioni scelte nella sidebar.
    # "countries" è vuoto solo in casi anomali: in quel caso il piatto resta visibile comunque.
    dishes_nazione = [d for d in dishes_all if not d.get("countries") or
                      any(c in selected_nazioni for c in d["countries"])]
    dishes_nazione.sort(key=lambda d: -d["mention_count"])

    if not dishes_nazione:
        st.info("Nessun piatto estratto per le nazioni selezionate. Lancia `build_dishes.py --all`.")
    else:
        col_a, col_b = st.columns([1, 1])
        with col_a:
            st.subheader(f"🍜 Piatti e bevande ({len(dishes_nazione)})")
            cerca_piatto = st.text_input("🔍 Cerca piatto", key="cerca_piatto", placeholder="Es. Pad Thai")
            lista = [d for d in dishes_nazione
                    if not cerca_piatto or cerca_piatto.lower() in d["name"].lower()]
            with st.container(height=450):
                for d in lista:
                    label = f"{d['name']} (×{d['mention_count']})" if d["mention_count"] > 1 else d["name"]
                    if st.button(label, key=f"dish_{d['_key']}", use_container_width=True):
                        st.session_state.selected_dish_key = d["_key"]

        with col_b:
            sel = next((d for d in dishes_nazione if d["_key"] == st.session_state.get("selected_dish_key")), None)
            if not sel:
                st.info("⬅️ Scegli un piatto dalla lista per vedere dove mangiarlo.")
            else:
                st.subheader(f"🍽️ {sel['name']}")
                if sel["aliases"]:
                    st.caption("Alias: " + ", ".join(sel["aliases"]))
                if sel["description"]:
                    st.markdown(sel["description"])
                st.markdown(f"**Dove mangiarlo ({len(sel['places'])}):**")
                for p in sel["places"]:
                    citta = f" ({p['city']})" if p["city"] else ""
                    st.markdown(f"- {p['name']}{citta}")
                if st.button("📍 Evidenzia questi posti sulla mappa", key=f"highlight_{sel['_key']}"):
                    st.session_state.highlight_nomi = {p["name"] for p in sel["places"]}
                    st.session_state.zoom_luogo = None
                    st.success("Fatto: apri la tab 🗺️ Mappa, i posti sono evidenziati in oro.")
                st.caption("Fonte: " + ", ".join(sel["reels"][:5]) +
                          (f" (+{len(sel['reels'])-5})" if len(sel["reels"]) > 5 else ""))

with tab_cultura:
    # Consigli/criticità culturali raggruppati per argomento (build_culture.py), non per città:
    # niente logistica di un singolo luogo (quella resta nella scheda nella tab Mappa).
    cultura_nazione = [it for it in culture_all if not it.get("countries") or
                       any(c in selected_nazioni for c in it["countries"])]

    # Dividi in due sezioni: templi/luoghi culturali e consigli generali
    sub_tab1, sub_tab2 = st.tabs(["🏛️ Templi e Luoghi Culturali", "💡 Consigli Culturali"])

    with sub_tab1:
        st.subheader("Templi, Monasteri e Luoghi Culturali")
        # Mostra i luoghi di categoria "cultura" da places.json con descrizioni approfondite
        places_all = load_places_data()
        cultural_places = [p for p in places_all if p.get("category") == "cultura"
                          and p.get("country") in selected_nazioni]

        if not cultural_places:
            st.info("Nessun luogo culturale trovato per le nazioni selezionate.")
        else:
            # Ordina per numero di citazioni
            cultural_places = sorted(cultural_places, key=lambda x: -x.get("mention_count", 1))

            for place in cultural_places:
                with st.expander(f"🏛️ **{place['name']}** ({place['city']}) - ×{place.get('mention_count', 1)} citazioni"):
                    # Riassunto
                    st.write(f"**Categoria:** {place.get('category', 'N/A')}")

                    # Note (descrizione principale)
                    if place.get("notes"):
                        st.write("**Descrizione:**")
                        for note in place["notes"]:
                            st.write(f"- {note}")

                    # Consigli specifici
                    if place.get("tips"):
                        st.write("**Consigli pratici:**")
                        for tip in place["tips"]:
                            st.write(f"💡 {tip}")

                    # Criticità
                    if place.get("cons"):
                        st.write("**Cose da sapere:**")
                        for con in place["cons"]:
                            st.write(f"⚠️ {con}")

                    # Voti
                    if place.get("creator_ratings"):
                        st.write(f"**Voti creator:** {', '.join(place['creator_ratings'])}")

                    # Costi
                    if place.get("costs"):
                        st.write(f"**Costi:** {', '.join(place['costs'])}")

                    # Link ai reel
                    if place.get("mentions"):
                        st.write(f"**Reel che lo citano:**")
                        for m in place["mentions"][:5]:  # Mostra max 5 reel
                            st.write(f"🎬 [{m['reel']}]({m['reel']})")
                        if len(place["mentions"]) > 5:
                            st.write(f"... e {len(place['mentions']) - 5} altri reel")

    with sub_tab2:
        st.subheader("Consigli e Tradizioni Culturali")
        if not cultura_nazione:
            st.info("Nessuna informazione culturale per le nazioni selezionate. Lancia `build_culture.py --all`.")
        else:
            cerca_cultura = st.text_input("🔍 Cerca nella cultura", key="cerca_cultura", placeholder="Es. truffe, templi...")
            by_topic = {}
            for it in cultura_nazione:
                if cerca_cultura and cerca_cultura.lower() not in it["text"].lower():
                    continue
                by_topic.setdefault(it["topic"], []).append(it)

            for topic in CULTURE_TOPICS:
                items = by_topic.get(topic)
                if not items:
                    continue
                with st.expander(f"**{topic.capitalize()}** ({len(items)})", expanded=False):
                    for it in sorted(items, key=lambda x: -x["mention_count"]):
                        star = f" ×{it['mention_count']}" if it["mention_count"] > 1 else ""
                        # Mostra il testo come trafiletto espandibile se molto lungo
                        if len(it['text']) > 100:
                            st.markdown(f"- {it['text']}{star}")
                        else:
                            st.markdown(f"- {it['text']}{star}")

# ============= FOOTER =============
st.divider()
st.caption("🔄 Dati aggiornati automaticamente dai file CSV in MyMaps/. Ultimo aggiornamento: caricamento cache.")
