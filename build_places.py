"""
build_places.py
Legge un file *_bot.txt (Link + Caption), estrae i luoghi con un LLM,
li raggruppa (stesso luogo in reel diversi = un solo record) e salva:

  places.json   -> un record per luogo, con TUTTI i reel che lo citano
  places.md     -> vista leggibile/compatta
                   (+ copia .txt in ../<Nazione>.txt per Gemini Notebook, che non legge i .md)
  Il txt del bot sta dentro la sua cartella: Aspettativa/<Nazione>_bot_output/<Nazione>_bot.txt
  tips.json     -> consigli generali non legati a un luogo (EZ-Link, ATM, scam...)

Uso:
  pip install google-genai
  set GEMINI_API_KEY=...                  (Windows)  /  export ... (Mac/Linux)
  python build_places.py "Singapore&Malesia_bot.txt" [--out-dir cartella]
  python build_places.py --all            (tutte le nazioni di bots_config.json)

Chiave gratuita: https://aistudio.google.com/apikey
Modello: di default gemini-3.5-flash-lite (quota gratuita giornaliera più ampia).
Per cambiarlo: set GEMINI_MODEL=gemini-3.8-flash

Ri-esecuzioni:
  - i reel già analizzati sono in extraction_cache.json (nessuna nuova chiamata LLM)
  - status / rating / user_notes già presenti in places.json vengono conservati
  - per forzare unioni manuali scrivi aliases.json, es.:
      {"Satay Street": "Lau Pa Sat", "Supertree Grove": "Gardens by the Bay"}
"""

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

try:  # usa i certificati di Windows (necessario dietro firewall aziendali): pip install truststore
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

try:  # senza, i print con emoji crashano (UnicodeEncodeError) sulla codepage cp1252 di default di Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
PAUSE_SECONDS = 5   # pausa tra le chiamate per restare nei limiti al minuto del tier gratuito
FUZZY_MERGE = 0.86      # sopra questa somiglianza i nomi vengono fusi
FUZZY_SUGGEST = 0.70    # tra questa e la soglia sopra: solo suggerimento

# Parole generiche ignorate nel confronto tra nomi ("Tek Sen Restaurant" = "Tek Sen").
# Si toglie anche il nome di città/nazione ("Universal Studios Singapore" = "Universal Studios").
# Il nome mostrato resta quello originale.
GENERIC_WORDS = r"\b(restaurant|ristorante|park|parco|food (centre|center)|hawker (centre|center))\b"

# Stessa città con nomi diversi (chiavi normalizzate -> nome da usare)
CITY_ALIASES = {
    "malacca": "Melaka",
    "george town": "Penang",
    "georgetown": "Penang",
}

# Da incrementare quando cambia il prompt: i reel in cache con versione diversa vengono rianalizzati
PROMPT_VERSION = 2

SYSTEM_PROMPT = """Estrai informazioni di viaggio dalla caption di un reel Instagram.
Rispondi SOLO con JSON valido, senza testo extra né backtick, con questo schema:
{
  "places": [
    {"name": "...", "city": "...", "country": "...", "category": "...", "cost": "...",
     "rating": "...", "days": "...", "note": "...", "cons": "...", "tips": ["..."]}
  ],
  "tips": [{"country": "...", "tip": "..."}]
}
Regole:
- "places": luoghi concreti e visitabili (ristoranti, hawker centre, quartieri, templi, parchi, attrazioni, negozi, mercati, esperienze con un posto preciso). Un elemento per luogo distinto.
- "name": nome ufficiale/più noto, in alfabeto latino, senza emoji né "@". NON tradurlo in italiano (es. "Petronas Twin Towers", non "Torri Petronas"). Lo stesso luogo deve avere sempre lo stesso nome (es. "Maxwell Food Centre", non "Maxwell").
- "city" e "country": in italiano (es. "Singapore", "Kuala Lumpur", "Malesia"). Per le isole usa il nome dell'isola (es. "Penang", non "George Town"). Se non certo, deducili dal contesto, altrimenti "".
- "category": una tra cibo, natura, cultura, nightlife, shopping, attivita, trasporti, alloggio, altro.
- "cost": prezzo/gratuito se citato, nella valuta locale, anche con dettaglio (es. "gratis", "RM12", "1 isola RM35, 4 isole RM65"), max 12 parole, altrimenti "". Ignora prezzi in altre valute (es. rupie). Se c'è una tariffa per residenti, riporta quella per turisti.
- "rating": voto dato dal creator se presente (es. "9.7/10"), altrimenti "".
- "days": giorni consigliati se citati (es. "3 giorni"), altrimenti "".
- "note": max 25 parole in italiano: cosa è e perché andarci, cosa ordinare/vedere.
- "cons": aspetti negativi citati (code, prezzi alti, sporcizia, affollamento), max 15 parole, altrimenti "".
- "tips" del luogo: consigli pratici SPECIFICI di quel luogo (orari migliori, dress code, come arrivare, prenotazioni). Max 15 parole ciascuno, in italiano. [] se nessuno.
- "tips" generali: consigli NON legati a un luogo (trasporti, pagamenti, ATM, truffe, stagione migliore, apps), con numeri concreti se citati. "country" = nazione a cui si riferiscono ("" se generici). Max 20 parole ciascuno, in italiano.
- Ignora hashtag, richieste di follow/save, codici sconto, link di affiliazione, hashtag SEO.
- Se il reel non contiene luoghi, "places": []. Non inventare nulla che non sia nel testo."""

# Bot "solo consigli" (type = "tips" in bots_config.json, es. Solo_Traveler): niente luoghi né mappa
TIPS_PROMPT_VERSION = "tips-1"
TIPS_TOPICS = ["bagaglio", "app", "soldi", "sicurezza", "trasporti", "alloggio", "salute",
               "documenti", "lingua e cultura", "mentalità", "altro"]

TIPS_PROMPT = f"""Estrai i consigli di viaggio dalla caption di un reel Instagram.
Rispondi SOLO con JSON valido, senza testo extra né backtick, con questo schema:
{{"tips": [{{"topic": "...", "tip": "..."}}]}}
Regole:
- "topic": uno tra {", ".join(TIPS_TOPICS)}.
- "tip": un consiglio concreto per elemento, max 25 parole in italiano. Mantieni nomi di app, prodotti e marche (es. "Airalo per eSIM economiche").
- Ignora hashtag, richieste di follow/save/commenti, codici sconto, link di affiliazione.
- Non inventare nulla che non sia nel testo. Se non ci sono consigli, "tips": []."""

TIPS_FUZZY_MERGE = 0.86      # sopra questa somiglianza di testo (stesso argomento) i consigli vengono uniti
TIPS_FUZZY_SUGGEST = 0.70    # tra questa e la soglia sopra: solo suggerimento, lo giudica Gemini

