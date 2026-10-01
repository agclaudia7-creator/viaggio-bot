"""
build_maps.py
Legge i places.json di tutte le nazioni (cartelle *_output dei txt in bots_config.json),
trova le coordinate dei luoghi e crea, per ogni nazione, fino a 5 CSV divisi per numero di citazioni
(da usare come layer di una mappa MyMaps a parte per quella nazione: max 10 layer/mappa, 2000 righe/layer).

Uso:
  python build_maps.py

Output in MyMaps/<Nazione>/ (cartella fratella di BOT_OUTPUT/):
  <Nazione>_bassissima/bassa/media/alta/altissima.csv (`split_citation_tiers`, `tier_count`)
    -> bassissima = luoghi citati da una sola fonte (×1, tipicamente la maggioranza): separata a parte
       perché da sola schiaccerebbe qualunque soglia sul resto. I restanti (×2+) si dividono con lo
       stesso ragionamento ma sul loro proprio minimo/massimo: soglie in scala LOGARITMICA (non
       lineari, non percentili, non calibrate per ottenere gruppi di uguale numero: l'importanza è per
       natura sbilanciata, pochi luoghi eccezionali e tanti normali, ed è corretto che le fasce alte
       siano piccole). Il NUMERO di fasce (1-4) si adatta da solo a quanta variazione c'è tra quei
       luoghi: una fascia in più ogni volta che le citazioni raddoppiano tra min e max, così nazioni
       con poca varietà (es. Laos) non restano con una fascia di mezzo vuota. Livelli vuoti non
       vengono creati. Righe ordinate per categoria, sempre nello stesso ordine (CATEGORY_ORDER) in
       ogni file: serve perché MyMaps assegna i colori in base all'ordine con cui incontra i valori,
       quindi senza un ordine fisso la stessa categoria avrebbe colori diversi da un layer all'altro.
  geocode_cache.json    -> coordinate già trovate (nessuna nuova richiesta ai rilanci), in MyMaps/

Geocoding: Nominatim (OpenStreetMap), gratuito, senza chiave, max 1 richiesta/secondo.
In MyMaps scegli la colonna "Posizione" per posizionare i segnaposto e "Nome" come titolo:
contiene "nome, città, nazione" (lo cerca Google in import). Lat/Lon = coordinate OSM, solo informative.
Per correggere un luogo sbagliato: modifica/cancella la sua voce in geocode_cache.json e rilancia.
"""

import csv
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path

import requests

try:  # senza, i print con emoji crashano (UnicodeEncodeError) sulla codepage cp1252 di default di Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:  # usa i certificati di Windows (necessario dietro firewall aziendali): pip install truststore
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from build_places import (DUP_PROMPT, FUZZY_MERGE, FUZZY_SUGGEST, MODEL, compatible, core_name, norm,
                          output_dir, txt_files_from_bots_config)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "TripPlanner-SudEstAsiatico/1.0 (uso personale)"
PAUSE_SECONDS = 1.1        # limite Nominatim: 1 richiesta al secondo
MAX_KM_FROM_CITY = 60      # un risultato più lontano dalla città è considerato sbagliato

COUNTRY_CODES = {
    "singapore": "sg", "malesia": "my", "thailandia": "th", "laos": "la",
    "indonesia": "id", "cambogia": "kh", "vietnam": "vn", "filippine": "ph",
    "myanmar": "mm", "brunei": "bn", "timor est": "tl",
}
CODE_TO_COUNTRY = {code: name.title() for name, code in COUNTRY_CODES.items()}

CSV_COLUMNS = ["Nome", "Categoria", "Città", "Stato", "Citazioni", "Descrizione", "Posizione", "Lat", "Lon"]

# Stesso ordine ovunque (righe nei CSV, colori MyMaps): categorie usate per i luoghi (places.md/NOTEBOOK_GUIDE).
CATEGORY_ORDER = ["cibo", "natura", "cultura", "nightlife", "shopping", "attivita", "trasporti", "alloggio", "altro"]


def category_rank(category: str) -> int:
    return CATEGORY_ORDER.index(category) if category in CATEGORY_ORDER else len(CATEGORY_ORDER)


