import asyncio
import os
import sys
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
import requests
import certifi

# Usa i certificati SSL di sistema su Windows
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler, TypeHandler, filters, ContextTypes,
                          ApplicationBuilder, CallbackQueryHandler)
from telegram.request import HTTPXRequest
from telegram.error import NetworkError, TimedOut

import build_places
import build_maps
import build_dishes
import build_culture
import blog_import
import assistant
import publish_pages

# Config con token/chiavi: in locale (~/.viaggio_bot), mai nella cartella del codice che è su Drive
os.makedirs(build_places.CONFIG_DIR, exist_ok=True)
CONFIG_FILE_PATH = str(build_places.CONFIG_DIR / "api_config.json")
BOTS_CONFIG_PATH = str(build_places.BOTS_CONFIG_PATH)

# Aggiornamento automatico di places.json + CSV MyMaps dopo l'ultimo reel ricevuto:
# se arrivano più reel di seguito, parte un solo aggiornamento alla fine.
UPDATE_DELAY_SECONDS = 180
update_lock = asyncio.Lock()     # un solo aggiornamento alla volta (i CSV MyMaps sono comuni a tutti i bot)
pending_updates = {}             # bot_name -> task in attesa
pending_args = {}                # bot_name -> argomenti per eseguire subito l'aggiornamento in pending_updates
tips_bots = set()                # bot con "type": "tips" in bots_config.json: solo consigli, niente mappa

# Uso da cellulare (Termux): 0 = disattivato (default, PC); se >0 il bot si ferma da solo dopo N secondi
# senza messaggi/tocchi su bottoni, per non restare acceso a vuoto e consumare dati/batteria.
IDLE_STOP_SECONDS = int(os.environ.get("IDLE_STOP_SECONDS", "0"))
last_activity = {"t": time.monotonic()}
stop_requested = {"now": False}  # /spegni


async def touch_activity(update, context):
    last_activity["t"] = time.monotonic()


async def handle_stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛑 Finisco gli aggiornamenti in coda e spengo il bot.")
    stop_requested["now"] = True

def load_bots_config():
    if not os.path.exists(BOTS_CONFIG_PATH):
        default_config = {
            "bots": [
                {
                    "name": "Bot Viaggi Principale",
                    "description": "Canale principale per i reels di viaggio",
                    "token": "METTI_QUI_IL_TOKEN_TELEGRAM",
                    "txt_file_path": "G:/Il mio Drive/Viaggi/Aspettativa/BOT_OUTPUT/Esempio_bot_output/Esempio_bot.txt"
                }
            ]
        }
        with open(BOTS_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4)
        return default_config
    
    with open(BOTS_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_api_config():
    if not os.path.exists(CONFIG_FILE_PATH):
        default_config = {
            "api_list": [
                {
                    "host": "instagram-best-experience.p.rapidapi.com",
                    "key": "METTI_QUI_LA_CHIAVE_RAPIDAPI",
                    "max_limit": 100,
                    "used": 0,
                    "endpoint_path": "/post",
                    "param_name": "shortcode"
                }
            ]
        }
        with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4)
        return default_config
    
    with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def format_seconds_to_readable(sec_str):
    try:
        total_seconds = int(sec_str)
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        parts = []
        if days > 0:
            parts.append(f"{days} giorni")
        if hours > 0:
            parts.append(f"{hours} ore")
        if minutes > 0 and days == 0:
            parts.append(f"{minutes} minuti")
        return "tra circa " + " e ".join(parts) if parts else "a breve"
    except ValueError:
        return sec_str

def extract_posted_date(post_data):
    """Data di pubblicazione del reel/post (dalla stessa risposta API, nessuna richiesta in più)."""
    if isinstance(post_data, dict) and "body" in post_data:
        post_data = post_data.get("body", {})
    ts = post_data.get("taken_at") or post_data.get("taken_at_timestamp")
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d") if ts else ""
    except (ValueError, TypeError, OSError):
        return ""


def extract_caption_universal(post_data):
    if isinstance(post_data, dict) and "body" in post_data:
        post_data = post_data.get("body", {})

    # Primo tentativo: caption diretto
    caption_obj = post_data.get("caption")
    if isinstance(caption_obj, str) and caption_obj:
        return caption_obj
    elif isinstance(caption_obj, dict):
        text = caption_obj.get("text")
        if text:
            return text

    # Secondo tentativo: edge_media_to_caption (Instagram Graph format)
    try:
        edges = post_data.get("edge_media_to_caption", {}).get("edges", [])
        if edges and len(edges) > 0:
            node = edges[0].get("node", {})
            text = node.get("text")
            if text:
                return text
    except (AttributeError, TypeError, IndexError):
        pass

    # Terzo tentativo: media.caption (instagram-profile1 format quando post_data è già media)
    if "media" in post_data:
        media = post_data.get("media", {})
        caption = media.get("caption")
        if isinstance(caption, str) and caption:
            return caption

    return None

MAX_POST_IMAGES = 20
IMAGE_TEXT_PROMPT = ("Trascrivi fedelmente tutto il testo presente nelle immagini, nell'ordine, separando le "
                     "immagini con \" / \". Solo il testo, niente commenti né formattazione. "
                     "Se non c'è testo rispondi con una stringa vuota.")


def extract_image_urls(post_data):
    """Link delle immagini del post (tutte quelle del carosello), per i vari formati delle API."""
    if isinstance(post_data, dict) and "body" in post_data:
        post_data = post_data.get("body", {})

    def best(node):
        cands = (node.get("image_versions2") or {}).get("candidates") or []
        url = node.get("display_url") or (cands[0].get("url") if cands else None) \
            or node.get("thumbnail_src") or node.get("thumbnail_url")
        is_video = node.get("is_video") or str(node.get("media_type")) == "2"
        return url or (None if is_video else node.get("media_url"))   # media_url: instagram-social (mp4 se video)

    edges = (post_data.get("edge_sidecar_to_children") or {}).get("edges") or []
    nodes = [e.get("node", {}) for e in edges] or post_data.get("carousel_media") or []
    urls = [u for u in (best(n) for n in nodes if isinstance(n, dict)) if u] or [best(post_data)]
    return list(dict.fromkeys(u for u in urls if u))[:MAX_POST_IMAGES]


def extract_image_text(urls, retry_count=0):
    """Scarica le immagini (i link Instagram scadono dopo qualche ora) e ne fa leggere il testo a Gemini."""
    from google import genai
    from google.genai import types
    parts = []
    failed_urls = []
    for url in urls:
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
        except (requests.exceptions.RequestException, requests.exceptions.HTTPError) as e:
            # URL scaduto (410 Gone, 404 Not Found, timeout) → non carica questa immagine
            print(f"  ⚠️ Immagine non disponibile ({url[:50]}...): {e}")
            failed_urls.append((url[:50], str(e)[:80]))
            continue
        mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
        if mime.startswith("image/"):   # salta eventuali video
            parts.append(types.Part.from_bytes(data=r.content, mime_type=mime))

    # Se TUTTE le immagini falliscono nel download, solleva eccezione tracciabile
    if not parts and failed_urls:
        error_summary = "; ".join([f"{url}... ({err[:40]})" for url, err in failed_urls[:2]])
        raise Exception(f"Tutte le immagini falliscono al download: {error_summary}")

    if not parts:
        return ""
    client = genai.Client()   # tenerlo in una variabile: se viene chiuso, la richiesta fallisce
    resp = client.models.generate_content(model=build_places.MODEL, contents=parts + [IMAGE_TEXT_PROMPT])
    return re.sub(r"\s+", " ", (resp.text or "").replace("**", "")).strip()