TIPS_DUP_PROMPT = """Ti do coppie di consigli di viaggio, ognuna sullo stesso argomento. Per ogni coppia dimmi
se esprimono lo STESSO consiglio (anche con parole diverse): non basta che parlino dello stesso argomento
generico, il consiglio pratico dev'essere lo stesso.
Ogni coppia ha un "id". Rispondi SOLO con JSON: {"results": [{"id": 1, "same": true}, ...]} con tutti gli id."""


# --------------------------------------------------------------------------- parsing

def image_texts_path(txt: Path) -> Path:
    """Testo letto dalle immagini dei post ("post" + link al bot), accanto al txt: {shortcode: testo}."""
    return Path(txt).with_name(f"{Path(txt).stem}_immagini.json")


def load_image_texts(txt: Path) -> dict:
    p = image_texts_path(txt)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_image_text(txt: Path, shortcode: str, text: str):
    data = load_image_texts(txt)
    data[shortcode] = text
    image_texts_path(txt).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def errors_log_path(txt: Path) -> Path:
    """Log degli errori di lettura testo immagini/video, accanto al txt: {shortcode: {type, timestamp, error}}."""
    return Path(txt).with_name(f"{Path(txt).stem}_errori.json")


def load_errors_log(txt: Path) -> dict:
    p = errors_log_path(txt)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_error(txt: Path, shortcode: str, error_type: str, error_msg: str, requested_media: str = "none"):
    """Salva un errore di lettura (type: 'images'/'video'/'api_failure'/etc, requested_media: 'images'/'video'/'both'/'none')."""
    data = load_errors_log(txt)
    data[shortcode] = {
        "type": error_type,
        "requested_media": requested_media,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "error": error_msg[:200]  # primi 200 caratteri del messaggio
    }
    errors_log_path(txt).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def remove_error(txt: Path, shortcode: str):
    """Rimuove un errore dal log (dopo il retry riuscito)."""
    data = load_errors_log(txt)
    data.pop(shortcode, None)
    if data:
        errors_log_path(txt).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        errors_log_path(txt).unlink(missing_ok=True)


def parse_reels(path: Path):
    """Ritorna lista di dict {url, shortcode, caption} dal file txt (+ testo delle immagini, se c'è)."""
    text = path.read_text(encoding="utf-8")
    # l'ultimo pezzo (dopo l'ultimo separatore) è vuoto o un reel che il bot sta ancora scrivendo
    blocks = re.split(r"\n-{10,}\s*\n", text)[:-1]
    reels = []
    for b in blocks:
        m_link = re.search(r"^Link:\s*(\S+)", b, re.M)
        m_date = re.search(r"^Data:\s*(\S+)", b, re.M)
        b_no_date = re.sub(r"\nData:\s*\S+\s*$", "", b) if m_date else b   # altrimenti finisce nella caption (re.S)
        m_cap = re.search(r"^Caption:\s*(.*)", b_no_date, re.M | re.S)
        if not m_link:
            continue
        url = m_link.group(1).strip()
        m_code = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", url)
        if not m_code:
            continue
        caption = m_cap.group(1).strip() if m_cap else ""
        reels.append({"url": url, "shortcode": m_code.group(1), "caption": caption,
                      "date": m_date.group(1).strip() if m_date else ""})
    # deduplica reel ripetuti nel file
    seen, unique = set(), []
    for r in reels:
        if r["shortcode"] not in seen:
            seen.add(r["shortcode"])
            unique.append(r)
    image_texts = load_image_texts(path)
    for r in unique:
        if image_texts.get(r["shortcode"]):   # la caption cambia -> il reel viene rianalizzato (hash in cache)
            r["caption"] += f"\nTesto immagini: {image_texts[r['shortcode']]}"
    return unique


def clean_caption(caption: str) -> str:
    """Toglie hashtag, blocchi {keyword}/[keyword], spazi multipli."""
    c = re.sub(r"\{[^}]*\}|\[[^\]]*\]", " ", caption)   # blocchi SEO tra graffe/quadre
    c = re.sub(r"#\w+", " ", c)                         # hashtag
    c = re.sub(r"[\u2800\u200b]+", " ", c)              # spazi invisibili
    c = re.sub(r"\s+", " ", c)
    return c.strip()


# --------------------------------------------------------------------------- LLM

class QuotaExhausted(Exception):
    pass


def call_gemini_json(client, types, system_prompt: str, user_payload: str, model: str = MODEL) -> dict:
    """Chiamata Gemini generica con risposta JSON e retry su limite al minuto (come extract_with_llm,
    ma senza i campi specifici dell'estrazione luoghi): usata anche da build_dishes.py/build_culture.py."""
    config = types.GenerateContentConfig(system_instruction=system_prompt, response_mime_type="application/json",
                                         temperature=0)
    last_err = None
    for attempt in range(4):
        try:
            resp = client.models.generate_content(model=model, contents=user_payload, config=config)
            raw = resp.text or ""
            m = re.search(r"\{.*\}", raw, re.S)
            if not m:
                raise ValueError(f"Risposta non JSON: {raw[:200]}")
            return json.loads(m.group(0))
        except Exception as e:
            last_err = e
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                if "PerDay" in msg or "per day" in msg.lower():
                    raise QuotaExhausted(msg)
                wait = 20 * (attempt + 1)
                print(f"   limite al minuto raggiunto, aspetto {wait}s...")
                time.sleep(wait)
            else:
                time.sleep(2)
    raise last_err


def extract_with_llm(client, types, caption: str, country_hint: str,
                     prompt: str = SYSTEM_PROMPT, version=PROMPT_VERSION) -> dict:
    user = f"Contesto: raccolta di reel su {country_hint}.\n\nCaption:\n{caption}"
    config = types.GenerateContentConfig(
        system_instruction=prompt,
        response_mime_type="application/json",
        temperature=0,
    )
    last_err = None
    for attempt in range(4):
        try:
            resp = client.models.generate_content(model=MODEL, contents=user, config=config)
            raw = resp.text or ""
            m = re.search(r"\{.*\}", raw, re.S)
            if not m:
                raise ValueError(f"Risposta non JSON: {raw[:200]}")
            data = json.loads(m.group(0))
            data.setdefault("places", [])
            data.setdefault("tips", [])
            data["_v"] = version
            return data
        except Exception as e:
            last_err = e
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                if "PerDay" in msg or "per day" in msg.lower():
                    raise QuotaExhausted(msg)
                wait = 20 * (attempt + 1)
                print(f"   limite al minuto raggiunto, aspetto {wait}s...")
                time.sleep(wait)
            else:
                time.sleep(2)
    raise last_err


def caption_hash(r) -> str:
    return hashlib.sha1(r["caption"].encode("utf-8")).hexdigest()[:12]


