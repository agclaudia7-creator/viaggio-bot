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
def load_csv_data():
    """Carica i dati dai CSV di MyMaps (locale o GitHub Pages)."""
    all_data = []

    # Prova locale prima
    MYMAPS_DIR = Path("G:/Il mio Drive/Viaggi/Aspettativa/BOT_OUTPUT/MyMaps")
    if MYMAPS_DIR.exists():
        for csv_file in MYMAPS_DIR.glob("*.csv"):
            try:
                df = pd.read_csv(csv_file, encoding='utf-8')
                nation = csv_file.stem
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
    """Crea URL Google Maps per aprire il luogo (senza indicazioni)."""
    if pd.notna(lat) and pd.notna(lon):
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    else:
        # Fallback con nome del luogo
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

def create_map(df_filtered, user_lat=None, user_lon=None, show_distance=False):
    """Crea mappa Folium con i dati filtrati."""
    # Calcola il centro della mappa basato sui dati disponibili
    valid_coords = df_filtered.dropna(subset=['Lat', 'Lon'])

    if len(valid_coords) == 0:
        st.warning("Nessun luogo con coordinate per la visualizzazione sulla mappa.")
        return None

    center_lat = valid_coords['Lat'].mean()
    center_lon = valid_coords['Lon'].mean()

    # Crea mappa
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=5,
        tiles="OpenStreetMap"
    )

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

        folium.CircleMarker(
            location=[row['Lat'], row['Lon']],
            radius=8,
            popup=folium.Popup(popup_text, max_width=300),
            color=color,
            fill=True,
            fillColor=color,
            fillOpacity=0.7,
            weight=2,
            opacity=0.9
        ).add_to(m)

    return m

# Carica dati
places = load_places_data()
df = load_csv_data()

if df.empty:
    st.error("Nessun dato trovato. Verifica che i file CSV siano presenti in MyMaps/")
    st.stop()

# ============= SIDEBAR - FILTRI =============
st.sidebar.title("🔍 Filtri")

# Filtro Nazione
nazioni = sorted(df['Nazione_CSV'].unique())
selected_nazioni = st.sidebar.multiselect(
    "Seleziona Nazione/i",
    options=nazioni,
    default=nazioni[:1] if nazioni else [],
    help="Scegli una o più nazioni da visualizzare"
)

if not selected_nazioni:
    st.warning("Seleziona almeno una nazione dal filtro nella sidebar.")
    st.stop()

df_filtered = df[df['Nazione_CSV'].isin(selected_nazioni)]

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

# ============= TUA POSIZIONE =============
st.sidebar.divider()
st.sidebar.title("📍 La tua Posizione")

user_lat = None
user_lon = None

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
        try:
            # Prova a estrarre coordinate da link Google Maps (anche short link)
            lat, lon = extract_coords_from_maps_url(location_input)

            if lat is not None and lon is not None:
                user_lat = lat
                user_lon = lon
                st.sidebar.success(f"✅ Coordinate estratte: {user_lat:.4f}, {user_lon:.4f}")
            else:
                # Se non trovo coordinate nel link, provo a geocodificare il nome
                from geopy.geocoders import Nominatim
                geocoder = Nominatim(user_agent="viaggio_bot")
                location = geocoder.geocode(location_input)
                if location:
                    user_lat = location.latitude
                    user_lon = location.longitude
                    st.sidebar.success(f"✅ {location_input}: {user_lat:.4f}, {user_lon:.4f}")
                else:
                    st.sidebar.warning(f"❌ Posizione '{location_input}' non trovata")
        except Exception as e:
            st.sidebar.error(f"Errore: {e}")

# Ordinamento per distanza
sort_by_distance = False
if user_lat is not None and user_lon is not None:
    sort_by_distance = st.sidebar.checkbox("📏 Ordina per distanza", value=False)
    if sort_by_distance:
        # Calcola distanza per ogni luogo con coordinate
        df_filtered['Distanza_km'] = df_filtered.apply(
            lambda row: haversine_distance(user_lat, user_lon, row['Lat'], row['Lon'])
            if pd.notna(row['Lat']) and pd.notna(row['Lon']) else float('inf'),
            axis=1
        )
        # Ordina per distanza
        df_filtered = df_filtered.sort_values('Distanza_km')

# ============= MAIN CONTENT =============
col1, col2 = st.columns([3, 1])

with col1:
    st.subheader(f"📌 Mappa - {len(df_filtered)} Luoghi")

    if len(df_filtered) > 0:
        map_obj = create_map(df_filtered, user_lat, user_lon, sort_by_distance)
        if map_obj:
            st_folium(map_obj, width=1200, height=600)
    else:
        st.warning("Nessun luogo corrisponde ai filtri selezionati.")

with col2:
    # Filtro Citazioni
    if len(df_filtered) > 0:
        citazioni_min, citazioni_max = st.slider(
            "Citazioni",
            min_value=int(df_filtered['Citazioni'].min()),
            max_value=int(df_filtered['Citazioni'].max()),
            value=(int(df_filtered['Citazioni'].min()), int(df_filtered['Citazioni'].max()))
        )
        df_filtered = df_filtered[
            (df_filtered['Citazioni'] >= citazioni_min) &
            (df_filtered['Citazioni'] <= citazioni_max)
        ]

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

    st.subheader(f"📋 Elenco ({len(df_filtered)})")

    # Container scrollabile con altezza fissa (come la mappa)
    with st.container(height=450):
        if len(df_filtered) > 0:
            for idx, row in df_filtered.iterrows():
                with st.expander(f"📍 {row['Nome']} ({row['Categoria']})"):
                    # Rendi link cliccabili nella descrizione
                    descrizione = make_links_clickable(row.get('Descrizione', 'N/A'))
                    st.markdown(f"**Descrizione:**\n{descrizione}", unsafe_allow_html=True)
                    if pd.notna(row['Lat']) and pd.notna(row['Lon']):
                        st.link_button(
                            "📍 Vedi su Google Maps",
                            get_google_maps_url(row['Lat'], row['Lon'], row['Nome'])
                        )
        else:
            st.info("Nessun luogo disponibile")


# ============= FOOTER =============
st.divider()
st.caption("🔄 Dati aggiornati automaticamente dai file CSV in MyMaps/. Ultimo aggiornamento: caricamento cache.")