VIDEO_TEXT_PROMPT = ("Trascrivi fedelmente solo il testo scritto in sovrimpressione nel video (titoli, scritte, "
                     "cartelli, nomi di luoghi, prezzi), nell'ordine in cui compare, ogni scritta una sola volta, "
                     "separate da \" / \". Ignora il parlato e la musica. Solo il testo, niente commenti né "
                     "formattazione. Se non c'è testo rispondi con una stringa vuota.")
MAX_INLINE_VIDEO_BYTES = 20 * 1024 * 1024   # oltre si carica con la Files API di Gemini


def extract_video_url(post_data):
    """Link del file video del reel (il primo video, anche dentro un carosello), per i vari formati delle API."""
    if isinstance(post_data, dict) and "body" in post_data:
        post_data = post_data.get("body", {})

    def video(node):
        versions = node.get("video_versions") or []
        url = node.get("video_url") or (versions[0].get("url") if versions else None)
        if not url and (str(node.get("media_type")) == "2" or ".mp4" in str(node.get("media_url", ""))):
            url = node.get("media_url")   # instagram-social
        return url

    edges = (post_data.get("edge_sidecar_to_children") or {}).get("edges") or []
    nodes = [post_data] + [e.get("node", {}) for e in edges] + (post_data.get("carousel_media") or [])
    return next((u for u in (video(n) for n in nodes if isinstance(n, dict)) if u), None)


def extract_video_text(url):
    """Scarica il video (il link scade dopo qualche ora) e fa leggere a Gemini solo le scritte a schermo."""
    import tempfile
    import time
    from google import genai
    from google.genai import types
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    client = genai.Client()   # tenerlo in una variabile: se viene chiuso, la richiesta fallisce
    if len(r.content) <= MAX_INLINE_VIDEO_BYTES:
        part = types.Part.from_bytes(data=r.content, mime_type="video/mp4")
    else:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(r.content)
        try:
            part = client.files.upload(file=tmp.name)
            while part.state and part.state.name == "PROCESSING":
                time.sleep(3)
                part = client.files.get(name=part.name)
        finally:
            os.remove(tmp.name)
    resp = client.models.generate_content(model=build_places.MODEL, contents=[part, VIDEO_TEXT_PROMPT])
    return re.sub(r"\s+", " ", (resp.text or "").replace("**", "")).strip()


def saved_block(txt_file_path, short_code):
    """(righe del file, indice riga Link, indice del separatore) del reel già salvato, oppure None."""
    if not os.path.exists(txt_file_path):
        return None
    with open(txt_file_path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")
    for i, line in enumerate(lines):
        if line.startswith("Link:") and re.search(rf"/{re.escape(short_code)}(?:[/?\s]|$)", line):
            end = next((j for j in range(i + 1, len(lines))
                        if re.match(r"-{10,}\s*$", lines[j]) or lines[j].startswith("Link:")), len(lines))
            return lines, i, end
    return None


# Circuit breaker per API che falliscono in continuazione (rete/429/403/errore HTTP): dopo N fallimenti di
# fila va in pausa per un po', per non riprovarla a ogni singolo reel sprecando tempo (e, se il fallimento è
# un errore HTTP diverso da 429/403, comunque un tentativo).
FAIL_STREAK_THRESHOLD = 3
COOLDOWN_SECONDS = 1800

# Se un'API, quando le vengono chieste immagini/video, non le restituisce N volte di fila, viene segnata come
# "non supporta immagini/video": nei messaggi post/video verrà provata solo come ultima risorsa, per non
# sprecare un credito su un'API che si sa già non avere quel dato (vedi CLAUDE.md: alcune API non restituiscono
# le immagini). Continua comunque a essere usata normalmente per la sola caption.
MEDIA_MISS_THRESHOLD = 3


def record_api_result(api_index, success):
    """Aggiorna fail_streak/cooldown_until dopo un tentativo generale (non legato a immagini/video)."""
    config = load_api_config()
    api = config["api_list"][api_index]
    if success:
        api["fail_streak"] = 0
        api["cooldown_until"] = 0
    else:
        api["fail_streak"] = api.get("fail_streak", 0) + 1
        if api["fail_streak"] >= FAIL_STREAK_THRESHOLD:
            api["cooldown_until"] = time.time() + COOLDOWN_SECONDS
    with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)


def record_media_result(api_index, kind, found):
    """kind: 'images' o 'video'. Aggiorna il contatore di "mancate" consecutive e il flag no_<kind>."""
    config = load_api_config()
    api = config["api_list"][api_index]
    streak_key, flag_key = f"{kind}_miss_streak", f"no_{kind}"
    if found:
        api[streak_key] = 0
        api[flag_key] = False   # ha funzionato: togli il flag, magari l'API è cambiata o era un falso negativo
    else:
        api[streak_key] = api.get(streak_key, 0) + 1
        if api[streak_key] >= MEDIA_MISS_THRESHOLD:
            api[flag_key] = True
    with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)


def update_api_usage_from_headers(api_index, response_headers):
    config = load_api_config()
    api = config["api_list"][api_index]

    remaining_header = None
    for header_key in ["x-ratelimit-requests-remaining", "x-ratelimit-remaining", "X-RateLimit-Remaining"]:
        if header_key in response_headers:
            remaining_header = response_headers.get(header_key)
            break

    if remaining_header is not None:
        try:
            rem_val = int(remaining_header)
            api["used"] = max(0, api["max_limit"] - rem_val)
        except ValueError:
            api["used"] += 1
    else:
        api["used"] += 1

    reset_header = None
    for header_key in ["x-ratelimit-requests-reset", "x-ratelimit-reset", "X-RateLimit-Reset"]:
        if header_key in response_headers:
            reset_header = response_headers.get(header_key)
            break
            
    if reset_header is not None:
        api["reset_info"] = str(reset_header)

    with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