def run_extraction(reels, cache_path: Path, country_hint: str, use_llm: bool = True,
                   prompt: str = SYSTEM_PROMPT, version=PROMPT_VERSION):
    """Ritorna (cache, quota_esaurita)."""
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    # nuovi, analizzati con un prompt vecchio (se la quota finisce resta la versione vecchia)
    # o con la caption cambiata (es. aggiunto "Testo immagini"); le voci vecchie senza _h non si rifanno
    def changed(r):
        c = cache.get(r["shortcode"], {})
        return c.get("_v") != version or c.get("_h", caption_hash(r)) != caption_hash(r)
    todo = [r for r in reels if changed(r)]
    if todo and not use_llm:
        print(f"Quota esaurita: {len(todo)} reel da analizzare restano per il prossimo giro.")
        return cache, True
    if todo:
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            sys.exit("Installa la libreria: pip install google-genai")
        if not os.environ.get("GEMINI_API_KEY"):
            sys.exit("Manca la variabile d'ambiente GEMINI_API_KEY (chiave gratuita su aistudio.google.com/apikey)")
        client = genai.Client()  # legge GEMINI_API_KEY
        print(f"Modello: {MODEL}")
        for i, r in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {r['shortcode']}")
            try:
                cache[r["shortcode"]] = extract_with_llm(client, types, clean_caption(r["caption"]), country_hint,
                                                         prompt, version)
                cache[r["shortcode"]]["_h"] = caption_hash(r)
            except QuotaExhausted:
                print("⛔ Quota giornaliera esaurita. Rilancia domani: i reel già fatti sono in cache.")
                return cache, True
            except Exception as e:  # non salvare in cache: verrà ritentato al prossimo giro
                print(f"   ⚠️ errore, salto: {e}")
                continue
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            time.sleep(PAUSE_SECONDS)
    else:
        print("Nessun reel nuovo: uso la cache.")
    return cache, False


# --------------------------------------------------------------------------- raggruppamento

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\b(the|il|la|lo|le|di|of)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def core_name(name: str, city: str, country: str) -> str:
    """Nome normalizzato senza parole generiche né città/nazione, usato solo per il confronto."""
    n = norm(name)
    c = re.sub(GENERIC_WORDS, " ", n)
    for place in (city, country):
        if norm(place or ""):
            c = re.sub(rf"\b{re.escape(norm(place))}\b", " ", c)
    c = re.sub(r"\s+", " ", c).strip()
    return c or n   # se resta vuoto (es. il luogo è la città stessa) usa il nome intero


def canonical_city(city: str) -> str:
    city = (city or "").strip()
    return CITY_ALIASES.get(norm(city), city)


def slug(s: str) -> str:
    return norm(s).replace(" ", "-")[:60]


def compatible(a: str, b: str) -> bool:
    """Due campi (città/nazione) sono compatibili se uno è vuoto o sono uguali."""
    na, nb = norm(a or ""), norm(b or "")
    return not na or not nb or na == nb


