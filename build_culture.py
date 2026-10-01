"""Raggruppa per ARGOMENTO (non per città/luogo) le informazioni culturali già estratte dai reel:
consigli specifici dei luoghi, criticità ("cose da sapere") e consigli generali. Stessa fonte dati di
write_culture() in build_places.py (places.json + tips.json), nessuna informazione da internet: solo
riorganizzazione con Gemini del testo già inviato dall'utente via Telegram.

Output in BOT_OUTPUT/<nome>_bot_output/:
  culture_topics.json  -> [{"topic","text","reels","mention_count"}]
  culture_topics.md    -> vista leggibile, raggruppata per argomento

Uso: python build_culture.py <percorso places.json o txt del bot> | --all
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from build_places import PAUSE_SECONDS, QuotaExhausted, call_gemini_json, output_dir, txt_files_from_bots_config

CULTURE_PROMPT_VERSION = "culture-3"
BATCH_SIZE = 80   # voci per chiamata Gemini, per non rischiare risposte troncate

CULTURE_TOPICS = ["cibo e bevande", "vestiario e rispetto nei luoghi sacri", "religione e tradizioni",
                  "festività ed eventi", "lingua e comunicazione", "sicurezza e truffe", "trasporti",
                  "soldi e contrattazione", "mentalità e usi locali", "altro"]

CULTURE_PROMPT = f"""Ti fornisco consigli di viaggio e criticità scritti da creator, ognuno etichettato con
un codice tra parentesi quadre. Organizzali per argomento.
Rispondi SOLO con JSON valido, senza testo extra né backtick, con questo schema:
{{"items": [{{"topic": "...", "text": "...", "refs": ["codice1", "codice2"]}}]}}
Regole:
- "topic": uno tra {", ".join(CULTURE_TOPICS)}.
- "text": in italiano, max 25 parole. Se più voci dicono la stessa cosa con parole diverse, unendole in
  UN SOLO elemento con una frase sintetica senza ripetizioni (metti tutti i loro codici in "refs").
  Se dicono cose diverse anche sullo stesso argomento, resta in elementi separati.
- "refs": TUTTI i codici delle voci originali riassunte in questo elemento.
- Non inventare nulla che non sia già scritto nelle voci.
- SCARTA (non includere nel risultato) le voci puramente logistiche legate a un singolo luogo senza alcun
  valore culturale/di curiosità (orari, prezzi, come arrivarci, cosa ordinare, affollamento): quelle restano
  nella scheda del luogo, non servono in questa vista. Tieni invece usi, tradizioni, galateo, comportamenti,
  credenze, truffe/sicurezza culturalmente rilevanti (es. contrattazione, mance, gesti da evitare)."""


def _culture_blocks(places: list, tips: list) -> list:
    """Un blocco per ogni consiglio/criticità, a livello di singola mention (reel preciso), più i
    consigli generali. Lavorare sulle mention (non su p["tips"]/p["cons"] già aggregati) mantiene
    l'esatta corrispondenza voce -> reel anche per i luoghi citati da più reel."""
    blocks = []
    for p in places:
        country = p.get("country", "")
        for m in p.get("mentions", []):
            for t in m.get("tips") or []:
                if t.strip():
                    blocks.append({"text": t.strip(), "reel": m.get("reel", ""), "country": country})
            if m.get("cons"):
                blocks.append({"text": m["cons"].strip(), "reel": m.get("reel", ""), "country": country})
    for t in tips:
        if (t.get("tip") or "").strip():
            blocks.append({"text": t["tip"].strip(), "reel": t.get("reel", ""), "country": t.get("country", "")})
    return blocks