def get_working_api_response(raw_text, short_code, want_images=False, want_video=False):
    config = load_api_config()
    apis = config["api_list"]

    url_match = re.search(r"https?://[^\s]+", raw_text)
    clean_url = url_match.group(0).split("?")[0] if url_match else f"https://www.instagram.com/p/{short_code}/"

    now = time.time()

    def unsupported_for_request(api):
        return bool((want_images and api.get("no_images")) or (want_video and api.get("no_video")))

    # Prima le API non note per non supportare immagini/video (se richiesti), poi le altre come ultima
    # risorsa; a parità, quelle con più crediti residui.
    apis_sorted = sorted(
        enumerate(apis),
        key=lambda item: (unsupported_for_request(item[1]), -(item[1]["max_limit"] - item[1]["used"]))
    )

    attempt_history = []

    for index, api in apis_sorted:
        remaining_credits = api["max_limit"] - api["used"]

        if remaining_credits <= 0:
            attempt_history.append((api['host'], "Crediti esauriti (Saltata)"))
            continue

        cooldown_until = api.get("cooldown_until", 0)
        if cooldown_until > now:
            wait_min = int((cooldown_until - now) // 60) + 1
            attempt_history.append((api['host'], f"In pausa per errori ripetuti (altri {wait_min} min, Saltata)"))
            continue

        param_key = api.get('param_name', 'shortcode')
        endpoint_path = api.get('endpoint_path', '/post')
        param_in_path = api.get('param_in_path', False)

        if param_in_path:
            if param_key == "url":
                url = f"https://{api['host']}{endpoint_path}/{clean_url}"
            else:
                url = f"https://{api['host']}{endpoint_path}/{short_code}"
            querystring = None
        else:
            url = f"https://{api['host']}{endpoint_path}"
            if param_key == "url":
                querystring = {param_key: clean_url}
            elif param_key in ["code_or_id_or_url", "code", "shortcode"]:
                querystring = {param_key: short_code}
            else:
                querystring = {param_key: short_code}

        headers = {
            "x-rapidapi-host": api["host"],
            "x-rapidapi-key": api["key"],
            "Content-Type": "application/json"
        }

        try:
            response = requests.get(url, headers=headers, params=querystring, timeout=10)
        except requests.RequestException as e:
            print(f"Errore di rete con l'API {api['host']}: {e}")
            attempt_history.append((api['host'], "Errore di connessione"))
            record_api_result(index, success=False)
            continue

        if response.status_code == 200:
            update_api_usage_from_headers(index, response.headers)

            updated_config = load_api_config()
            updated_api = updated_config["api_list"][index]
            remaining = updated_api["max_limit"] - updated_api["used"]

            data = response.json()
            post_data = data.get("data", data)

            caption = extract_caption_universal(post_data)

            if not caption:
                # Risposta 200 ma senza caption: non è un errore dell'API (il post magari non ne ha),
                # non conta per il circuit breaker.
                attempt_history.append((updated_api['host'], f"Nessuna caption (Crediti residui: {remaining})"))
                continue

            record_api_result(index, success=True)
            attempt_history.append((updated_api['host'], f"Successo (Crediti residui: {remaining})"))
            return data, attempt_history, index

        elif response.status_code in [429, 403]:
            attempt_history.append((api['host'], f"Bloccata/Esaurita (Status {response.status_code})"))
            record_api_result(index, success=False)
            continue
        else:
            attempt_history.append((api['host'], f"Errore HTTP {response.status_code}"))
            record_api_result(index, success=False)

    return None, attempt_history, None

def run_update(txt_file_path, tips_only=False):
    """Eseguito in un thread: estrae i dati dei reel nuovi e (per i bot per nazione) rigenera i CSV
    MyMaps, i piatti/consigli per argomento (tab Cibo/Cultura dell'app) e pubblica tutto su gh-pages
    per Streamlit Cloud. Ogni passo oltre al primo è "best effort": un errore (es. quota Gemini finita,
    git non disponibile) viene segnalato ma non interrompe l'aggiornamento principale."""
    summary = build_places.process_file(Path(txt_file_path), tips_only=tips_only)
    if tips_only:
        return summary, None

    maps = build_maps.build_maps()

    out_dir = build_places.output_dir(Path(txt_file_path))
    places_json = out_dir / "places.json"
    use_llm = not summary.get("quota_hit", False)
    try:
        build_dishes.process_file(places_json, use_llm)
    except Exception as e:
        print(f"⚠️ Aggiornamento piatti (build_dishes) non riuscito: {e}")
    try:
        build_culture.process_file(places_json, use_llm)
    except Exception as e:
        print(f"⚠️ Aggiornamento cultura per argomento (build_culture) non riuscito: {e}")
    try:
        if not publish_pages.publish_to_gh_pages(out_dir):
            print("⚠️ Pubblicazione su gh-pages non riuscita: Streamlit Cloud resta con i dati precedenti.")
    except Exception as e:
        print(f"⚠️ Pubblicazione su gh-pages non riuscita: {e}")

    return summary, maps


async def safe_send(bot, chat_id, text: str):
    """Invia un messaggio; senza rete riprova qualche volta e poi rinuncia (l'aggiornamento non si deve fermare)."""
    for attempt in range(5):
        try:
            await bot.send_message(chat_id, text)
            return
        except (NetworkError, TimedOut) as e:
            if attempt == 4:
                print(f"⚠️ Messaggio non inviato (rete assente): {e}")
                return
            await asyncio.sleep(10)


async def update_and_report(bot, chat_id, bot_name: str, txt_file_path: str):
    tips_only = bot_name in tips_bots
    if update_lock.locked():
        await safe_send(bot, chat_id, "⏳ Un altro aggiornamento è in corso, il tuo parte subito dopo.")
    async with update_lock:
        print(f"\n[AGGIORNAMENTO] {bot_name}")
        try:
            summary, maps = await asyncio.to_thread(run_update, txt_file_path, tips_only)
        except (Exception, SystemExit) as e:   # SystemExit: es. GEMINI_API_KEY mancante
            print(f"❌ Aggiornamento fallito per '{bot_name}': {e}")
            await safe_send(bot, chat_id, f"❌ Aggiornamento dati fallito: {e}")
            return

    quota_line = "⛔ Quota Gemini esaurita: i reel mancanti verranno analizzati al prossimo /aggiorna (domani)."
    if tips_only:
        lines = [f"🔄 Consigli aggiornati: {summary['tips']} (tips.md)"]
        new = summary["new_tips"]
        if new:
            lines.append(f"🆕 {len(new)} nuovi:\n" + "\n".join("• " + t for t in new[:5]) + ("\n…" if len(new) > 5 else ""))
        if summary["quota_hit"]:
            lines.append(quota_line)
        if summary["suggestions"]:
            lines.append(f"🔁 {len(summary['suggestions'])} possibili consigli doppioni da controllare al prossimo aggiornamento")
        await safe_send(bot, chat_id, "\n".join(lines))
        return

    lines = [f"🔄 Dati aggiornati: {summary['places']} luoghi"]
    new = summary["new_places"]
    if new:
        lines.append(f"🆕 {len(new)} nuovi: " + ", ".join(new[:10]) + (" …" if len(new) > 10 else ""))
    if summary["quota_hit"]:
        lines.append(quota_line)
    if summary["merged"]:
        lines.append("🔁 Doppioni uniti: " + ", ".join(f"{d} → {k}" for d, k in summary["merged"][:5])
                     + (" …" if len(summary["merged"]) > 5 else ""))
    if summary["suggestions"]:
        lines.append(f"🔁 {len(summary['suggestions'])} possibili doppioni da controllare al prossimo aggiornamento")
    lines.append(f"🗺️ CSV MyMaps aggiornati ({len(maps)} nazioni)")
    lines.append("Per vederli sulla mappa: in MyMaps elimina il layer della nazione e reimporta il CSV.")
    await safe_send(bot, chat_id, "\n".join(lines))


async def delayed_update(bot, chat_id, bot_name: str, txt_file_path: str):
    await asyncio.sleep(UPDATE_DELAY_SECONDS)
    # tolto dai pending prima di partire: da qui in poi non viene più annullato
    pending_updates.pop(bot_name, None)
    pending_args.pop(bot_name, None)
    await update_and_report(bot, chat_id, bot_name, txt_file_path)


def schedule_update(bot, chat_id, bot_name: str, txt_file_path: str):
    """(Ri)avvia il timer: l'aggiornamento parte UPDATE_DELAY_SECONDS dopo l'ultimo reel."""
    old = pending_updates.pop(bot_name, None)
    if old:
        old.cancel()
    pending_args[bot_name] = (bot, chat_id, bot_name, txt_file_path)
    pending_updates[bot_name] = asyncio.create_task(delayed_update(bot, chat_id, bot_name, txt_file_path))


async def handle_update_command(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str, txt_file_path: str):
    """/aggiorna: aggiorna subito (usa Gemini solo per i reel non ancora analizzati)."""
    old = pending_updates.pop(bot_name, None)
    if old:
        old.cancel()
    pending_args.pop(bot_name, None)
    await update.message.reply_text("🔄 Aggiornamento avviato...")
    await update_and_report(context.bot, update.effective_chat.id, bot_name, txt_file_path)


async def handle_errors_command(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str, txt_file_path: str):
    """/errori: mostra i post che hanno avuto errori durante la lettura del testo (paginate se tanti)."""
    errors = build_places.load_errors_log(Path(txt_file_path))
    if not errors:
        await update.message.reply_text("✅ Nessun errore registrato!")
        return

    # Recupera le informazioni dei reel dal file txt per mostrare la caption
    reels = build_places.parse_reels(Path(txt_file_path))
    reel_map = {r["shortcode"]: r for r in reels}

    # Ordina errori per timestamp decrescente
    sorted_errors = sorted(errors.items(), key=lambda x: x[1]["timestamp"], reverse=True)

    # Dividi in pagine di 10 errori
    page_size = 10
    total_pages = (len(sorted_errors) + page_size - 1) // page_size

    for page_num in range(total_pages):
        start_idx = page_num * page_size
        end_idx = min(start_idx + page_size, len(sorted_errors))
        page_errors = sorted_errors[start_idx:end_idx]

        lines = [f"❌ Pagina {page_num + 1}/{total_pages} ({len(errors)} post totali con errori):"]
        for shortcode, err_info in page_errors:
            reel = reel_map.get(shortcode)
            caption_preview = reel["caption"][:50].replace("\n", " ") if reel else "(caption non trovata)"
            if len(caption_preview) == 50:
                caption_preview += "..."
            timestamp = err_info.get("timestamp", "?")
            error_type = err_info.get("type", "?")
            requested_media = err_info.get("requested_media", "none")
            error_msg = err_info.get("error", "errore sconosciuto")[:60]

            media_str = f" [{requested_media}]" if requested_media != "none" else ""
            lines.append(f"• [{timestamp}] {error_type}{media_str}: {error_msg}")
            lines.append(f"  «{caption_preview}»")
            lines.append(f"  Shortcode: `{shortcode}`")

        # Aggiungi istruzioni solo nell'ultima pagina
        if page_num == total_pages - 1:
            lines.append("")
            lines.append("Ritenta:")
            lines.append("- /autoretry — ritenta tutti automaticamente")
            lines.append("- /skip <shortcode> — skippa un errore singolo")

        await update.message.reply_text("\n".join(lines))


async def handle_error_retry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str, txt_file_path: str):
    """Callback per i bottoni su uno specifico errore (Riprova, Skip, Riprova tutti)."""
    query = update.callback_query
    await query.answer()

    if query.data == "autoretry_all_errors":
        # Lancincia /autoretry per tutti gli errori
        await query.edit_message_text("⏳ Avvio autoretry...")
        await handle_autoretry_command(update, context, bot_name, txt_file_path)
        return

    if query.data.startswith("skip_error:"):
        # Formato: "skip_error:shortcode"
        shortcode = query.data.split(":", 1)[1]
        errors = build_places.load_errors_log(Path(txt_file_path))
        if shortcode not in errors:
            await query.edit_message_text(f"⚠️ Errore `{shortcode}` non trovato.")
            return
        err_info = errors[shortcode]
        build_places.remove_error(Path(txt_file_path), shortcode)
        await query.edit_message_text(
            f"✅ Errore rimosso: `{shortcode}`\n"
            f"Tipo: {err_info.get('type', 'unknown')}"
        )
        return

    if query.data.startswith("autoretry_error:"):
        # Formato: "autoretry_error:shortcode" - ritenta un singolo errore
        shortcode = query.data.split(":", 1)[1]
        errors = build_places.load_errors_log(Path(txt_file_path))
        if shortcode not in errors:
            await query.edit_message_text(f"⚠️ Errore `{shortcode}` non trovato.")
            return

        err_info = errors[shortcode]
        requested_media = err_info.get("requested_media", "none")

        # Ricostruisci il comando originale
        url = f"https://www.instagram.com/p/{shortcode}/"
        if requested_media == "both":
            link_text = f"{url} post"
        elif requested_media == "video":
            link_text = f"{url} video"
        elif requested_media == "images":
            link_text = f"{url} post"
        else:
            link_text = url

        # Rimuovi l'errore prima di ritentare
        build_places.remove_error(Path(txt_file_path), shortcode)

        # Rilancia il comando
        await query.edit_message_text(f"⏳ Riprovo {shortcode}...")
        try:
            result = await process_single_link(update, context, bot_name, txt_file_path, link_text)
            if result and result.get("success"):
                await query.edit_message_text(f"✅ {shortcode}: completato con successo!")
            elif result and result.get("reason") == "già_salvato_con_media":
                await query.edit_message_text(f"✅ {shortcode}: reel già completamente salvato!")
            else:
                await query.edit_message_text(f"❌ {shortcode}: {result.get('reason', 'fallito') if result else 'errore'}")
        except Exception as e:
            await query.edit_message_text(f"❌ {shortcode}: errore - {str(e)[:80]}")