def load_aliases(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {norm(k): v for k, v in raw.items()}


def group_places(reels, cache, aliases):
    groups = []          # ogni gruppo: dict con names(set norm), records
    suggestions = set()

    for reel in reels:
        entry = cache.get(reel["shortcode"])
        if not entry:
            continue
        for p in entry.get("places", []):
            name = (p.get("name") or "").strip()
            if not name:
                continue
            name = aliases.get(norm(name), name)      # unione manuale
            city = canonical_city(p.get("city"))
            if not norm(name):
                continue
            n = core_name(name, city, p.get("country"))

            target = None
            for g in groups:
                if not (compatible(p.get("country"), g["country"]) and compatible(city, g["city"])):
                    continue
                best = max(
                    (1.0 if n == gn else difflib.SequenceMatcher(None, n, gn).ratio()) for gn in g["norms"]
                )
                if best >= FUZZY_MERGE:
                    target = g
                    break
                if best >= FUZZY_SUGGEST or any(n in gn or gn in n for gn in g["norms"]):
                    suggestions.add(tuple(sorted((name, g["name"]))))
            if target is None:
                target = {"name": name, "norms": set(), "city": "", "country": "",
                          "aliases": set(), "records": []}
                groups.append(target)
            target["norms"].add(n)
            if name != target["name"]:
                target["aliases"].add(name)
            target["city"] = target["city"] or city
            target["country"] = target["country"] or (p.get("country") or "")
            target["records"].append({
                "reel": reel["url"],
                "date": reel.get("date") or "",
                "category": p.get("category") or "altro",
                "cost": p.get("cost") or "",
                "note": p.get("note") or "",
                "rating": p.get("rating") or "",
                "days": p.get("days") or "",
                "cons": p.get("cons") or "",
                "tips": [t for t in (p.get("tips") or []) if isinstance(t, str) and t.strip()],
            })

    places = []
    for g in groups:
        cats = Counter(r["category"] for r in g["records"])
        # una sola voce per reel (un reel può nominare due volte lo stesso posto)
        mentions, seen = [], set()
        for r in g["records"]:
            if r["reel"] in seen:
                continue
            seen.add(r["reel"])
            mentions.append({k: r[k] for k in ("reel", "date", "note", "cost", "rating", "days", "cons", "tips")})
        costs = [m["cost"] for m in mentions if m["cost"]]
        dates = sorted(m["date"] for m in mentions if m["date"])   # "YYYY-MM-DD": l'ordine alfabetico è cronologico

        def uniq(key, dedupe_sim=0.85):
            """Ritorna liste uniche di valori, deduplicando testi molto simili (fuzzy match)."""
            values = [m[key] for m in mentions if m[key]]
            if not values:
                return []
            # Deduplicazione: mantieni il primo di ogni gruppo simile
            kept = []
            for val in values:
                # Controlla se è già stato visto un testo simile
                is_dup = any(difflib.SequenceMatcher(None, val, k).ratio() >= dedupe_sim for k in kept)
                if not is_dup:
                    kept.append(val)
            return kept
        notes_list = uniq("note")
        tips_list = list(dict.fromkeys(t for m in mentions for t in m["tips"]))
        cons_list = uniq("cons")

        place_data = {
            "id": f"{slug(g['country'])}_{slug(g['name'])}".strip("_"),
            "name": g["name"],
            "aliases": sorted(g["aliases"]),
            "city": g["city"],
            "country": g["country"],
            "category": cats.most_common(1)[0][0],
            "costs": sorted(set(costs)),
            "notes": notes_list,
            "creator_ratings": uniq("rating"),   # voti dati nei reel (non quello personale)
            "days": uniq("days"),
            "cons": cons_list,
            "tips": tips_list,
            "mention_count": len(mentions),
            "mentions": mentions,
            "last_mention_date": dates[-1] if dates else "",   # reel più recente che lo cita
            # campi di stato per la futura Evo 1 (feedback/visitati)
            "status": "da_fare",
            "rating": None,
            "user_notes": "",
        }
        places.append(place_data)
    places.sort(key=lambda p: (p["country"], p["city"], -p["mention_count"], p["name"]))
    return places, sorted(suggestions)


def preserve_state(places, out_path: Path):
    """Ricopia status/rating/user_notes dalla versione precedente di places.json."""
    if not out_path.exists():
        return
    old = {p["id"]: p for p in json.loads(out_path.read_text(encoding="utf-8"))}
    for p in places:
        o = old.get(p["id"])
        if o:
            p["status"], p["rating"], p["user_notes"] = o.get("status", "da_fare"), o.get("rating"), o.get("user_notes", "")


DUP_PROMPT = """Ti do coppie di luoghi estratti da reel di viaggio (nome, città, nazione, descrizione).
Per ogni coppia dimmi se indicano lo STESSO luogo fisico (nomi diversi, abbreviazioni, traduzioni, refusi).
NON sono lo stesso: luoghi vicini o collegati (un tempio e il quartiere in cui si trova, un'attrazione
dentro un parco, due ristoranti diversi della stessa via).
Ogni coppia ha un "id". Rispondi SOLO con JSON: {"results": [{"id": 1, "same": true}, ...]} con tutti gli id."""


def pair_key(a: str, b: str) -> str:
    return "|".join(sorted((norm(a), norm(b))))


def resolve_duplicates(places, suggestions, out_dir: Path, use_llm: bool) -> dict:
    """Fa giudicare a Gemini (una richiesta sola) le coppie di possibili doppioni mai viste.
    Le coppie uguali vanno in aliases.json; tutti i giudizi in duplicate_checks.json (non richiesti di nuovo).
    Ritorna {"merged": [(nome, unito_a)], "open": coppie ancora da giudicare, "quota_hit": bool}."""
    checks_path, aliases_path = out_dir / "duplicate_checks.json", out_dir / "aliases.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8")) if checks_path.exists() else {}
    todo = [s for s in suggestions if pair_key(*s) not in checks]
    result = {"merged": [], "open": todo, "quota_hit": False}
    if not todo or not use_llm or not os.environ.get("GEMINI_API_KEY"):
        return result

    by_name = {}
    for p in places:
        for n in [p["name"]] + p["aliases"]:
            by_name.setdefault(norm(n), p)

    def info(name):
        p = by_name.get(norm(name), {})
        return {"nome": name, "città": p.get("city", ""), "nazione": p.get("country", ""),
                "descrizione": (p.get("notes") or [""])[0]}

    from google import genai
    from google.genai import types
    client = genai.Client()
    payload = json.dumps([{"id": i, "coppia": [info(a), info(b)]} for i, (a, b) in enumerate(todo, 1)],
                         ensure_ascii=False)
    try:
        resp = client.models.generate_content(
            model=MODEL, contents=payload,
            config=types.GenerateContentConfig(system_instruction=DUP_PROMPT,
                                               response_mime_type="application/json", temperature=0))
        answers = json.loads(re.search(r"\{.*\}", resp.text or "", re.S).group(0))["results"]
        by_id = {int(r["id"]): bool(r["same"]) for r in answers if isinstance(r, dict) and "id" in r}
    except Exception as e:
        msg = str(e)
        result["quota_hit"] = "429" in msg or "RESOURCE_EXHAUSTED" in msg
        print(f"⚠️ Controllo doppioni non riuscito, riprovo al prossimo giro: {msg[:200]}")
        return result

    aliases = json.loads(aliases_path.read_text(encoding="utf-8")) if aliases_path.exists() else {}
    # gruppi di nomi uguali (A=B e A=C -> A=B=C), compresi gli alias già presenti
    parent = {}

    def root(n):
        while parent.get(n, n) != n:
            n = parent[n]
        return n

    names = {}
    for k, v in aliases.items():
        names.setdefault(norm(k), k), names.setdefault(norm(v), v)
        parent[root(norm(k))] = root(norm(v))
    for i, (a, b) in enumerate(todo, 1):
        if i not in by_id:   # senza risposta: resta da giudicare
            continue
        is_same = by_id[i]
        checks[pair_key(a, b)] = is_same
        if is_same:
            names.setdefault(norm(a), a), names.setdefault(norm(b), b)
            parent[root(norm(a))] = root(norm(b))

    clusters = {}
    for n in names:
        clusters.setdefault(root(n), []).append(n)
    old = {norm(k): v for k, v in aliases.items()}
    aliases = {}
    for members in clusters.values():
        # nome da tenere: il luogo più citato, poi il nome già usato come principale, poi il più corto
        keep = max(members, key=lambda n: (by_name.get(n, {}).get("mention_count", 0),
                                           by_name.get(n, {}).get("name") == names[n], -len(names[n])))
        for n in members:
            if n != keep:
                aliases[names[n]] = names[keep]
                if norm(old.get(n, "")) != keep:
                    result["merged"].append((names[n], names[keep]))
    aliases_path.write_text(json.dumps(aliases, ensure_ascii=False, indent=2), encoding="utf-8")
    checks_path.write_text(json.dumps(checks, ensure_ascii=False, indent=1), encoding="utf-8")
    result["open"] = [s for s in todo if pair_key(*s) not in checks]
    return result


# --------------------------------------------------------------------------- output