TIER_NAMES_BY_COUNT = {
    1: ["bassa"],
    2: ["bassa", "altissima"],
    3: ["bassa", "alta", "altissima"],
    4: ["bassa", "media", "alta", "altissima"],
}


def tier_count(lo: int, hi: int) -> int:
    """Quante fasce (1-4) ha senso usare: una in più ogni volta che le citazioni raddoppiano (in scala
    log) tra il minimo e il massimo di una nazione. Con poca variazione (es. Laos, ×1-×3) si riduce
    da solo a 2 fasce invece di lasciarne una vuota in mezzo (vedi discussione con l'utente)."""
    if hi <= lo:
        return 1
    return 1 + min(3, math.floor(math.log2(hi / lo)))


def split_citation_tiers(rows: list) -> dict:
    """{livello: [row, ...]}. bassissima = citati da una sola fonte (×1, tipicamente la maggioranza):
    separata a parte perché altrimenti da sola schiaccerebbe tutte le soglie (vedi discussione con
    l'utente). I restanti (×2+) si dividono con lo stesso ragionamento, ma partendo dal loro proprio
    minimo/massimo: un numero di fasce (`tier_count`) adattato a quanta variazione hanno, con soglie
    equidistanti in scala LOGARITMICA (non lineare né percentili, non ricalibrate per ottenere gruppi
    di uguale numero — l'importanza è per natura sbilanciata: pochi luoghi eccezionali, tanti normali,
    ed è corretto che le fasce alte siano piccole)."""
    rows = list(rows)
    result = {"bassissima": [row for n, row in rows if n <= 1]}
    rest = [(n, row) for n, row in rows if n > 1]
    if not rest:
        return result

    counts = [n for n, _ in rest]
    lo, hi = min(counts), max(counts)
    names = TIER_NAMES_BY_COUNT[tier_count(lo, hi)]
    n = len(names)
    thresholds = [lo * (hi / lo) ** (i / n) for i in range(1, n)]

    def tier_for(count):
        for name, t in zip(names[:-1], thresholds):
            if count <= t:
                return name
        return names[-1]

    for count, row in rest:
        result.setdefault(tier_for(count), []).append(row)
    return result


# --------------------------------------------------------------------------- geocoding

def km_between(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


class Geocoder:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.requests_done = 0

    def search(self, query: str, country: str):
        """Coordinate (lat, lon) o None. Anche i 'non trovato' vanno in cache."""
        key = f"{norm(query)}|{norm(country)}"
        if key in self.cache:
            hit = self.cache[key]
            return (hit["lat"], hit["lon"]) if hit else None
        params = {"q": query, "format": "jsonv2", "limit": 1}
        code = COUNTRY_CODES.get(norm(country))
        if code:
            params["countrycodes"] = code
        try:
            time.sleep(PAUSE_SECONDS)
            r = self.session.get(NOMINATIM_URL, params=params, timeout=20)
            r.raise_for_status()
            results = r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"   ⚠️ errore geocoding '{query}': {e}")   # non in cache: ritentato al prossimo giro
            return None
        self.requests_done += 1
        hit = {"lat": float(results[0]["lat"]), "lon": float(results[0]["lon"])} if results else None
        self.cache[key] = hit
        return (hit["lat"], hit["lon"]) if hit else None

    def locate(self, place: dict):
        """Ritorna (coordinate, precisione) con precisione 'luogo' / 'città' / ''."""
        name, city, country = place["name"], place.get("city") or "", place.get("country") or ""
        city_pos = self.search(city, country) if city else None
        queries = [", ".join(x for x in (name, city if norm(city) != norm(name) else "", country) if x)]
        if city:
            queries.append(f"{name}, {country}" if country else name)
        for q in queries:
            pos = self.search(q, country)
            if pos and (not city_pos or km_between(pos, city_pos) <= MAX_KM_FROM_CITY):
                return pos, "luogo"
        if city_pos:
            return city_pos, "città"
        return None, ""

    def reverse_country(self, lat: float, lon: float) -> str:
        """Nazione (nome come in COUNTRY_CODES, es. 'Thailandia') dalle coordinate, o '' se fuori dal Sud-Est
        asiatico o non riconosciuta. Usato per i luoghi da blog, che possono citare più nazioni nello stesso post."""
        key = f"rev|{lat:.4f}|{lon:.4f}"
        if key in self.cache:
            return self.cache[key] or ""
        try:
            time.sleep(PAUSE_SECONDS)
            r = self.session.get(NOMINATIM_REVERSE_URL, params={"lat": lat, "lon": lon, "format": "jsonv2"},
                                 timeout=20)
            r.raise_for_status()
            code = (r.json().get("address") or {}).get("country_code", "")
        except (requests.RequestException, ValueError) as e:
            print(f"   ⚠️ errore reverse geocoding ({lat}, {lon}): {e}")
            return ""
        self.requests_done += 1
        country = CODE_TO_COUNTRY.get(code, "")
        self.cache[key] = country
        return country

    def save(self):
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- CSV