def _source_hash(blocks: list) -> str:
    payload = json.dumps([(b["text"], b["reel"], b["country"]) for b in blocks], ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _call_gemini(client, types, batch: list) -> list:
    payload = "\n".join(f"[{i}] {b['text']}" for i, b in enumerate(batch))
    data = call_gemini_json(client, types, CULTURE_PROMPT, payload)
    return data.get("items") or []


def extract_culture_topics(places: list, tips: list, out_dir: Path, use_llm: bool = True) -> tuple:
    """Una richiesta Gemini per lotto di 80 voci, solo se il contenuto è cambiato
    (cache in culture_topics_cache.json). I duplicati vengono uniti solo dentro lo stesso lotto:
    stessa semplificazione di build_dishes.py (i casi tra lotti diversi restano separati).
    Ritorna (items, quota_hit): se la quota giornaliera finisce, si ferma subito (si riprova tutto
    al prossimo giro, la cache non viene salvata) e le nazioni successive in --all saltano Gemini."""
    blocks = _culture_blocks(places, tips)
    cache_path = out_dir / "culture_topics_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    h = _source_hash(blocks)
    if cache.get("_h") == h and cache.get("_v") == CULTURE_PROMPT_VERSION:
        return cache.get("items", []), False
    if not blocks:
        return [], False
    if not use_llm or not os.environ.get("GEMINI_API_KEY"):
        print("⚠️ Niente GEMINI_API_KEY o quota esaurita: salto il raggruppamento, resta la cache precedente.")
        return cache.get("items", []), True

    from google import genai
    from google.genai import types
    client = genai.Client()

    merged = {}  # (topic, text) -> {"reels": set}
    any_batch_failed, quota_hit = False, False
    n_batches = (len(blocks) - 1) // BATCH_SIZE + 1
    for start in range(0, len(blocks), BATCH_SIZE):
        batch = blocks[start:start + BATCH_SIZE]
        print(f"Cultura: lotto {start // BATCH_SIZE + 1}/{n_batches} ({len(batch)} voci)...")
        try:
            items = _call_gemini(client, types, batch)
        except QuotaExhausted:
            print("⛔ Quota giornaliera Gemini esaurita: mi fermo qui, riprovo al prossimo giro.")
            any_batch_failed = quota_hit = True
            break
        except Exception as e:
            print(f"⚠️ Raggruppamento cultura non riuscito per questo lotto: {str(e)[:200]}")
            any_batch_failed = True
            continue
        time.sleep(PAUSE_SECONDS)
        for it in items:
            topic = it.get("topic") if it.get("topic") in CULTURE_TOPICS else "altro"
            text = (it.get("text") or "").strip()
            if not text:
                continue
            reels, countries = set(), set()
            for code in it.get("refs") or []:
                try:
                    idx = int(code)
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(batch):
                    if batch[idx]["reel"]:
                        reels.add(batch[idx]["reel"])
                    if batch[idx]["country"]:
                        countries.add(batch[idx]["country"])
            key = (topic, text)
            entry = merged.setdefault(key, {"reels": set(), "countries": set()})
            entry["reels"] |= reels
            entry["countries"] |= countries

    results = [{"topic": topic, "text": text, "reels": sorted(v["reels"]),
               "countries": sorted(v["countries"]), "mention_count": len(v["reels"])}
              for (topic, text), v in merged.items()]
    results.sort(key=lambda it: (CULTURE_TOPICS.index(it["topic"]), -it["mention_count"]))

    if any_batch_failed:
        print("⚠️ Almeno un lotto è fallito: risultato parziale, cache NON salvata (si riprova tutto al prossimo giro).")
    else:
        cache_path.write_text(json.dumps({"_h": h, "_v": CULTURE_PROMPT_VERSION, "items": results},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
    return results, quota_hit


def write_culture_topics_markdown(items: list, path: Path):
    lines = [f"# Cultura e consigli per argomento ({len(items)} voci)\n"]
    cur = None
    for it in items:
        if it["topic"] != cur:
            cur = it["topic"]
            lines.append(f"\n## {cur.capitalize()}")
        star = f" ×{it['mention_count']}" if it["mention_count"] > 1 else ""
        lines.append(f"- {it['text']}{star}")
        if it["reels"]:
            lines.append("  - reel: " + ", ".join(it["reels"]))
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def process_file(places_json: Path, use_llm: bool = True) -> dict:
    out_dir = places_json.parent
    places = json.loads(places_json.read_text(encoding="utf-8"))
    tips_path = out_dir / "tips.json"
    tips = json.loads(tips_path.read_text(encoding="utf-8")) if tips_path.exists() else []
    items, quota_hit = extract_culture_topics(places, tips, out_dir, use_llm)
    (out_dir / "culture_topics.json").write_text(json.dumps(items, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")
    write_culture_topics_markdown(items, out_dir / "culture_topics.md")
    print(f"-> {len(items)} voci in {out_dir / 'culture_topics.json'}")
    return {"items": items, "quota_hit": quota_hit}


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
        use_llm = not process_file(places_json, use_llm)["quota_hit"] and use_llm


if __name__ == "__main__":
    main()