# Gemini Notebook non legge i .md: una copia .txt per nazione, in Aspettativa/FONTI/<Nazione>/
# (insieme alle fonti originali dell'utente per quella nazione: guide, PDF); i dati di lavoro del
# bot restano in <Nazione>_bot_output/, separati dalle fonti per Notebook
NOTEBOOK_GUIDE_NAME = "00_LEGGIMI_come_sono_creati_i_file.txt"
NOTEBOOK_GUIDE = """COME SONO STATI CREATI I FILE <Nazione>.txt DI QUESTA CARTELLA
(guida per Gemini Notebook: leggila prima di rispondere usando gli altri file;
i PDF e i documenti dell'utente sono fonti a parte, non generate da qui)

1. PROVENIENZA DEI DATI
- Ogni file <Nazione>.txt raccoglie luoghi e consigli presi da reel e post Instagram di travel creator,
  salvati dall'utente durante la preparazione di un viaggio di 6 mesi nel Sud-Est asiatico.
- Per ogni reel si usa la caption; per alcuni anche il testo scritto nelle immagini (post) o le scritte
  in sovrimpressione nel video. Il parlato dei video NON è incluso.
- Un modello AI (Gemini Flash-Lite) ha estratto dai testi i luoghi e i consigli, riassumendoli in italiano.
  Può contenere errori o omissioni: non è verificato a mano. Prezzi e orari sono quelli del reel e possono
  essere cambiati.
- Lo stesso luogo citato in più reel è unito in un solo punto (i nomi simili sono stati confrontati in
  automatico).
- Ogni file inizia con "Aggiornato al: aaaa-mm-gg" = quando è stato generato questo .txt (non la data dei reel).

2. STRUTTURA DI UN FILE <Nazione>.txt
- "## Nazione – Città": sezione con i luoghi di quella città ("?" = città o nazione non indicata nel reel).
  Il nome del file è la nazione del bot, ma un file può contenere anche luoghi di nazioni vicine:
  vale sempre la nazione scritta nella sezione.
- Riga del luogo:
  - **Nome** [categoria] ×N | costo | voto X | giorni (stato | mio voto N/5)
  - categoria: cibo, natura, cultura, nightlife, shopping, attivita, trasporti, alloggio, altro.
  - ×N = numero di reel diversi che citano il luogo (assente se 1): più è alto, più il luogo è consigliato.
  - costo: prezzo citato dai creator, nella valuta locale.
  - voto: voto dato dal creator nel reel (non dall'utente).
  - giorni: giorni consigliati dal creator.
  - stato: da_fare = non ancora visitato; visitato = già visto dall'utente; scartato = l'utente non ci vuole andare.
  - mio voto N/5: voto dato dall'utente dopo la visita (1 = pessimo, 5 = ottimo).
  - ultimo reel: aaaa-mm-gg = data di pubblicazione del reel più recente che cita il luogo (assente se non nota).
    Più è vecchia, più prezzi/orari/apertura rischiano di essere cambiati.
- Righe sotto il luogo:
  - 📝 mia nota: commento scritto dall'utente (ha la precedenza su tutto il resto).
  - righe semplici: descrizione dai reel (cosa è, perché andarci, cosa ordinare/vedere).
  - 💡 consiglio pratico specifico del luogo (orari, dress code, come arrivare, prenotazioni).
  - ⚠️ aspetto negativo citato (code, prezzi alti, affollamento...).
  - reel: link ai reel originali.
- "## Consigli – Nazione": consigli generali non legati a un luogo (trasporti, soldi, SIM, truffe...).

3. FILE DI SOLI CONSIGLI (es. Solo_Traveler.txt)
- Consigli generali per viaggiare (in solitaria), raggruppati per argomento: bagaglio, app, soldi,
  sicurezza, trasporti, alloggio, salute, documenti, lingua e cultura, mentalità, altro.
  ×N = numero di reel diversi che danno lo stesso consiglio (assente se 1); ogni riga ha il link
  a tutti i reel da cui viene (consigli quasi identici tra loro sono già stati uniti).

4. LINK DA BLOG ESTERNI (file <dominio-blog>_link_guide.txt e blog_sorgenti.txt, se presenti)
- Non generati da reel Instagram, ma da blog di viaggio che l'utente ha mandato al bot.
- <dominio-blog>_link_guide.txt: ogni riga è "titolo pagina - link" di una sottopagina di quel blog
  (altri articoli/guide dello stesso autore). Sono solo indirizzi, il contenuto non è stato letto: se
  una risposta può beneficiarne (es. l'utente chiede di quell'argomento/luogo), chiedi se vuoi che li
  importi come fonte a parte prima di usarli.
- blog_sorgenti.txt: elenco ("aaaa-mm-gg - link") dei blog che l'utente ha mandato al bot, non delle
  loro sottopagine; utile solo per sapere quali blog sono già stati passati al bot.
- I luoghi su Google Maps trovati negli stessi blog NON sono in questi file: finiscono nei CSV di
  MyMaps (con le coordinate, non nei file per Notebook).

5. COME USARLI NELLE RISPOSTE
- Proponi solo luoghi "da_fare"; non riproporre quelli "scartato".
- Tieni conto dei gusti dell'utente: luoghi simili a quelli con mio voto alto (4-5) sono preferiti,
  simili a quelli con voto basso (1-2) evitati; rispetta le note dell'utente.
- A parità di tutto, preferisci i luoghi con ×N più alto.
- Rispondi in italiano, in modo breve e schematico; cita il nome esatto del luogo e la città.
- Se un'informazione non è nei file, dillo invece di inventarla.
- DATI DA FILE vs DATI DAL WEB: i gusti dell'utente (stato, mio voto, mia nota) esistono solo nei file, il
  web non li conosce: usa sempre i file per queste informazioni. Per orari, prezzi attuali, chiusure
  temporanee/definitive ed eventi, invece, verifica sul web quando la domanda riguarda un piano concreto
  (es. "cosa faccio oggi/domani a X"): i dati dei reel possono avere mesi ed essere superati. Se trovi una
  differenza tra file e web, segnalala e preferisci l'informazione più recente.
- Luoghi delle categorie nightlife, mercati e locali stagionali/pop-up cambiano spesso (aprono, chiudono,
  cambiano orari/nome): per questi verifica online più spesso che per templi, parchi o attrazioni fisse.

5. AGGIORNAMENTO
- I file vengono rigenerati in automatico (sul PC o sul telefono dell'utente) quando arrivano nuovi reel o feedback;
  in Gemini Notebook vanno ricaricati a mano, quindi potrebbero non essere gli ultimi.
"""


def notebook_path(out_dir: Path) -> Path:
    """Aspettativa/FONTI/<Nazione>/<Nazione>.txt (da Aspettativa/BOT_OUTPUT/<Nazione>_bot_output,
    o Aspettativa/<Nazione>_bot_output nel vecchio schema senza BOT_OUTPUT/)."""
    name = out_dir.name.removesuffix("_output").removesuffix("_bot")
    root = out_dir.parent.parent if out_dir.parent.name == "BOT_OUTPUT" else out_dir.parent
    return root / "FONTI" / name / f"{name}.txt"


def write_notebook(text: str, out_dir: Path):
    """Copia .txt per Gemini Notebook, in una sottocartella per nazione dentro FONTI/;
    la guida sta una sola volta in FONTI/ (non ripetuta in ogni sottocartella)."""
    from datetime import date
    path = notebook_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"Aggiornato al: {date.today().isoformat()}\n\n"
    path.write_text(header + text, encoding="utf-8")
    (path.parent.parent / NOTEBOOK_GUIDE_NAME).write_text(NOTEBOOK_GUIDE, encoding="utf-8")


def write_image_texts(txt_path: Path, out_dir: Path):
    """Genera file txt con testo estratto dalle immagini/video, leggibile da Gemini Notebook.
    Salva in FONTI/<Nazione>/<Nazione>_testo_immagini.txt"""
    from datetime import date
    image_texts = load_image_texts(txt_path)
    if not image_texts:
        return  # nessun testo estratto

    reels = parse_reels(txt_path)
    reel_map = {r["shortcode"]: r for r in reels}

    lines = [
        f"Testo estratto dalle immagini e video — {txt_path.stem.replace('_bot', '')}",
        f"Aggiornato al: {date.today().isoformat()}\n",
        "Questo file contiene tutto il testo estratto dalle immagini e dai video dei reel,",
        "utile come riferimento durante la ricerca con Gemini Notebook.\n",
    ]

    for shortcode, text in sorted(image_texts.items()):
        reel = reel_map.get(shortcode)
        if not reel:
            continue

        lines.append("─" * 80)
        lines.append(f"Shortcode: {shortcode}")
        lines.append(f"Link: {reel['url']}")
        if reel.get("date"):
            lines.append(f"Data: {reel['date']}")
        lines.append("")
        lines.append(text)
        lines.append("")

    # Salva in FONTI/<Nazione>/<Nazione>_testo_immagini.txt
    notebook_dir = notebook_path(out_dir).parent
    notebook_dir.mkdir(parents=True, exist_ok=True)
    nation = txt_path.stem.replace("_bot", "")
    image_text_path = notebook_dir / f"{nation}_testo_immagini.txt"
    image_text_path.write_text("\n".join(lines), encoding="utf-8")


