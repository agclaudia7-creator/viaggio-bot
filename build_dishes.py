"""Estrae i piatti/bevande tipici citati nei reel, a partire dai luoghi già salvati in places.json
(categoria "cibo"): niente nuove chiamate RapidAPI, niente ricerche su internet per il TESTO (nome,
descrizione, luoghi), solo riorganizzazione con Gemini del testo già inviato dall'utente via Telegram
(note/consigli/contro di ogni mention). Unica eccezione: una miniatura da Wikimedia Commons SOLO per
l'aspetto visivo (eccezione voluta dall'utente, nessun fatto/testo preso da lì), vedi attach_thumbnails.

Output in BOT_OUTPUT/<nome>_bot_output/ (stessa cartella di places.json):
  dishes.json   -> [{"id","name","aliases","description","places":[...],"reels":[...],
                     "mention_count","thumbnail_url"}]
  dishes.md     -> vista leggibile

Uso: python build_dishes.py <percorso places.json o txt del bot> | --all
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from build_maps import USER_AGENT
from build_places import PAUSE_SECONDS, QuotaExhausted, call_gemini_json, norm, output_dir, slug, \
    txt_files_from_bots_config

DISH_PROMPT_VERSION = "dish-3"
BATCH_SIZE = 50   # luoghi di cibo per chiamata Gemini, per non rischiare risposte troncate

DISH_PROMPT = """Ti fornisco note scritte da creator di viaggio su ristoranti/mercati/street food in una
nazione del Sud-Est asiatico, ognuna etichettata con un codice tra parentesi quadre.
Estrai SOLO i piatti o bevande tipici nominati esplicitamente nel testo: non inventare nulla che non ci sia.
Rispondi SOLO con JSON valido, senza testo extra né backtick, con questo schema:
{"dishes": [{"name": "...", "aliases": ["..."], "description": "...", "refs": ["codice1", "codice2"]}]}
Regole:
- "name": nome del piatto/bevanda come scritto nelle note (lingua originale va bene, es. "Pad Thai", "Tom Yum",
  "Cendol"). Un piatto per elemento: non unire piatti diversi anche se nello stesso luogo.
- "aliases": varianti ortografiche/di traduzione dello stesso piatto trovate nel testo (es. "Phad Thai"),
  [] se nessuna.
- "description": max 20 parole in italiano, SOLO da quello che dicono le note (cos'è, com'è, perché provarlo).
  Se più note dicono la stessa cosa con parole diverse, scrivi una sola descrizione sintetica senza ripetizioni.
- "refs": TUTTI i codici dei blocchi che nominano questo piatto, anche con parole diverse.
- Ignora piatti/bevande generici non nominati per nome (es. "street food", "dessert" da soli non contano).
- Ignora anche cibi/bevande comuni ovunque nel mondo (caffè semplice, uova, toast, latte, pane) a meno che
  il testo non li descriva con un nome o una preparazione tipica locale (es. "caffè thai boran", "oliang").