async def flush_pending_updates():
    """Esegue subito gli aggiornamenti ancora in attesa del loro timer (usato prima di fermarsi per inattività)."""
    for bot_name, task in list(pending_updates.items()):
        task.cancel()
    pending_updates.clear()
    for bot_name, args in list(pending_args.items()):
        pending_args.pop(bot_name, None)
        try:
            await update_and_report(*args)
        except Exception as e:
            print(f"⚠️ Aggiornamento finale (prima dello spegnimento) fallito per '{bot_name}': {e}")


async def handle_blog_link(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str, txt_file_path: str,
                           blog_url: str):
    """Messaggio 'blog <url>': scarica la pagina e classifica i link esterni (vedi blog_import.py)."""
    await update.message.chat.send_action("typing")
    await update.message.reply_text("⏳ Sto analizzando il blog: con molti link Maps può richiedere qualche minuto "
                                    "(un geocoding al secondo)...")
    out_dir = build_places.output_dir(Path(txt_file_path))
    try:
        result = await asyncio.to_thread(blog_import.import_blog, blog_url, out_dir)
    except Exception as e:
        await update.message.reply_text(f"❌ Importazione blog fallita: {e}")
        return

    def preview(items, limit=10):
        return ", ".join(items[:limit]) + (f" … (+{len(items) - limit})" if len(items) > limit else "")

    lines = [f"📰 Blog analizzato: {blog_url}"]
    if result["maps_added"]:
        lines.append(f"📍 {len(result['maps_added'])} luoghi da Google Maps: " + preview(result["maps_added"]))
    if result["guides_added"]:
        lines.append(f"📖 {len(result['guides_added'])} guide salvate per Notebook (da importare a mano): "
                     + preview(result["guides_added"]))
    if not (result["maps_added"] or result["guides_added"]):
        lines.append("Nessun link esterno rilevante trovato.")
    if result["maps_added"]:
        lines.append(f"🔄 Mappa aggiornata tra {UPDATE_DELAY_SECONDS // 60} minuti (o subito con /aggiorna).")
        schedule_update(context.bot, update.effective_chat.id, bot_name, txt_file_path)
    await update.message.reply_text("\n".join(lines))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce gli errori di rete imprevisti senza fare crashare il bot."""
    print(f"⚠️ Errore di rete catturato dal gestore globale: {context.error}")

async def process_single_link(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str, txt_file_path: str,
                               link_text: str, link_num: int = None, total_links: int = None, msg_to_update = None,
                               update_needed: list = None):
    """
    Processa un singolo link (Instagram o blog). Usato sia per singoli link che per batch.
    link_text: es. "https://ig.com/p/ABC post" oppure "https://ig.com/reel/XYZ"
    link_num, total_links: per feedback progressivo
    msg_to_update: messaggio da aggiornare (batch)
    update_needed: lista [è_stato_salvato_qualcosa] per accumulare se serve aggiornamento finale
    """
    try:
        return await _process_single_link_impl(update, context, bot_name, txt_file_path, link_text, link_num, total_links, msg_to_update, update_needed)
    except Exception as e:
        import traceback
        print(f"❌ ERRORE CRITICO in process_single_link: {e}")
        print(traceback.format_exc())
        return {"type": "instagram", "success": False, "reason": "errore_interno", "error": str(e)[:100]}


async def _process_single_link_impl(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str, txt_file_path: str,
                               link_text: str, link_num: int = None, total_links: int = None, msg_to_update = None,
                               update_needed: list = None):
    """Implementazione vera di process_single_link."""
    # Estrai URL e modifiers
    url_match = re.search(r"https?://\S+", link_text)
    if not url_match:
        return None  # Skip, non è un URL valido

    full_url = url_match.group(0)
    words = re.sub(r"https?://\S+", " ", link_text)

    # Controlla se è un blog
    if re.search(r"\bblog\b", words, re.I):
        await handle_blog_link(update, context, bot_name, txt_file_path, full_url)
        if update_needed is not None:
            update_needed[0] = True
        return {"type": "blog", "success": True}

    # Estrai shortcode Instagram
    match = re.search(r"(?:reel|p)\/([A-Za-z0-9_-]+)", full_url)
    if not match:
        return {"type": "instagram", "success": False, "reason": "URL non valido"}

    short_code = match.group(1)
    want_images = bool(re.search(r"\bpost\b", words, re.I))
    want_video = bool(re.search(r"\bpost\b", words, re.I)) or bool(re.search(r"\bvideo\b", words, re.I))

    # Determina requested_media per tracciamento errori
    if want_images and want_video:
        requested_media = "both"
    elif want_images:
        requested_media = "images"
    elif want_video:
        requested_media = "video"
    else:
        requested_media = "none"

    saved = saved_block(txt_file_path, short_code)
    if saved and not (want_images or want_video):
        return {"type": "instagram", "success": False, "reason": "già_salvato"}

    if saved and (want_images or want_video):
        if (build_places.load_image_texts(Path(txt_file_path)).get(short_code)
                or any(l.startswith("Testo immagini:") for l in saved[0][saved[1]:saved[2]])):
            return {"type": "instagram", "success": False, "reason": "già_salvato_con_media"}

    # === Elaborazione link Instagram ===
    for attempt in range(3):
        try:
            await update.message.chat.send_action("typing")
            break
        except (NetworkError, TimedOut):
            if attempt == 2:
                raise
            await asyncio.sleep(1)

    data, attempt_history, api_index = get_working_api_response(full_url, short_code, want_images, want_video)

    log_summary = "\n".join([f"  - {host}: {status}" for host, status in attempt_history])
    print(f"[TENTATIVI API - {bot_name}]\n{log_summary}")

    if not data:
        error_msg = "; ".join([status for _, status in attempt_history[:3]])
        build_places.save_error(Path(txt_file_path), short_code, "api_failure", error_msg, requested_media=requested_media)
        return {"type": "instagram", "success": False, "reason": "api_failure", "error": error_msg}

    post_data = data.get("data", data)
    caption = extract_caption_universal(post_data) or "Nessuna caption"
    caption = caption.replace("\n", " ")
    code = post_data.get("code", short_code)
    reel_link = f"https://www.instagram.com/p/{code}/"
    posted_date = extract_posted_date(post_data)

    image_text, image_line = "", ""
    if want_images:
        urls = extract_image_urls(post_data)
        record_media_result(api_index, "images", found=bool(urls))
        if not urls:
            image_line = "🖼️ Nessuna immagine trovata nella risposta dell'API"
            build_places.save_error(Path(txt_file_path), short_code, "images_missing",
                                   f"API {attempt_history[0][0] if attempt_history else 'unknown'}: nessuna immagine", requested_media=requested_media)
        else:
            try:
                image_text = await asyncio.to_thread(extract_image_text, urls)
                image_line = (f"🖼️ Testo letto da {len(urls)} immagini ({len(image_text)} caratteri)" if image_text
                              else f"🖼️ Nessun testo nelle {len(urls)} immagini")
                build_places.remove_error(Path(txt_file_path), short_code)
            except Exception as e:
                error_str = str(e)
                if ("400" in error_str or "410" in error_str or "Bad Request" in error_str) and api_index is not None:
                    print(f"⏳ URL immagini scaduti, richiedo nuove immagini a RapidAPI...")
                    try:
                        new_data, _, _ = get_working_api_response(full_url, short_code, want_images=True, want_video=False)
                        if new_data:
                            new_urls = extract_image_urls(new_data)
                            if new_urls:
                                print(f"✅ Nuove immagini ricevute, riprovo OCR...")
                                image_text = await asyncio.to_thread(extract_image_text, new_urls)
                                image_line = (f"🖼️ Testo letto da {len(new_urls)} immagini ({len(image_text)} caratteri)" if image_text
                                              else f"🖼️ Nessun testo nelle {len(new_urls)} immagini")
                                build_places.remove_error(Path(txt_file_path), short_code)
                            else:
                                raise Exception("Nessuna immagine nella risposta di retry")
                        else:
                            raise Exception("RapidAPI non ha restituito dati nel retry")
                    except Exception as retry_error:
                        print(f"⚠️ Retry fallito: {retry_error}")
                        build_places.save_error(Path(txt_file_path), short_code, "images", f"Primo fallimento: {error_str[:50]}; Retry: {str(retry_error)[:50]}", requested_media=requested_media)
                        image_line = f"🖼️ Testo immagini non letto (retry fallito: {str(retry_error)[:80]})"
                else:
                    print(f"⚠️ Testo immagini non letto: {e}")
                    build_places.save_error(Path(txt_file_path), short_code, "images", error_str, requested_media=requested_media)
                    image_line = f"🖼️ Testo immagini non letto ({error_str[:100]})"

    if want_video:
        video_url = extract_video_url(post_data)
        record_media_result(api_index, "video", found=bool(video_url))
        if not video_url:
            video_line = "🎞️ Nessun video trovato nella risposta dell'API"
            build_places.save_error(Path(txt_file_path), short_code, "video_missing",
                                   f"API {attempt_history[0][0] if attempt_history else 'unknown'}: nessun video", requested_media=requested_media)
        else:
            try:
                video_text = await asyncio.to_thread(extract_video_text, video_url)
                video_line = (f"🎞️ Testo a schermo letto dal video ({len(video_text)} caratteri)" if video_text
                              else "🎞️ Nessun testo a schermo nel video")
                image_text = " / ".join(t for t in (image_text, video_text) if t)
                build_places.remove_error(Path(txt_file_path), short_code)
            except Exception as e:
                error_str = str(e)
                if ("400" in error_str or "410" in error_str or "Bad Request" in error_str) and api_index is not None:
                    print(f"⏳ URL video scaduto, richiedo nuovo video a RapidAPI...")
                    try:
                        new_data, _, _ = get_working_api_response(full_url, short_code, want_images=False, want_video=True)
                        if new_data:
                            new_video_url = extract_video_url(new_data)
                            if new_video_url:
                                print(f"✅ Nuovo video ricevuto, riprovo OCR...")
                                video_text = await asyncio.to_thread(extract_video_text, new_video_url)
                                video_line = (f"🎞️ Testo a schermo letto dal video ({len(video_text)} caratteri)" if video_text
                                              else "🎞️ Nessun testo a schermo nel video")
                                image_text = " / ".join(t for t in (image_text, video_text) if t)
                                build_places.remove_error(Path(txt_file_path), short_code)
                            else:
                                raise Exception("Nessun video nella risposta di retry")
                        else:
                            raise Exception("RapidAPI non ha restituito dati nel retry")
                    except Exception as retry_error:
                        print(f"⚠️ Retry video fallito: {retry_error}")
                        build_places.save_error(Path(txt_file_path), short_code, "video", f"Primo fallimento: {error_str[:50]}; Retry: {str(retry_error)[:50]}", requested_media=requested_media)
                        video_line = f"🎞️ Testo video non letto (retry fallito: {str(retry_error)[:80]})"
                else:
                    print(f"⚠️ Testo video non letto: {e}")
                    build_places.save_error(Path(txt_file_path), short_code, "video", error_str, requested_media=requested_media)
                    video_line = f"🎞️ Testo video non letto ({error_str[:100]})"
        image_line = "\n".join(l for l in (image_line, video_line) if l)

    if saved:
        if not image_text:
            return {"type": "instagram", "success": False, "reason": "testo_non_aggiunto"}
        build_places.save_image_text(Path(txt_file_path), short_code, image_text)
        return {"type": "instagram", "success": True, "reason": "testo_aggiunto"}

    if (want_images or want_video) and not image_text:
        image_line += ": salvo solo la caption."
        if want_images and not extract_image_urls(post_data):
            image_line += "\n💾 Immagini non trovate registrate negli errori."
        elif want_video and not extract_video_url(post_data):
            image_line += "\n💾 Video non trovato registrato negli errori."

    os.makedirs(os.path.dirname(txt_file_path), exist_ok=True)

    with open(txt_file_path, mode="a", encoding="utf-8") as f:
        f.write(f"Link: {reel_link}\n")
        f.write(f"Caption: {caption}\n")
        if posted_date:
            f.write(f"Data: {posted_date}\n")
        f.write("-" * 50 + "\n\n")
    if image_text:
        build_places.save_image_text(Path(txt_file_path), code, image_text)

    if update_needed is not None:
        update_needed[0] = True

    try:
        formatted_history = "\n".join([f"• {host} ➔ {status}" for host, status in attempt_history]) if attempt_history else "Nessun report disponibile"
    except Exception as e:
        print(f"⚠️ Errore formattando history: {e}")
        formatted_history = "Errore nel report API"

    return {
        "type": "instagram",
        "success": True,
        "reason": "salvato",
        "caption": caption,
        "caption_preview": (caption[:75] + '...') if len(caption) > 75 else caption,
        "image_line": image_line,
        "reel_link": reel_link,
        "bot_name": bot_name,
        "txt_file_path": txt_file_path,
        "formatted_history": formatted_history
    }


async def handle_batch_links(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str, txt_file_path: str, text: str):
    """Processa multipli link separati da semicolon o righe vuote."""
    # Split prima per righe vuote (doppio newline), poi per semicoloni all'interno di ogni riga
    import re
    raw_links = []

    # Split per righe vuote (doppio newline)
    for section in re.split(r'\n\s*\n', text):
        # Split per semicoloni all'interno della sezione
        for item in section.split(";"):
            item = item.strip()
            if item:
                raw_links.append(item)

    raw_links = [l for l in raw_links if l]  # Rimuovi vuoti

    if len(raw_links) > 50:
        await update.message.reply_text(f"❌ Massimo 50 link per messaggio (hai mandato {len(raw_links)}). Dividi in più messaggi.")
        return

    update_needed = [False]  # Accumulatore se uno dei link richiede aggiornamento

    # Messaggio iniziale con placeholder
    try:
        msg = await update.message.reply_text(f"⏳ Elaborando {len(raw_links)} link...")
    except (NetworkError, TimedOut):
        await asyncio.sleep(1)
        msg = await update.message.reply_text(f"⏳ Elaborando {len(raw_links)} link...")

    results = []
    for idx, link_text in enumerate(raw_links, 1):
        try:
            result = await process_single_link(update, context, bot_name, txt_file_path, link_text,
                                              link_num=idx, total_links=len(raw_links), msg_to_update=msg,
                                              update_needed=update_needed)
            if result:
                results.append((idx, result))

                # Invia messaggio dettagliato per ogni link salvato con successo
                if result.get("type") == "instagram" and result.get("success"):
                    reason = result.get("reason", "")
                    if reason == "salvato":
                        detailed_msg = (
                            f"Salvato con successo! ✅\n\n"
                            f"🤖 Bot: {result.get('bot_name', 'N/A')}\n"
                            f"📁 File: {result.get('txt_file_path', 'N/A')}\n\n"
                            f"🔗 {result.get('reel_link', 'N/A')}\n\n"
                            f"💬 {result.get('caption_preview', 'N/A')}\n"
                        )
                        if result.get('image_line'):
                            detailed_msg += f"\n{result.get('image_line')}\n"
                        detailed_msg += (
                            f"\n📋 Report tentativi API:\n"
                            f"{result.get('formatted_history', 'N/A')}\n\n"
                            f"🔄 Dati e mappa si aggiornano tra {UPDATE_DELAY_SECONDS // 60} minuti (o subito con /aggiorna)."
                        )
                        try:
                            await update.message.reply_text(detailed_msg)
                        except Exception as e:
                            print(f"⚠️ Non riuscito a inviare dettagli link {idx}: {e}")

            # Aggiorna messaggio progressivo leggero
            success_count = sum(1 for _, r in results if isinstance(r, dict) and r.get("success"))
            progress_text = f"⏳ Elaborazione: {idx}/{len(raw_links)} ✅ {success_count} salvati"
            try:
                await msg.edit_text(progress_text)
            except Exception as e:
                print(f"⚠️ Non riuscito ad aggiornare il messaggio progressivo: {e}")
        except Exception as e:
            print(f"⚠️ Errore elaborando link {idx}: {e}")

    # Riepilogo finale
    success_count = sum(1 for _, r in results if isinstance(r, dict) and r.get("success"))
    failed_count = sum(1 for _, r in results if isinstance(r, dict) and not r.get("success"))

    final_text = (f"🎉 Elaborazione completata!\n\n"
                  f"✅ Salvati: {success_count}\n"
                  f"❌ Falliti: {failed_count}\n"
                  f"Totale: {len(results)}")

    if update_needed[0]:
        final_text += f"\n\n🔄 Dati e mappa si aggiornano tra {UPDATE_DELAY_SECONDS // 60} minuti (o subito con /aggiorna)."
        schedule_update(context.bot, update.effective_chat.id, bot_name, txt_file_path)

    try:
        await msg.edit_text(final_text)
    except Exception as e:
        print(f"⚠️ Non riuscito a editare il messaggio finale: {e}")
        await update.message.reply_text(final_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str, txt_file_path: str,
                         assistant_text=None):
    """assistant_text: handler dell'assistente (bot nazione) per note e parole chiave; None per i bot di consigli."""
    text = update.message.text.strip()
    print(f"\n[ATTIVITÀ] Il bot '{bot_name}' ha ricevuto un messaggio.")

    # Gestione retry automatico per errori precedenti
    if (hasattr(context, "user_data") and context.user_data.get("retry_shortcodes")):
        match = re.search(r"(?:reel|p)\/([A-Za-z0-9_-]+)", text)
        if match:
            shortcode = match.group(1)
            expected_shortcodes = context.user_data.get("retry_shortcodes", [])
            current_index = context.user_data.get("retry_index", 0)
            if current_index < len(expected_shortcodes) and shortcode == expected_shortcodes[current_index]:
                # È il post corretto; procedi normalmente (il resto della funzione farà il retry)
                pass
            else:
                # Post diverso: aspettiamo il corretto
                await update.message.reply_text(
                    f"⚠️ Aspettavo {expected_shortcodes[current_index]}, ma hai inviato {shortcode}.\n"
                    f"Riprova con il post corretto o scrivi /annulla per abortire."
                )
                return

    # Controlla se ci sono separatori per batch link: semicolon o doppi newline
    # Ma esclude `;` finale (utente dimentica di toglierlo)
    text_for_batch = text.rstrip(";").strip()
    has_batch_sep = ";" in text_for_batch or "\n\n" in text_for_batch
    if has_batch_sep:
        try:
            context.user_data.pop("note_for", None)  # Cancella nota in attesa
            await handle_batch_links(update, context, bot_name, txt_file_path, text_for_batch)
            return
        except Exception as e:
            print(f"⚠️ Errore in batch link processing: {e}")
            # Ricaduta a singolo link se batch fallisce
            text = text_for_batch  # Usa il testo ripulito

    if text.upper() == "INFO":
        config = load_api_config()
        info_lines = ["📊 Stato Crediti API Configurate (da RapidAPI):\n"]
        now = time.time()
        for api in config["api_list"]:
            remaining = api["max_limit"] - api["used"]
            raw_reset = api.get("reset_info")
            readable_reset = format_seconds_to_readable(raw_reset) if raw_reset else "Non ancora disponibile"

            status_bits = []
            cooldown_until = api.get("cooldown_until", 0)
            if cooldown_until > now:
                status_bits.append(f"⏸️ in pausa altri {int((cooldown_until - now) // 60) + 1} min")
            if api.get("no_images"):
                status_bits.append("🖼️ no immagini")
            if api.get("no_video"):
                status_bits.append("🎞️ no video")
            status_line = f"  - Stato: {', '.join(status_bits)}\n" if status_bits else ""

            info_lines.append(
                f"• {api['host']}\n"
                f"  - Rimasti: {remaining} / {api['max_limit']}\n"
                f"  - Usati: {api['used']}\n"
                f"  - Reset: {readable_reset}\n"
                f"{status_line}"
            )
        await update.message.reply_text("\n".join(info_lines))
        return

    # Per il single link, chiama process_single_link
    context.user_data.pop("note_for", None)  # Cancella nota in attesa
    result = await process_single_link(update, context, bot_name, txt_file_path, text)

    if not result:
        # Non è Instagram
        url_match = re.search(r"https?://\S+", text)
        if url_match and re.search(r"\bblog\b", text, re.I):
            return  # already handled by process_single_link
        if assistant_text:
            await assistant_text(update, context)
            return
        await update.message.reply_text("⚠️ Non ho trovato un link Instagram valido (o digita INFO per i crediti).")
        return

    # Mostra il risultato all'utente
    if result["type"] == "instagram":
        if result["success"]:
            reason = result["reason"]
            if reason == "salvato":
                caption = result.get("caption", "")
                image_line = result.get("image_line", "")
                msg = f"Salvato con successo! ✅\n\n"
                if caption:
                    msg += f"💬 {caption}\n"
                if image_line:
                    msg += f"\n{image_line}\n"
                msg += f"\n🔄 Dati e mappa si aggiornano tra {UPDATE_DELAY_SECONDS // 60} minuti (o subito con /aggiorna)."
                await update.message.reply_text(msg)
            elif reason == "testo_aggiunto":
                await update.message.reply_text(
                    f"✅ Testo aggiunto al contenuto già salvato\n\n"
                    f"🔄 Dati e mappa si aggiornano tra {UPDATE_DELAY_SECONDS // 60} minuti (o subito con /aggiorna)."
                )
            schedule_update(context.bot, update.effective_chat.id, bot_name, txt_file_path)
        else:
            reason = result["reason"]
            if reason == "api_failure":
                await update.message.reply_text(
                    f"❌ Impossibile recuperare il contenuto.\n\n"
                    f"💾 Link registrato negli errori. Ritenta con /autoretry."
                )
            elif reason == "già_salvato":
                await update.message.reply_text(
                    "ℹ️ Questo contenuto era già stato salvato in precedenza! "
                    "(scrivi post/video + link per aggiungere il testo di immagini/video)"
                )
            elif reason == "già_salvato_con_media":
                await update.message.reply_text("ℹ️ Già salvato, anche con il testo di immagini/video.")
            elif reason == "testo_non_aggiunto":
                await update.message.reply_text(f"ℹ️ Già salvato, testo non aggiunto.")

    # Aggiorna il contatore di retry se siamo in modalità replay
    if hasattr(context, "user_data") and context.user_data.get("retry_shortcodes"):
        context.user_data["retry_index"] = context.user_data.get("retry_index", 0) + 1
        remaining = len(context.user_data["retry_shortcodes"]) - context.user_data["retry_index"]
        if remaining > 0:
            await update.message.reply_text(
                f"✅ Post salvato!\n\n"
                f"⏳ Prossimi da riprovare: {remaining} ({context.user_data['retry_index'] + 1} / "
                f"{len(context.user_data['retry_shortcodes'])})\n"
                f"Shortcode: {context.user_data['retry_shortcodes'][context.user_data['retry_index']]}"
            )
        else:
            context.user_data.pop("retry_shortcodes", None)
            context.user_data.pop("retry_index", None)
            await update.message.reply_text("✅ Tutti i retry completati!")