def load_blog_places(out_dir: Path) -> list:
    """Carica i luoghi importati da blog (blog_places.json) e li converte nel formato di places.json."""
    blog_path = out_dir / "blog_places.json"
    if not blog_path.exists():
        return []
    blog_data = json.loads(blog_path.read_text(encoding="utf-8"))
    blog_places = []
    for b in blog_data:
        blog_places.append({
            "id": slug(b["name"]),
            "name": b["name"],
            "city": b.get("city", ""),
            "country": b.get("country", ""),
            "category": b.get("category", "altro"),
            "costs": [],
            "creator_ratings": [],
            "days": [],
            "notes": [f"Da blog: {b.get('source_url', '')}"],
            "tips": [],
            "cons": [],
            "mentions": [{"reel": b.get("url", ""), "country": b.get("country", "")}],
            "mention_count": 1,
            "status": "da_fare",
            "rating": None,
            "user_notes": None,
            "last_mention_date": b.get("added", ""),
            "from_blog": True
        })
    return blog_places


def write_markdown(places, tips, path: Path):
    # Carica anche i luoghi da blog
    out_dir = path.parent
    blog_places = load_blog_places(out_dir)
    all_places = places + blog_places
    all_places = sorted(all_places, key=lambda p: (p.get("country", ""), p.get("city", "")))

    lines = []
    cur = None
    for p in all_places:
        key = (p["country"], p["city"])
        if key != cur:
            cur = key
            lines.append(f"\n## {p['country'] or '?'} – {p['city'] or '?'}")
        star = f" ×{p['mention_count']}" if p["mention_count"] > 1 else ""
        cost = f" | {', '.join(p['costs'])}" if p["costs"] else ""
        vote = f" | voto {', '.join(p['creator_ratings'])}" if p.get("creator_ratings") else ""
        days = f" | {', '.join(p['days'])}" if p.get("days") else ""
        mine = f" | mio voto {p['rating']}/5" if p.get("rating") else ""
        last = f" | ultimo reel: {p['last_mention_date']}" if p.get("last_mention_date") else ""
        lines.append(f"- **{p['name']}** [{p['category']}]{star}{cost}{vote}{days}{last} ({p['status']}{mine})")
        if p.get("user_notes"):
            lines.append(f"  - 📝 mia nota: {p['user_notes']}")
        for n in p["notes"]:
            lines.append(f"  - {n}")
        for t in p.get("tips", []):
            lines.append(f"  - 💡 {t}")
        for c in p.get("cons", []):
            lines.append(f"  - ⚠️ {c}")
        lines.append("  - reel: " + ", ".join(m["reel"] for m in p["mentions"]))

    by_country = {}
    for t in tips:
        by_country.setdefault(t["country"], []).append(t)
    for country in sorted(by_country):
        lines.append(f"\n## Consigli – {country or 'generali'}")
        for t in by_country[country]:
            source = f" [da: {t['reel']}]" if t.get("reel") else ""
            lines.append(f"- {t['tip']}{source}")
    text = "\n".join(lines).strip() + "\n"
    path.write_text(text, encoding="utf-8")
    title = notebook_path(path.parent).stem
    write_notebook(f"# {title} – luoghi e consigli dai reel Instagram ({len(places)} luoghi)\n\n{text}", path.parent)


def write_culture(places, tips, out_dir: Path):
    """Crea <Nazione>_culture.txt con informazioni culturali dai places.json,
    tracciando le fonti (reel) per ogni info. Organizzato per luogo e consigli generali."""
    from datetime import date
    lines = [f"Informazioni culturali e curiosità\nAggiornato al: {date.today().isoformat()}\n"]

    # Carica anche i luoghi da blog (ma non includere nella culture, solo nei consigli generali)
    blog_places = load_blog_places(out_dir)
    all_places = places + blog_places

    # Raggruppa per città
    by_city = {}
    for p in all_places:
        key = (p["country"], p["city"])
        by_city.setdefault(key, []).append(p)

    for (country, city), places_in_city in sorted(by_city.items()):
        lines.append(f"\n## {country or '?'} – {city or '?'}\n")

        for p in places_in_city:
            lines.append(f"### {p['name']}")

            # Fonte: reel che lo citano
            sources = [m["reel"] for m in p["mentions"]]
            if sources:
                shown_sources = ", ".join(sources[:3])
                if len(sources) > 3:
                    shown_sources += f" (+{len(sources)-3})"
                lines.append(f"Fonte: {shown_sources}\n")

            # Descrizione breve
            if p.get("notes"):
                lines.append(f"**Descrizione:** {p['notes'][0]}\n")

            # Tips culturali/pratici specifici del luogo
            if p.get("tips"):
                lines.append("**Consigli e dettagli:**")
                for tip in p["tips"]:
                    lines.append(f"- {tip}")
                lines.append("")

            # Contro/criticità
            if p.get("cons"):
                lines.append("**Cose da sapere:**")
                for con in p["cons"]:
                    lines.append(f"- {con}")
                lines.append("")

            lines.append("")  # linea vuota tra i luoghi

    # Consigli generali (non legati a un luogo)
    tips_by_country = {}
    for t in tips:
        tips_by_country.setdefault(t.get("country") or "Generali", []).append(t)

    if tips_by_country:
        lines.append("\n## Consigli generali di viaggio\n")
        for country in sorted(tips_by_country):
            lines.append(f"### {country}\n")
            for t in tips_by_country[country]:
                source = f" [da: {t['reel']}]" if t.get("reel") else ""
                lines.append(f"- {t['tip']}{source}")
            lines.append("")

    culture_path = notebook_path(out_dir)
    culture_path = culture_path.parent / f"{culture_path.stem}_culture.txt"
    culture_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).strip() + "\n"
    culture_path.write_text(text, encoding="utf-8")


def collect_tips(reels, cache):
    tips = []
    for r in reels:
        entry = cache.get(r["shortcode"], {})
        # nazione di ripiego: quella dei luoghi del reel, se unica
        countries = {p.get("country") or "" for p in entry.get("places", [])} - {""}
        fallback = countries.pop() if len(countries) == 1 else ""
        for t in entry.get("tips", []):
            if isinstance(t, str):   # formato della cache vecchia
                t = {"tip": t}
            if not (t.get("tip") or "").strip():
                continue
            tips.append({"tip": t["tip"], "country": t.get("country") or fallback, "reel": r["url"]})
    # dedup grossolano
    out, seen = [], set()
    for t in tips:
        k = norm(t["tip"])
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


# --------------------------------------------------------------------------- main

# Config con token/chiavi: solo in locale su ogni dispositivo, mai nella cartella del codice (che è su Drive)
CONFIG_DIR = Path(os.environ.get("BOT_CONFIG_DIR") or Path.home() / ".viaggio_bot")
BOTS_CONFIG_PATH = CONFIG_DIR / "bots_config.json"


