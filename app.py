import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from pathlib import Path
import json
from urllib.parse import quote
import os
import io

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
    """Crea URL Google Maps per aprire il luogo."""
    if pd.notna(lat) and pd.notna(lon):
        return f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"
    else:
        # Fallback con nome del luogo
        return f"https://www.google.com/maps/search/{quote(name)}"

def create_map(df_filtered):
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

        # Crea popup HTML
        popup_text = f"""
        <div style='font-family: Arial; width: 250px;'>
            <h4>{row['Nome']}</h4>
            <p><b>Categoria:</b> {row.get('Categoria', 'N/A')}</p>
            <p><b>Città:</b> {row.get('Città', 'N/A')}</p>
            <p><b>Citazioni:</b> {row.get('Citazioni', 'N/A')}</p>
            <p><b>Descrizione:</b> {row.get('Descrizione', 'N/A')[:150]}...</p>
            <a href="{get_google_maps_url(row['Lat'], row['Lon'], row['Nome'])}"
               target="_blank" style='color: #3498db; text-decoration: none;'>
               📍 Apri su Google Maps
            </a>
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

# Filtro Stato
stati = sorted(df_filtered['Stato'].unique())
selected_stati = st.sidebar.multiselect(
    "Seleziona Stato/i",
    options=stati,
    default=stati,
    help="da_fare, visitato, ecc."
)

if selected_stati:
    df_filtered = df_filtered[df_filtered['Stato'].isin(selected_stati)]

# Filtro Citazioni (range)
if len(df_filtered) > 0:
    citazioni_min, citazioni_max = st.sidebar.slider(
        "Filtro Citazioni",
        min_value=int(df_filtered['Citazioni'].min()),
        max_value=int(df_filtered['Citazioni'].max()),
        value=(int(df_filtered['Citazioni'].min()), int(df_filtered['Citazioni'].max())),
        help="Mostra solo luoghi menzionati N volte"
    )
    df_filtered = df_filtered[
        (df_filtered['Citazioni'] >= citazioni_min) &
        (df_filtered['Citazioni'] <= citazioni_max)
    ]

# Filtro Nome (ricerca testuale)
nome_ricerca = st.sidebar.text_input(
    "Cerca per Nome",
    placeholder="Es. Bangkok, Waterfall...",
    help="Ricerca case-insensitive nel nome e nella descrizione"
)
if nome_ricerca:
    mask_nome = df_filtered['Nome'].str.contains(nome_ricerca, case=False, na=False)
    mask_descrizione = df_filtered['Descrizione'].str.contains(nome_ricerca, case=False, na=False)
    df_filtered = df_filtered[mask_nome | mask_descrizione]

# ============= MAIN CONTENT =============
col1, col2 = st.columns([3, 1])

with col1:
    st.subheader(f"📌 Mappa - {len(df_filtered)} Luoghi")

    if len(df_filtered) > 0:
        map_obj = create_map(df_filtered)
        if map_obj:
            st_folium(map_obj, width=1200, height=600)
    else:
        st.warning("Nessun luogo corrisponde ai filtri selezionati.")

with col2:
    st.subheader("📊 Statistiche")
    st.metric("Totale Luoghi", len(df_filtered))

    if len(df_filtered) > 0:
        st.write("**Per Categoria:**")
        cat_counts = df_filtered['Categoria'].value_counts()
        for cat, count in cat_counts.items():
            st.write(f"• {cat}: {count}")

        st.write("\n**Per Stato:**")
        stato_counts = df_filtered['Stato'].value_counts()
        for stato, count in stato_counts.items():
            st.write(f"• {stato}: {count}")

# ============= TABELLA DETTAGLIATA =============
st.divider()
st.subheader("📋 Elenco Dettagliato")

if len(df_filtered) > 0:
    # Mostra un'anteprima della tabella
    display_cols = ['Nome', 'Categoria', 'Città', 'Stato', 'Citazioni', 'Nazione_CSV']
    df_display = df_filtered[display_cols].copy()

    st.dataframe(
        df_display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Nome": st.column_config.TextColumn(width="medium"),
            "Categoria": st.column_config.TextColumn(width="small"),
            "Città": st.column_config.TextColumn(width="small"),
            "Stato": st.column_config.TextColumn(width="small"),
            "Citazioni": st.column_config.NumberColumn(width="small"),
            "Nazione_CSV": st.column_config.TextColumn(width="small"),
        }
    )

    # Opzione per visualizzare descrizioni complete
    if st.checkbox("Mostra descrizioni complete"):
        for idx, row in df_filtered.iterrows():
            with st.expander(f"📍 {row['Nome']} ({row['Categoria']})"):
                st.write(f"**Descrizione:** {row.get('Descrizione', 'N/A')}")
                st.write(f"**Posizione:** {row.get('Posizione', 'N/A')}")
                if pd.notna(row['Lat']) and pd.notna(row['Lon']):
                    st.write(f"**Coordinate:** {row['Lat']}, {row['Lon']}")
                    st.link_button(
                        "🔗 Apri su Google Maps",
                        get_google_maps_url(row['Lat'], row['Lon'], row['Nome'])
                    )
else:
    st.info("Nessun luogo disponibile per questa selezione.")

# ============= FOOTER =============
st.divider()
st.caption("🔄 Dati aggiornati automaticamente dai file CSV in MyMaps/. Ultimo aggiornamento: caricamento cache.")