DESC_SYNTH_PROMPT = """Ti do luoghi di viaggio con descrizioni scritte da creator diversi (stesso posto,
parole diverse ma spesso lo stesso contenuto) e consigli/criticità che si sovrappongono.
Per ciascun luogo scrivi:
- "descrizione": 1-3 frasi in italiano che riassumono le informazioni SENZA ripetere più volte lo stesso
  concetto con parole diverse, ma senza perdere dettagli presenti in una sola fonte.
- "consigli": lista di consigli pratici, uniti quelli che dicono la stessa cosa, senza perdere consigli diversi.
- "contro": lista di criticità/avvertenze, stesso criterio.
Non inventare nulla che non sia già scritto nelle fonti. Rispondi SOLO con JSON:
{"results": [{"id": 1, "descrizione": "...", "consigli": ["..."], "contro": ["..."]}, ...]} con tutti gli id."""


def _needs_synthesis(p: dict) -> bool:
    """Un luogo citato da più fonti ha quasi sempre descrizioni/consigli ridondanti (stesso contenuto,
    parole diverse): va sintetizzato con Gemini invece di accodare tutto (vedi feedback utente)."""
    return len(p.get("notes") or []) > 1 or len(p.get("tips") or []) > 3 or len(p.get("cons") or []) > 3


