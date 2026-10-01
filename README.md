# Dati pubblici per la Mappa Viaggio

Fallback letto da `app.py` quando l'app gira su Streamlit Cloud (senza accesso al Google Drive
locale). Stessa origine delle informazioni già nel CSV: solo dati pubblici (reel Instagram
pubblici), nessun contenuto personale.

- `*.csv` — luoghi per nazione (vecchio formato, un file piatto; va aggiornato a mano)
- `BOT_OUTPUT/<Nazione>_bot_output/{places,dishes,culture_topics}.json` — stessi dati del CSV più
  piatti tipici e consigli culturali per argomento (tab Cibo/Cultura dell'app)
- `FONTI/<Nazione>/{<Nazione>.txt,<Nazione>_culture.txt}` — solo i due file generati dal bot (tab
  Chat dell'app); eventuali altri file in FONTI/ (note personali, guide da blog) NON vengono
  pubblicati qui

Pubblicato manualmente (nessuna automazione): va rifatto dopo aggiornamenti importanti.