def output_dir(txt: Path) -> Path:
    """Cartella <nome>_output del bot: il txt sta dentro (nuovo schema) o accanto (vecchio schema)."""
    txt = Path(txt)
    if txt.parent.name == f"{txt.stem}_output":
        return txt.parent
    return txt.parent / f"{txt.stem}_output"


def bot_entries() -> list:
    """[(txt, tips_only)] dai bot in bots_config.json; "type": "tips" = bot di soli consigli (no luoghi)."""
    if not BOTS_CONFIG_PATH.exists():
        sys.exit(f"File non trovato: {BOTS_CONFIG_PATH}")
    bots = json.loads(BOTS_CONFIG_PATH.read_text(encoding="utf-8")).get("bots", [])
    return [(Path(b["txt_file_path"]), b.get("type") == "tips") for b in bots if b.get("txt_file_path")]


def txt_files_from_bots_config(include_tips: bool = False) -> list:
    """Txt dei bot per nazione (quelli di soli consigli solo se include_tips)."""
    return [txt for txt, tips_only in bot_entries() if include_tips or not tips_only]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("txt_file", nargs="?")
    ap.add_argument("--all", action="store_true", help="elabora tutti i txt elencati in bots_config.json")
    ap.add_argument("--out-dir", default=None, help="default: cartella <nome file>_output che contiene il txt")
    ap.add_argument("--tips", action="store_true", help="il txt contiene solo consigli generali (niente luoghi)")
    args = ap.parse_args()

    if args.all:
        files, out_dir = bot_entries(), None
    elif args.txt_file:
        files, out_dir = [(Path(args.txt_file), args.tips)], (Path(args.out_dir) if args.out_dir else None)
    else:
        ap.error("indica un file txt oppure --all")

    use_llm = True
    for src, tips_only in files:
        print(f"\n===== {src.name} =====")
        if not src.exists():
            print(f"File non trovato, salto: {src}")
            continue
        # dopo la quota esaurita si rigenerano gli output solo dalla cache
        use_llm = not process_file(src, out_dir, use_llm, tips_only)["quota_hit"] and use_llm


def group_tips(reels: list, cache: dict, checks: dict) -> tuple:
    """Consigli raggruppati per somiglianza di testo, confrontando solo consigli dello stesso argomento
    (come group_places, ma sul testo invece che sul nome). checks: giudizi già dati da Gemini su coppie
    ambigue (tips_duplicate_checks.json), per unire in automatico quelle già confermate.
    Ritorna (tips, suggestions ancora da giudicare)."""
    groups_by_topic, suggestions = {}, []
    for r in reels:
        for t in cache.get(r["shortcode"], {}).get("tips", []):
            text = (t.get("tip") or "").strip() if isinstance(t, dict) else ""
            if not text:
                continue
            topic = t.get("topic") if t.get("topic") in TIPS_TOPICS else "altro"
            groups = groups_by_topic.setdefault(topic, [])
            n = norm(text)
            target = None
            for g in groups:
                if n == g["norm"]:
                    target = g
                    break
                same = checks.get(pair_key(text, g["tip"]))
                if same:
                    target = g
                    break
                if same is None and difflib.SequenceMatcher(None, n, g["norm"]).ratio() >= TIPS_FUZZY_SUGGEST:
                    suggestions.append((text, g["tip"]))
            if target is None:
                target = {"tip": text, "norm": n, "topic": topic, "reels": []}
                groups.append(target)
            if r["url"] not in target["reels"]:
                target["reels"].append(r["url"])

    tips = [{"topic": g["topic"], "tip": g["tip"], "reels": g["reels"], "mention_count": len(g["reels"])}
            for groups in groups_by_topic.values() for g in groups]
    return tips, sorted(set(suggestions))


def resolve_tips_duplicates(suggestions: list, out_dir: Path, use_llm: bool) -> dict:
    """Fa giudicare a Gemini (una richiesta sola) le coppie di consigli ambigui mai viste, come
    resolve_duplicates ma per il testo dei consigli. Il giudizio va in tips_duplicate_checks.json
    (non richiesto di nuovo). Ritorna {"merged": bool, "open": coppie ancora da giudicare, "quota_hit": bool}."""
    checks_path = out_dir / "tips_duplicate_checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8")) if checks_path.exists() else {}
    todo = [s for s in suggestions if pair_key(*s) not in checks]
    result = {"merged": False, "open": todo, "quota_hit": False}
    if not todo or not use_llm or not os.environ.get("GEMINI_API_KEY"):
        return result

    from google import genai
    from google.genai import types
    client = genai.Client()
    payload = json.dumps([{"id": i, "coppia": [a, b]} for i, (a, b) in enumerate(todo, 1)], ensure_ascii=False)
    try:
        resp = client.models.generate_content(
            model=MODEL, contents=payload,
            config=types.GenerateContentConfig(system_instruction=TIPS_DUP_PROMPT,
                                               response_mime_type="application/json", temperature=0))
        answers = json.loads(re.search(r"\{.*\}", resp.text or "", re.S).group(0))["results"]
        by_id = {int(r["id"]): bool(r["same"]) for r in answers if isinstance(r, dict) and "id" in r}
    except Exception as e:
        msg = str(e)
        result["quota_hit"] = "429" in msg or "RESOURCE_EXHAUSTED" in msg
        print(f"⚠️ Controllo doppioni consigli non riuscito, riprovo al prossimo giro: {msg[:200]}")
        return result

    for i, (a, b) in enumerate(todo, 1):
        if i in by_id:
            checks[pair_key(a, b)] = by_id[i]
            result["merged"] = result["merged"] or by_id[i]
    checks_path.write_text(json.dumps(checks, ensure_ascii=False, indent=1), encoding="utf-8")
    result["open"] = [s for s in todo if pair_key(*s) not in checks]
    return result