- Se non ci sono piatti nominati, "dishes": []."""


def _food_blocks(places: list) -> list:
    """Un blocco per ogni mention di un luogo di categoria "cibo", con un codice incrementale.
    Ogni blocco porta già il place/reel associato, per non far inventare a Gemini le fonti."""
    blocks = []
    for p in places:
        if p.get("category") != "cibo":
            continue
        for m in p.get("mentions", []):
            parts = []
            if m.get("note"):
                parts.append(m["note"])
            if m.get("tips"):
                parts.append("Consigli: " + "; ".join(m["tips"]))
            if m.get("cons"):
                parts.append("Contro: " + m["cons"])
            text = " ".join(parts).strip()
            if not text:
                continue
            blocks.append({"place": p, "reel": m.get("reel", ""), "text": text})
    return blocks


def _source_hash(blocks: list) -> str:
    payload = json.dumps([(b["place"]["id"], b["reel"], b["text"]) for b in blocks], ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _call_gemini(client, types, blocks_batch: list) -> list:
    payload = "\n".join(f"[{i}] Luogo: {b['place']['name']} ({b['place']['city']}). {b['text']}"
                        for i, b in enumerate(blocks_batch))
    data = call_gemini_json(client, types, DISH_PROMPT, payload)
    return data.get("dishes") or []


def _merge_dish(found: dict, name: str, aliases: list, description: str, refs: list):
    """Unisce per nome normalizzato (anche tra gli alias): stesso criterio semplice dentro un run,
    i casi dubbi tra run diversi restano separati (si possono unire a mano più avanti se serve)."""
    keys = {norm(name)} | {norm(a) for a in aliases if a}
    target = None
    for k in keys:
        if k in found:
            target = found[k]
            break
    if target is None:
        target = {"name": name, "aliases": set(aliases), "description": description, "refs": set()}
    else:
        target["aliases"] |= set(aliases)
        if len(description) > len(target["description"]):   # tiene la descrizione più informativa
            target["description"] = description
    target["refs"] |= set(refs)
    for k in keys | {norm(target["name"])}:
        found[k] = target


def extract_dishes(places: list, out_dir: Path, use_llm: bool = True) -> tuple:
    """Una sola richiesta Gemini per lotto di 50 luoghi di cibo, solo se il contenuto è cambiato
    (cache in dishes_cache.json). Nessuna informazione da internet: solo le note già salvate dal bot.
    Ritorna (dishes, quota_hit): se la quota giornaliera finisce, i lotti restanti si fermano subito
    (si riprova tutto al prossimo giro, la cache non viene salvata) e le nazioni successive in --all
    saltano Gemini."""
    blocks = _food_blocks(places)
    cache_path = out_dir / "dishes_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    h = _source_hash(blocks)
    if cache.get("_h") == h and cache.get("_v") == DISH_PROMPT_VERSION:
        return cache.get("dishes", []), False
    if not blocks:
        return [], False
    if not use_llm or not os.environ.get("GEMINI_API_KEY"):
        print("⚠️ Niente GEMINI_API_KEY o quota esaurita: salto l'estrazione piatti, resta la cache precedente.")
        return cache.get("dishes", []), True

    from google import genai
    from google.genai import types
    client = genai.Client()

    found = {}
    any_batch_failed, quota_hit = False, False
    n_batches = (len(blocks) - 1) // BATCH_SIZE + 1
    for start in range(0, len(blocks), BATCH_SIZE):
        batch = blocks[start:start + BATCH_SIZE]
        print(f"Piatti: lotto {start // BATCH_SIZE + 1}/{n_batches} ({len(batch)} note)...")
        try:
            dishes = _call_gemini(client, types, batch)
        except QuotaExhausted:
            print("⛔ Quota giornaliera Gemini esaurita: mi fermo qui, riprovo al prossimo giro.")
            any_batch_failed = quota_hit = True
            break
        except Exception as e:
            print(f"⚠️ Estrazione piatti non riuscita per questo lotto: {str(e)[:200]}")
            any_batch_failed = True
            continue
        time.sleep(PAUSE_SECONDS)
        for d in dishes:
            name = (d.get("name") or "").strip()
            if not name:
                continue
            aliases = [a.strip() for a in (d.get("aliases") or []) if a.strip()]
            description = (d.get("description") or "").strip()
            refs = []
            for code in d.get("refs") or []:
                try:
                    idx = int(code)
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(batch):
                    refs.append(idx + start)
            if refs:
                _merge_dish(found, name, aliases, description, refs)

    seen_ids, results = set(), []
    for target in {id(v): v for v in found.values()}.values():
        place_ids, reels = {}, set()
        for idx in target["refs"]:
            b = blocks[idx]
            place_ids[b["place"]["id"]] = b["place"]
            if b["reel"]:
                reels.add(b["reel"])
        did = slug(target["name"]) or f"piatto-{len(results)+1}"
        while did in seen_ids:
            did += "-2"
        seen_ids.add(did)
        aliases = sorted(a for a in target["aliases"] if norm(a) != norm(target["name"]))
        results.append({
            "id": did,
            "name": target["name"],
            "aliases": aliases,
            "description": target["description"],
            "places": [{"id": p["id"], "name": p["name"], "city": p.get("city", "")}
                      for p in sorted(place_ids.values(), key=lambda p: p["name"])],
            "countries": sorted({p.get("country", "") for p in place_ids.values() if p.get("country")}),
            "reels": sorted(reels),
            "mention_count": len(reels),
        })
    results.sort(key=lambda d: -d["mention_count"])

    if any_batch_failed:
        print("⚠️ Almeno un lotto è fallito: risultato parziale, cache NON salvata (si riprova tutto al prossimo giro).")
    else:
        cache_path.write_text(json.dumps({"_h": h, "_v": DISH_PROMPT_VERSION, "dishes": results},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
    return results, quota_hit


WIKI_OPENSEARCH = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
OPENVERSE_SEARCH = "https://api.openverse.org/v1/images/"


def _wiki_thumbnail(name: str):
    """Cerca su Wikipedia/Wikimedia Commons una foto del piatto, SOLO per l'aspetto visivo: nessun
    testo/fatto viene letto da qui (eccezione concordata con l'utente alla regola "niente internet",
    valida solo per questa miniatura).
    Ritorna "" se la pagina esiste ma non ha trovato nulla (da mettere in cache, non cambierà),
    None se la richiesta è fallita per un errore di rete/rate limit (da NON mettere in cache,
    altrimenti un 429 temporaneo marcherebbe il piatto come "senza foto" per sempre — bug visto e
    corretto: la prima run su 180 piatti aveva quasi tutto vuoto per throttling di Wikipedia)."""
    try:
        r = requests.get(WIKI_OPENSEARCH, params={"action": "opensearch", "search": name, "limit": 1,
                                                   "namespace": 0, "format": "json"},
                         headers={"User-Agent": USER_AGENT}, timeout=8)
        if r.status_code != 200:
            return None
        titles = r.json()[1]
        if not titles:
            return ""
        r2 = requests.get(WIKI_SUMMARY.format(quote(titles[0])),
                          headers={"User-Agent": USER_AGENT}, timeout=8)
        if r2.status_code == 404:
            return ""
        if r2.status_code != 200:
            return None
        return (r2.json().get("thumbnail") or {}).get("source", "") or ""
    except requests.RequestException:
        return None


def _openverse_thumbnail(name: str):
    """Fallback quando Wikipedia non trova nulla: Openverse aggrega foto a licenza libera (Wikimedia
    Commons, Flickr Commons, ecc.), gratis e senza chiave API. Stesso contratto di _wiki_thumbnail
    ("" = non trovata, None = errore/rate limit, da non cachare). Limite account anonimo: 20
    richieste/min, 200/giorno — usato solo come fallback (non per ogni piatto), raramente ci si
    arriva anche su una nazione piena di piatti."""
    try:
        r = requests.get(OPENVERSE_SEARCH, params={"q": name, "page_size": 1},
                         headers={"User-Agent": USER_AGENT}, timeout=8)
        if r.status_code == 429:
            return None
        if r.status_code != 200:
            return None
        results = r.json().get("results") or []
        return (results[0].get("thumbnail") or "") if results else ""
    except requests.RequestException:
        return None


def attach_thumbnails(dishes: list, out_dir: Path) -> None:
    """Aggiunge d["thumbnail_url"]: prima Wikimedia Commons, poi Openverse come fallback se Wikipedia
    non trova nulla. Cache condivisa tra tutte le nazioni (BOT_OUTPUT/dish_thumbnails_cache.json, non
    per bot) perché molti piatti si ripetono (es. satay sia in Thailandia che in Singapore&Malesia):
    una ricerca sola per nome, mai ripetuta (gli errori non vengono cachati, si ritentano al giro
    dopo — vedi _wiki_thumbnail/_openverse_thumbnail)."""
    cache_path = out_dir.parent / "dish_thumbnails_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    changed = False
    for d in dishes:
        key = norm(d["name"])
        if key in cache:
            d["thumbnail_url"] = cache[key]
            continue
        url = _wiki_thumbnail(d["name"])
        time.sleep(2)   # cortesia verso l'API pubblica di Wikipedia: con 1s si rischia ancora il
                        # throttling su molte richieste di fila (visto su ~180 piatti in un run)
        if url is None:
            d["thumbnail_url"] = ""   # errore temporaneo: niente foto per ora, non è definitivo
            continue
        if not url:   # Wikipedia non ha trovato nulla: provo Openverse come fallback
            url = _openverse_thumbnail(d["name"])
            time.sleep(1.5)   # resta ben sotto i 20/min di Openverse per account anonimi
            if url is None:
                d["thumbnail_url"] = ""   # errore temporaneo anche qui: non è definitivo
                continue
        cache[key] = url
        changed = True
        d["thumbnail_url"] = url
    if changed:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def write_dishes_markdown(dishes: list, path: Path):
    lines = [f"# Piatti e bevande tipici ({len(dishes)})\n"]
    for d in dishes:
        star = f" ×{d['mention_count']}" if d["mention_count"] > 1 else ""
        alias = f" ({', '.join(d['aliases'])})" if d["aliases"] else ""
        lines.append(f"- **{d['name']}**{alias}{star}")
        if d["description"]:
            lines.append(f"  - {d['description']}")
        if d["places"]:
            lines.append("  - dove: " + ", ".join(f"{p['name']} ({p['city']})" if p["city"] else p["name"]
                                                   for p in d["places"]))
        lines.append("  - reel: " + ", ".join(d["reels"]))
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def process_file(places_json: Path, use_llm: bool = True) -> dict:
    out_dir = places_json.parent
    places = json.loads(places_json.read_text(encoding="utf-8"))
    dishes, quota_hit = extract_dishes(places, out_dir, use_llm)
    attach_thumbnails(dishes, out_dir)
    (out_dir / "dishes.json").write_text(json.dumps(dishes, ensure_ascii=False, indent=2), encoding="utf-8")
    write_dishes_markdown(dishes, out_dir / "dishes.md")
    print(f"-> {len(dishes)} piatti in {out_dir / 'dishes.json'}")
    return {"dishes": dishes, "quota_hit": quota_hit}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="places.json o txt del bot")
    ap.add_argument("--all", action="store_true", help="tutte le nazioni di bots_config.json")
    args = ap.parse_args()

    if args.all:
        txts = txt_files_from_bots_config()
    elif args.path:
        txts = [Path(args.path)]
    else:
        ap.error("indica un file (places.json o txt del bot) oppure --all")

    use_llm = True
    for txt in txts:
        src = Path(txt)
        places_json = src if src.name == "places.json" else output_dir(src) / "places.json"
        if not places_json.exists():
            print(f"places.json non trovato, salto: {places_json}")
            continue
        print(f"\n===== {places_json.parent.name} =====")
        # dopo la quota esaurita si rigenera solo dalla cache per le nazioni successive
        use_llm = not process_file(places_json, use_llm)["quota_hit"] and use_llm


if __name__ == "__main__":
    main()