async def handle_cancel_retry_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/annulla: cancella il processo di retry in corso."""
    if hasattr(context, "user_data"):
        context.user_data.pop("retry_shortcodes", None)
        context.user_data.pop("retry_index", None)
    await update.message.reply_text("❌ Retry annullato.")


async def handle_skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str, txt_file_path: str):
    """/skip <shortcode>: rimuove un errore dalla lista (per errori legittimi come post senza immagini)."""
    args = update.message.text.split(maxsplit=1)
    if len(args) < 2:
        await update.message.reply_text("❌ Uso: `/skip <shortcode>`\nEsempio: `/skip ABC123DEF456`")
        return

    shortcode = args[1].strip()
    errors = build_places.load_errors_log(Path(txt_file_path))

    if shortcode not in errors:
        await update.message.reply_text(f"⚠️ Errore `{shortcode}` non trovato nella lista.")
        return

    err_info = errors[shortcode]
    build_places.remove_error(Path(txt_file_path), shortcode)
    await update.message.reply_text(
        f"✅ Errore rimosso: `{shortcode}`\n"
        f"Tipo: {err_info.get('type', 'unknown')}\n"
        f"Motivo: {err_info.get('error', 'N/A')[:100]}"
    )


async def handle_retryall_command(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str, txt_file_path: str):
    """/retryall: ritenta tutti gli errori (alternativa diretta al bottone)."""
    errors = build_places.load_errors_log(Path(txt_file_path))
    if not errors:
        await update.message.reply_text("✅ Nessun errore da riprovare.")
        return

    shortcodes = list(errors.keys())
    if not hasattr(context, "user_data"):
        context.user_data = {}
    context.user_data["retry_shortcodes"] = shortcodes
    context.user_data["retry_index"] = 0

    await update.message.reply_text(
        f"⏳ Preparati a reinviare i {len(shortcodes)} post con POST (immagini), VIDEO (video), o senza (API).\n\n"
        f"Prossimo da riprovare: {shortcodes[0]} (1 / {len(shortcodes)})\n\n"
        f"Risposte attese:\n"
        f"- `post https://instagram.com/p/{shortcodes[0]}/` (per testo immagini)\n"
        f"- `video https://instagram.com/p/{shortcodes[0]}/` (per testo video)\n"
        f"- `https://instagram.com/p/{shortcodes[0]}/` (per riprovare l'API RapidAPI)"
    )


async def handle_autoretry_command(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name: str, txt_file_path: str):
    """/autoretry: ritenta automaticamente TUTTI gli errori ricreando i comandi originali."""
    errors = build_places.load_errors_log(Path(txt_file_path))

    # Distingui tra comando e callback query
    is_callback = update.callback_query is not None

    if not errors:
        msg_text = "✅ Nessun errore da riprovare."
        if is_callback:
            await update.callback_query.edit_message_text(msg_text)
        else:
            await update.message.reply_text(msg_text)
        return

    msg_text = f"⏳ Riprovo automaticamente {len(errors)} errori..."
    if is_callback:
        msg_info = await update.callback_query.edit_message_text(msg_text)
    else:
        msg_info = await update.message.reply_text(msg_text)

    results = {"riusciti": 0, "falliti": 0}
    details = []

    for idx, (shortcode, err_info) in enumerate(list(errors.items()), 1):
        requested_media = err_info.get("requested_media", "none")

        # Ricostruisci il comando originale
        url = f"https://www.instagram.com/p/{shortcode}/"
        if requested_media == "both":
            link_text = f"{url} post"
        elif requested_media == "video":
            link_text = f"{url} video"
        elif requested_media == "images":
            link_text = f"{url} post"
        else:  # "none"
            link_text = url

        # Rimuovi l'errore prima di ritentare (verrà risalvato se fallisce)
        build_places.remove_error(Path(txt_file_path), shortcode)

        # Rilancia il comando come se l'utente lo avesse reinviato
        try:
            result = await process_single_link(update, context, bot_name, txt_file_path, link_text)
            if result and result.get("success"):
                results["riusciti"] += 1
                status = "✅"
                reason = result.get("reason", "salvato")
            elif result and result.get("reason") == "già_salvato_con_media":
                results["riusciti"] += 1
                status = "✅"
                reason = "già completamente salvato"
            else:
                results["falliti"] += 1
                status = "❌"
                reason = result.get("reason", "fallito") if result else "errore sconosciuto"
            details.append(f"{status} {shortcode}: {reason}")
        except Exception as e:
            results["falliti"] += 1
            details.append(f"❌ {shortcode}: {str(e)[:80]}")

        # Aggiorna il messaggio di progresso ogni 3 errori o alla fine
        if idx % 3 == 0 or idx == len(errors):
            progress_msg = (f"🔄 Riprovo automaticamente...\n"
                           f"({idx} / {len(errors)} completati)\n\n" +
                           "\n".join(details[-5:]))  # Mostra ultimi 5
            try:
                await msg_info.edit_text(progress_msg)
            except Exception:
                pass

    final_msg = (f"🔄 Retry automatico completato!\n\n"
                f"✅ Riusciti: {results['riusciti']}\n"
                f"❌ Falliti: {results['falliti']}\n\n" +
                "\n".join(details))

    if results["riusciti"] > 0:
        final_msg += f"\n\n🔄 I dati si aggiorneranno tra {UPDATE_DELAY_SECONDS // 60} minuti (o subito con /aggiorna)."

    await msg_info.edit_text(final_msg)

async def start_single_bot(bot_config):
    name = bot_config.get("name", "Bot Senza Nome")
    token = bot_config["token"]
    txt_file_path = bot_config.get("txt_file_path", "")   # non serve per il bot "assistant"
    if bot_config.get("type") == "tips":
        tips_bots.add(name)

    request_config = HTTPXRequest(
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        connection_pool_size=30
    )
    
    application = ApplicationBuilder().token(token).request(request_config).build()

    if IDLE_STOP_SECONDS:   # solo in modalità cellulare: registra ogni update senza interferire con gli altri handler
        application.add_handler(TypeHandler(Update, touch_activity), group=-1)
    application.add_handler(CommandHandler("spegni", handle_stop_command))

    if bot_config.get("type") == "assistant":
        assistant.register(application, update_lock)
    else:
        assistant_text = None
        if name not in tips_bots:   # bot nazione: anche /consiglia, parole chiave e bottoni di feedback
            assistant_text = assistant.register(application, update_lock,
                                                home_file=build_places.output_dir(txt_file_path) / "places.json")
        application.add_handler(CommandHandler(
            "aggiorna",
            lambda update, ctx: handle_update_command(update, ctx, name, txt_file_path)
        ))
        application.add_handler(CommandHandler(
            "errori",
            lambda update, ctx: handle_errors_command(update, ctx, name, txt_file_path)
        ))
        application.add_handler(CommandHandler(
            "skip",
            lambda update, ctx: handle_skip_command(update, ctx, name, txt_file_path)
        ))
        application.add_handler(CommandHandler(
            "retryall",
            lambda update, ctx: handle_retryall_command(update, ctx, name, txt_file_path)
        ))
        application.add_handler(CommandHandler(
            "autoretry",
            lambda update, ctx: handle_autoretry_command(update, ctx, name, txt_file_path)
        ))
        application.add_handler(CommandHandler("annulla", handle_cancel_retry_command))
        application.add_handler(CallbackQueryHandler(
            lambda update, ctx: handle_error_retry_callback(update, ctx, name, txt_file_path),
            pattern="^(autoretry|skip)"
        ))
        application.add_handler(MessageHandler(
            filters.TEXT & (~filters.COMMAND),
            lambda update, ctx: handle_message(update, ctx, name, txt_file_path, assistant_text)
        ))
    
    application.add_error_handler(error_handler)
    
    # Aumentato a 5 tentativi con attesa crescente (backoff) per superare i blocchi intermittenti del firewall
    for attempt in range(5):
        try:
            await application.initialize()
            await application.start()
            # False: i messaggi inviati a PC spento (Telegram li tiene 24 ore) vengono elaborati all'avvio
            await application.updater.start_polling(drop_pending_updates=False)
            print(f"[{name}] Avviato correttamente (Token terminante in ...{token[-6:]}) - Salva su: {txt_file_path}")
            return application
        except Exception as e:
            if attempt == 4:
                raise e
            wait_time = (attempt + 1) * 4  # 4s, 8s, 12s, 16s
            print(f"⚠️ Tentativo {attempt+1} fallito per '{name}' a causa della rete. Riprovo tra {wait_time} secondi...")
            await asyncio.sleep(wait_time)

async def main():
    bots_config = load_bots_config()
    bot_list = bots_config.get("bots", [])
    
    if not bot_list:
        print("Nessun bot configurato nel file bots_config.json!")
        return

    applications = []
    for b_config in bot_list:
        try:
            applications.append(await start_single_bot(b_config))
            await asyncio.sleep(4.0)  # Pausa maggiore tra l'avvio di un bot e l'altro per non intasare il firewall aziendale
        except Exception as e:
            name = b_config.get("name", "Bot")
            print(f"❌ Impossibile avviare il bot '{name}' dopo vari tentativi: {e}")

    if IDLE_STOP_SECONDS:
        print(f"⏱️ Modalità cellulare: mi fermo da solo dopo {IDLE_STOP_SECONDS}s senza attività.")
    while not stop_requested["now"] and (
            not IDLE_STOP_SECONDS or time.monotonic() - last_activity["t"] < IDLE_STOP_SECONDS):
        await asyncio.sleep(5)

    print("💤 Completo gli aggiornamenti in attesa e mi fermo...")
    await flush_pending_updates()
    for app in applications:
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception as e:
            print(f"⚠️ Arresto non pulito di un bot: {e}")
    print("✅ Bot fermato.")

if __name__ == "__main__":
    if not os.environ.get("GEMINI_API_KEY"):
        print("❌ GEMINI_API_KEY non trovata nelle variabili d'ambiente!")
        print("Su Termux aggiungi a ~/.bashrc:")
        print('  export GEMINI_API_KEY=your_api_key')
        print("Su Windows (PowerShell):")
        print("  $env:GEMINI_API_KEY='your_api_key'")
        print("Su Windows (cmd):")
        print("  set GEMINI_API_KEY=your_api_key")
        print("Su Mac/Linux:")
        print("  export GEMINI_API_KEY=your_api_key")
        print("\nChiave da: https://aistudio.google.com/apikey")
        sys.exit(1)

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot arrestati correttamente.")