def _synth_hash(p: dict) -> str:
    payload = json.dumps([p.get("notes") or [], p.get("tips") or [], p.get("cons") or []], ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def synthesize_descriptions(places: list, out_dir: Path, use_llm: bool = True) -> None:
    """Fa riscrivere a Gemini (una richiesta sola per run) le descrizioni/consigli/contro dei luoghi con
    più fonti, eliminando le ripetizioni. Risultato in p["_desc_synth"]/p["_tips_synth"]/p["_cons_synth"]
    (solo per il CSV della mappa, places.json non viene toccato). Cache in description_cache.json:
    richiamato di nuovo solo se note/consigli/contro del luogo cambiano."""
    cache_path = out_dir / "description_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    todo = []
    for p in places:
        if not _needs_synthesis(p):
            continue
        h = _synth_hash(p)
        hit = cache.get(p["id"])
        if hit and hit.get("_h") == h:
            p["_desc_synth"] = hit.get("descrizione", "")
            p["_tips_synth"] = hit.get("consigli", [])
            p["_cons_synth"] = hit.get("contro", [])
        else:
            todo.append((p, h))

    if not todo or not use_llm or not os.environ.get("GEMINI_API_KEY"):
        return

    from google import genai
    from google.genai import types
    client = genai.Client()
    payload = json.dumps([{"id": i, "nome": p["name"], "note": p.get("notes") or [],
                           "consigli": p.get("tips") or [], "contro": p.get("cons") or []}
                          for i, (p, _) in enumerate(todo, 1)], ensure_ascii=False)
    try:
        resp = client.models.generate_content(
            model=MODEL, contents=payload,
            config=types.GenerateContentConfig(system_instruction=DESC_SYNTH_PROMPT,
                                               response_mime_type="application/json", temperature=0))
        answers = json.loads(re.search(r"\{.*\}", resp.text or "", re.S).group(0))["results"]
        by_id = {int(r["id"]): r for r in answers if isinstance(r, dict) and "id" in r}
    except Exception as e:
        print(f"⚠️ Sintesi descrizioni non riuscita, riprovo al prossimo giro: {str(e)[:200]}")
        return

    for i, (p, h) in enumerate(todo, 1):
        r = by_id.get(i)
        if not r:
            continue
        descrizione, consigli, contro = r.get("descrizione") or "", r.get("consigli") or [], r.get("contro") or []
        p["_desc_synth"], p["_tips_synth"], p["_cons_synth"] = descrizione, consigli, contro
        cache[p["id"]] = {"_h": h, "descrizione": descrizione, "consigli": consigli, "contro": contro}
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def description(p: dict) -> str:
    lines = []
    if p.get("costs"):
        lines.append("💰 " + ", ".join(p["costs"]))
    if p.get("creator_ratings"):
        lines.append("⭐ voto creator: " + ", ".join(p["creator_ratings"]))
    if p.get("days"):
        lines.append("📅 " + ", ".join(p["days"]))
    if "_desc_synth" in p:   # già sintetizzato da Gemini, senza ripetizioni (synthesize_descriptions)
        if p["_desc_synth"]:
            lines.append(p["_desc_synth"])
        lines += ["💡 " + t for t in p.get("_tips_synth") or []]
        lines += ["⚠️ " + c for c in p.get("_cons_synth") or []]
    else:
        lines += p.get("notes", [])  # note già deduplicate in build_places.py
        lines += ["💡 " + t for t in p.get("tips", [])]
        lines += ["⚠️ " + c for c in p.get("cons", [])]
    lines += [m["reel"] for m in p.get("mentions", [])]
    return "\n".join(lines)


def collect_places() -> list:
    """Luoghi di tutte le cartelle *_output; se lo stesso id compare in più file i dati si uniscono
    (`_merge_place`, non si scarta più il meno citato: aveva comunque tips/note proprie, vedi feedback
    utente) tenendo come principale quello più citato. Aggiunge anche i luoghi da blog (blog_places.json,
    vedi blog_import.py), con coordinate già note."""
    by_id = {}

    def add(pid, p):
        old = by_id.get(pid)
        if not old:
            by_id[pid] = p
        elif p.get("mention_count", 0) > old.get("mention_count", 0):
            _merge_place(p, old)
            by_id[pid] = p
        else:
            _merge_place(old, p)

    for txt in txt_files_from_bots_config():
        out_dir = output_dir(txt)
        path = out_dir / "places.json"
        if path.exists():
            for p in json.loads(path.read_text(encoding="utf-8")):
                add(p["id"], p)

        blog_path = out_dir / "blog_places.json"
        if blog_path.exists():
            for b in json.loads(blog_path.read_text(encoding="utf-8")):
                pid = "blog_" + norm(b["name"]).replace(" ", "_")
                add(pid, {
                    "id": pid, "name": b["name"], "city": "", "country": b["country"],
                    "category": b.get("category") or "altro",
                    "costs": [], "creator_ratings": [], "days": [], "cons": [], "tips": [],
                    "notes": [f"🔗 Maps: {b['url']}"],
                    "status": "da_fare", "mention_count": 1, "mentions": [{"reel": b["source_url"]}],
                    "_lat": b["lat"], "_lon": b["lon"],
                })
    return list(by_id.values())


def _merge_place(keep: dict, drop: dict) -> None:
    """Sposta in keep i dati di drop (keep resta l'id/categoria/coordinate "ufficiali", scelto in
    merge_cross_source_duplicates come quello con più citazioni): stesso luogo arrivato da fonti diverse
    (reel + blog, o due blog). I testi uniti finiscono poi a Gemini via synthesize_descriptions, che li
    riscrive senza ripetizioni."""
    def uniq(a, b):
        return list(dict.fromkeys((a or []) + (b or [])))
    keep["costs"] = sorted(set((keep.get("costs") or []) + (drop.get("costs") or [])))
    keep["creator_ratings"] = uniq(keep.get("creator_ratings"), drop.get("creator_ratings"))
    keep["days"] = uniq(keep.get("days"), drop.get("days"))
    keep["notes"] = uniq(keep.get("notes"), drop.get("notes"))
    keep["tips"] = uniq(keep.get("tips"), drop.get("tips"))
    keep["cons"] = uniq(keep.get("cons"), drop.get("cons"))
    seen_reels = {m.get("reel") for m in keep.get("mentions") or []}
    keep["mentions"] = (keep.get("mentions") or []) + [m for m in (drop.get("mentions") or [])
                                                        if m.get("reel") not in seen_reels]
    keep["mention_count"] = len(keep["mentions"])
    if "_lat" in drop and "_lat" not in keep:
        keep["_lat"], keep["_lon"] = drop["_lat"], drop["_lon"]


def merge_cross_source_duplicates(places: list, out_dir: Path, use_llm: bool = True) -> list:
    """places.json (reel) e blog_places.json (blog_import.py) assegnano id separati (collect_places):
    lo stesso luogo nominato in modo diverso dalle due fonti (es. "Batu Caves" dai reel vs "Batu cave" da
    un blog, categorie pure diverse) non viene mai confrontato a monte, quindi resta duplicato nel CSV
    (bug visto e corretto, vedi feedback utente). Qui si confrontano TUTTI i luoghi con lo stesso fuzzy
    match di build_places.group_places: sopra FUZZY_MERGE si uniscono subito, tra FUZZY_SUGGEST e
    FUZZY_MERGE lo giudica Gemini una volta sola (cache in cross_duplicate_checks.json, mai richiesto di
    nuovo). Vince (resta come id/categoria) il luogo con più citazioni. Non tocca i file originali: solo
    i dati usati per generare il CSV."""
    checks_path = out_dir / "cross_duplicate_checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8")) if checks_path.exists() else {}

    by_id = {p["id"]: p for p in places}
    names = {p["id"]: core_name(p["name"], p.get("city") or "", p.get("country") or "") for p in places}
    parent = {pid: pid for pid in by_id}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    todo = []
    for i, a in enumerate(places):
        for b in places[i + 1:]:
            if not (compatible(a.get("country"), b.get("country")) and compatible(a.get("city"), b.get("city"))):
                continue
            na, nb = names[a["id"]], names[b["id"]]
            score = 1.0 if na == nb else difflib.SequenceMatcher(None, na, nb).ratio()
            if score >= FUZZY_MERGE:
                union(a["id"], b["id"])
            elif score >= FUZZY_SUGGEST:
                key = "|".join(sorted((a["id"], b["id"])))
                if key in checks:
                    if checks[key]:
                        union(a["id"], b["id"])
                else:
                    todo.append((a, b, key))

    if todo and use_llm and os.environ.get("GEMINI_API_KEY"):
        from google import genai
        from google.genai import types
        client = genai.Client()

        def info(p):
            return {"nome": p["name"], "città": p.get("city", ""), "nazione": p.get("country", ""),
                    "descrizione": (p.get("notes") or [""])[0]}

        payload = json.dumps([{"id": i, "coppia": [info(a), info(b)]} for i, (a, b, _) in enumerate(todo, 1)],
                             ensure_ascii=False)
        try:
            resp = client.models.generate_content(
                model=MODEL, contents=payload,
                config=types.GenerateContentConfig(system_instruction=DUP_PROMPT,
                                                   response_mime_type="application/json", temperature=0))
            answers = json.loads(re.search(r"\{.*\}", resp.text or "", re.S).group(0))["results"]
            by_idx = {int(r["id"]): bool(r["same"]) for r in answers if isinstance(r, dict) and "id" in r}
        except Exception as e:
            print(f"⚠️ Controllo doppioni incrociati non riuscito, riprovo al prossimo giro: {str(e)[:200]}")
            by_idx = {}
        for i, (a, b, key) in enumerate(todo, 1):
            if i in by_idx:
                checks[key] = by_idx[i]
                if by_idx[i]:
                    union(a["id"], b["id"])
        checks_path.write_text(json.dumps(checks, ensure_ascii=False, indent=1), encoding="utf-8")

    clusters = {}
    for pid in by_id:
        clusters.setdefault(find(pid), []).append(pid)

    merged = []
    for members in clusters.values():
        if len(members) == 1:
            merged.append(by_id[members[0]])
            continue
        keep_id = max(members, key=lambda pid: (by_id[pid].get("mention_count", 0), not pid.startswith("blog_")))
        keep = by_id[keep_id]
        for pid in members:
            if pid != keep_id:
                _merge_place(keep, by_id[pid])
        merged.append(keep)
    return merged