def process_tips_file(src: Path, out_dir: Path, reels: list, use_llm: bool, summary: dict) -> dict:
    """Bot di soli consigli: tips.json + tips.md raggruppati per argomento (fuzzy match sul testo per
    unire consigli uguali da reel diversi; le coppie ambigue le giudica Gemini, come per i luoghi)."""
    cache, quota_hit = run_extraction(reels, out_dir / "extraction_cache.json", "consigli per viaggiare (in solitaria)", use_llm,
                                      TIPS_PROMPT, TIPS_PROMPT_VERSION)
    tips_path = out_dir / "tips.json"
    old = {norm(t["tip"]) for t in json.loads(tips_path.read_text(encoding="utf-8"))} if tips_path.exists() else set()

    checks_path = out_dir / "tips_duplicate_checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8")) if checks_path.exists() else {}
    tips, suggestions = group_tips(reels, cache, checks)
    dups = resolve_tips_duplicates(suggestions, out_dir, use_llm and not quota_hit)
    if dups["merged"]:
        tips, suggestions = group_tips(reels, cache, json.loads(checks_path.read_text(encoding="utf-8")))
    suggestions = dups["open"]   # quelle giudicate diverse non vengono più riproposte
    quota_hit = quota_hit or dups["quota_hit"]

    tips_path.write_text(json.dumps(tips, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Consigli di viaggio ({len(tips)})", ""]
    for topic in TIPS_TOPICS:
        group = [t for t in tips if t["topic"] == topic]
        if group:
            lines += [f"## {topic.capitalize()} ({len(group)})", ""]
            for t in group:
                star = f" ×{t['mention_count']}" if t["mention_count"] > 1 else ""
                reel_links = ", ".join(f"[reel]({r})" for r in t["reels"])
                lines.append(f"- {t['tip']}{star} ({reel_links})")
            lines.append("")
    (out_dir / "tips.md").write_text("\n".join(lines), encoding="utf-8")
    write_notebook("\n".join(lines), out_dir)

    if suggestions:
        print("\nPossibili consigli duplicati ancora da controllare (li giudica Gemini al prossimo giro):")
        for a, b in suggestions:
            print(f"  - {a}  <->  {b}")

    print(f"\nConsigli: {len(tips)}\nFile salvati in: {out_dir}")
    summary.update(quota_hit=quota_hit, tips=len(tips), suggestions=suggestions,
                   new_tips=[t["tip"] for t in tips if norm(t["tip"]) not in old])
    return summary


CULTURE_SYNTH_PROMPT_VERSION = "culture-synth-1"

def synthesize_cultural_place_text(places: list, use_llm: bool = True) -> None:
    """Sintetizza descrizioni e consigli dei luoghi culturali in paragrafi discorsivi.
    Aggiunge i campi _notes_synth e _tips_synth a places di categoria "cultura"."""
    if not use_llm:
        return

    try:
        import google.generativeai as genai
        if not os.getenv("GEMINI_API_KEY"):
            print("  ❌ GEMINI_API_KEY non impostato, skipping sintesi")
            return
    except ImportError:
        print("  ❌ google.generativeai non installato, skipping sintesi")
        return

    cultural_places = [p for p in places if p.get("category") == "cultura" and p.get("notes")]
    if not cultural_places:
        return

    print(f"Sintetizzando {len(cultural_places)} luoghi culturali...")

    for p in cultural_places:
        # Se già sintetizzato e non è cambiato, salta
        if p.get("_notes_synth") and p.get("_notes_synth_version") == CULTURE_SYNTH_PROMPT_VERSION:
            continue

        try:
            model = genai.GenerativeModel(MODEL)

            # Sintetizza descrizioni
            if p.get("notes"):
                notes_text = "\n".join(f"- {n}" for n in p["notes"])
                prompt_desc = f"""Sintetizza questi testi su un luogo culturale in UN unico paragrafo discorsivo coerente:
{notes_text}

Scrivi come un paragrafo continuo, naturale e senza ripetizioni, mantenendo tutte le informazioni importanti."""

                resp_desc = model.generate_content(prompt_desc)
                p["_notes_synth"] = resp_desc.text if resp_desc else " ".join(p["notes"])

            # Sintetizza consigli
            if p.get("tips"):
                tips_text = "\n".join(f"- {t}" for t in p["tips"])
                prompt_tips = f"""Sintetizza questi consigli pratici in UN unico paragrafo discorsivo coerente:
{tips_text}

Scrivi come un paragrafo unico e naturale, mantenendo tutti i consigli importanti."""

                resp_tips = model.generate_content(prompt_tips)
                p["_tips_synth"] = resp_tips.text if resp_tips else " ".join(p["tips"])

            p["_notes_synth_version"] = CULTURE_SYNTH_PROMPT_VERSION
            time.sleep(PAUSE_SECONDS)

        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                print(f"  Quota Gemini esaurita, mi fermo qui")
                return
            else:
                print(f"  Errore per {p.get('name', '?')}: {e}, continuo...")
                continue


def process_file(src: Path, out_dir=None, use_llm: bool = True, tips_only: bool = False) -> dict:
    """Elabora un txt. Ritorna un riepilogo: quota_hit, places, new_places, suggestions
    (per i bot di soli consigli: quota_hit, tips, new_tips)."""
    summary = {"quota_hit": False, "places": 0, "new_places": [], "suggestions": [], "merged": [],
               "tips": 0, "new_tips": []}
    out_dir = out_dir or output_dir(src)
    reels = parse_reels(src)
    print(f"Reel trovati: {len(reels)}")
    if not reels:
        return summary
    out_dir.mkdir(parents=True, exist_ok=True)
    if tips_only:
        return process_tips_file(src, out_dir, reels, use_llm, summary)

    country_hint = src.stem.replace("_bot", "").replace("&", " e ").replace("_", " ")

    cache, quota_hit = run_extraction(reels, out_dir / "extraction_cache.json", country_hint, use_llm)
    aliases = load_aliases(out_dir / "aliases.json")

    places, suggestions = group_places(reels, cache, aliases)
    dups = resolve_duplicates(places, suggestions, out_dir, use_llm and not quota_hit)
    if dups["merged"]:
        places, suggestions = group_places(reels, cache, load_aliases(out_dir / "aliases.json"))
    suggestions = dups["open"]   # quelle giudicate diverse non vengono più riproposte
    quota_hit = quota_hit or dups["quota_hit"]

    # Sintetizza i luoghi culturali con Gemini
    try:
        synthesize_cultural_place_text(places, use_llm and not quota_hit)
    except QuotaExhausted:
        quota_hit = True

    places_path = out_dir / "places.json"
    old_ids = {p["id"] for p in json.loads(places_path.read_text(encoding="utf-8"))} if places_path.exists() else set()
    preserve_state(places, places_path)

    (out_dir / "places.json").write_text(json.dumps(places, ensure_ascii=False, indent=2), encoding="utf-8")
    tips = collect_tips(reels, cache)
    write_markdown(places, tips, out_dir / "places.md")
    write_culture(places, tips, out_dir)
    write_image_texts(src, out_dir)  # genera <Nazione>_testo_immagini.txt in FONTI/
    (out_dir / "tips.json").write_text(json.dumps(tips, ensure_ascii=False, indent=2), encoding="utf-8")

    multi = [p for p in places if p["mention_count"] > 1]
    print(f"\nLuoghi unici: {len(places)} | citati in più reel: {len(multi)}")
    for p in sorted(multi, key=lambda p: -p["mention_count"])[:10]:
        print(f"  ×{p['mention_count']}  {p['name']} ({p['city']})")

    if dups["merged"]:
        print("\nDoppioni uniti automaticamente (per annullare: togli la riga da aliases.json):")
        for drop, keep in dups["merged"]:
            print(f"  - {drop}  ->  {keep}")
    if suggestions:
        print("\nPossibili duplicati ancora da controllare (li giudica Gemini al prossimo giro):")
        for a, b in suggestions:
            print(f"  - {a}  <->  {b}")

    print(f"\nFile salvati in: {out_dir}")
    summary.update(quota_hit=quota_hit, places=len(places), suggestions=suggestions, merged=dups["merged"],
                   new_places=[p["name"] for p in places if p["id"] not in old_ids])
    return summary


if __name__ == "__main__":
    main()