def mymaps_dir() -> Path:
    """Cartella MyMaps/, fratella di BOT_OUTPUT/ (o accanto alle cartelle *_output nel vecchio schema)."""
    txts = txt_files_from_bots_config()
    bot_out = output_dir(txts[0]).parent
    root = bot_out.parent if bot_out.name == "BOT_OUTPUT" else bot_out
    d = root / "MyMaps"
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_maps() -> dict:
    """Crea i CSV. Ritorna {nazione: {"places": n, "not_found": [nomi]}}."""
    txts = txt_files_from_bots_config()
    if not txts:
        return {}
    out_dir = mymaps_dir()

    places_all = collect_places()
    places_all = merge_cross_source_duplicates(places_all, out_dir)
    synthesize_descriptions(places_all, out_dir)

    geo = Geocoder(out_dir / "geocode_cache.json")
    rows_by_country, summary = {}, {}
    try:
        for p in sorted(places_all, key=lambda p: (p["country"], p["city"], p["name"])):
            country = p.get("country") or "Senza nazione"
            pos, precision = ((p["_lat"], p["_lon"]), "luogo") if "_lat" in p else geo.locate(p)
            s = summary.setdefault(country, {"places": 0, "not_found": []})
            s["places"] += 1
            # Posizione sempre testo: MyMaps tratta la colonna come indirizzo e non riconosce
            # le coordinate mescolate al testo (righe in errore). Lat/Lon OSM restano solo come info
            # (con la sola città sarebbero il centro città: non utili, lasciate vuote).
            exact = precision == "luogo"
            if not exact:
                s["not_found"].append(p["name"])
            mention_count = p.get("mention_count", 1)
            rows_by_country.setdefault(country, []).append((mention_count, {
                "Nome": p["name"],
                "Categoria": p.get("category", ""),
                "Città": p.get("city", ""),
                "Stato": p.get("status", ""),
                "Citazioni": mention_count,
                "Descrizione": description(p),
                "Posizione": ", ".join(x for x in (p["name"],
                                                   p.get("city") if norm(p.get("city") or "") != norm(p["name"]) else "",
                                                   p.get("country")) if x),
                "Lat": f"{pos[0]:.6f}" if exact else "",
                "Lon": f"{pos[1]:.6f}" if exact else "",
            }))
    finally:
        geo.save()   # anche se interrotto, le coordinate trovate non vanno perse

    # CSV piatti del vecchio schema (un file per nazione, senza cartelle) vengono rimossi
    for old in out_dir.glob("*.csv"):
        old.unlink()
        print(f"Rimosso CSV vecchio schema: {old.name}")
    # cartelle di nazioni non più presenti (es. luoghi ora spariti) vengono rimosse
    for old_dir in out_dir.iterdir():
        if old_dir.is_dir() and old_dir.name not in rows_by_country:
            shutil.rmtree(old_dir)
            print(f"Rimossa cartella non più usata: {old_dir.name}")

    for country, rows in rows_by_country.items():
        country_dir = out_dir / country
        country_dir.mkdir(parents=True, exist_ok=True)
        by_tier = split_citation_tiers(rows)

        for tier in ("bassissima", "bassa", "media", "alta", "altissima"):
            path = country_dir / f"{country}_{tier}.csv"
            tier_rows = sorted(by_tier.get(tier, []), key=lambda r: (category_rank(r["Categoria"]), r["Nome"]))
            if not tier_rows:
                path.unlink(missing_ok=True)   # non più luoghi in questo livello: tolgo il csv vecchio
                continue
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                w.writeheader()
                w.writerows(tier_rows[:2000])   # limite MyMaps per layer
            if len(tier_rows) > 2000:
                print(f"⚠️ {country} ({tier}): {len(tier_rows)} luoghi, MyMaps ne importa max 2000 per layer")

    print(f"Richieste di geocoding nuove: {geo.requests_done}")
    for country, s in summary.items():
        print(f"  {country}: {s['places']} luoghi, senza coordinate OSM: {len(s['not_found'])}")
    print(f"CSV salvati in: {out_dir}")
    return summary


if __name__ == "__main__":
    build_maps()
