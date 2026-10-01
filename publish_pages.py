"""Pubblica una copia filtrata dei dati (places/dishes/culture_topics.json, due file testuali per
nazione) sul branch gh-pages di GitHub: è lì che app.py va a leggere quando gira su Streamlit Cloud,
che non ha accesso al Google Drive locale (vedi README.md). Chiamato da script.py dopo ogni
aggiornamento (timer o /aggiorna), così la versione online resta sincronizzata senza bisogno di farlo
a mano. Richiede `git` e una chiave SSH con permesso di push sul repo (stessa che serve per i commit
manuali): se manca uno dei due (es. sul telefono, dove il codice è copiato con rclone e non c'è un
checkout git), salta la pubblicazione con un avviso, senza interrompere l'aggiornamento.

Privacy: status/rating/user_notes (stato visitato/voto/note personali inseriti dall'utente via bot,
NON dati pubblici da reel) vengono sempre tolti da places.json prima di pubblicare. Da FONTI/<Nazione>/
vengono presi SOLO i due file generati dal bot (<Nazione>.txt, <Nazione>_culture.txt): eventuali altri
file nella stessa cartella (note personali, guide da blog importati, testo immagini) restano privati."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import build_places

REPO_URL = "git@github.com:agclaudia7-creator/viaggio-bot.git"
PUBLISH_DIR = build_places.CONFIG_DIR / "gh-pages-publish"
GIT_USER_NAME = "agclaudia7"
GIT_USER_EMAIL = "tua.email@example.com"   # stessa identità già usata per i commit manuali sul repo

PERSONAL_PLACE_FIELDS = ("status", "rating", "user_notes")


def _run_git(*args, cwd: Path, timeout: int = 60) -> bool:
    try:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=timeout)
        return True
    except FileNotFoundError:
        print("⚠️ git non installato: salto la pubblicazione su gh-pages.")
        return False
    except subprocess.CalledProcessError as e:
        print(f"⚠️ git {' '.join(args)} fallito: {(e.stderr or '').strip()[:300]}")
        return False
    except subprocess.TimeoutExpired:
        print(f"⚠️ git {' '.join(args)} troppo lento, salto la pubblicazione.")
        return False


def _run_git_networked(*args, cwd: Path, timeout: int = 60, retries: int = 2) -> bool:
    """Come _run_git, ma con un ritentativo per clone/fetch/push: una porta bloccata un istante (visto
    in sessione: "ssh: connect to host github.com port 22: Connection timed out", risolto da solo al
    tentativo successivo) non deve far saltare la pubblicazione di un intero aggiornamento."""
    for attempt in range(retries):
        if _run_git(*args, cwd=cwd, timeout=timeout):
            return True
        if attempt < retries - 1:
            print("   ritento in 5s...")
            time.sleep(5)
    return False


def _ensure_clone() -> bool:
    """Clona gh-pages in CONFIG_DIR (fuori da Google Drive: una repo git dentro una cartella
    sincronizzata da Drive può bloccarsi su index.lock, visto e capito in sessione) la prima volta,
    altrimenti la risincronizza da zero (fetch + reset --hard) per evitare conflitti con eventuali
    altre modifiche al branch nel frattempo."""
    if (PUBLISH_DIR / ".git").exists():
        ok = _run_git_networked("fetch", "origin", "gh-pages", cwd=PUBLISH_DIR) and \
             _run_git("reset", "--hard", "origin/gh-pages", cwd=PUBLISH_DIR)
    else:
        PUBLISH_DIR.parent.mkdir(parents=True, exist_ok=True)
        ok = _run_git_networked("clone", "--branch", "gh-pages", "--single-branch", REPO_URL, str(PUBLISH_DIR),
                                cwd=PUBLISH_DIR.parent, timeout=180)
    if ok:
        _run_git("config", "user.name", GIT_USER_NAME, cwd=PUBLISH_DIR)
        _run_git("config", "user.email", GIT_USER_EMAIL, cwd=PUBLISH_DIR)
    return ok


def _strip_personal_fields(places: list) -> list:
    for p in places:
        for key in PERSONAL_PLACE_FIELDS:
            p.pop(key, None)
    return places


def publish_to_gh_pages(bot_output_dir: Path) -> bool:
    """bot_output_dir: la cartella <Nazione>_bot_output appena aggiornata (places.json ecc. già
    scritti). Copia i suoi dati nella copia locale di gh-pages e pubblica solo se è cambiato qualcosa.
    Ritorna True se tutto ok (incluso "niente da pubblicare"), False se ha rinunciato per un errore
    (git/rete/auth): non blocca il resto dell'aggiornamento in nessun caso, va sempre chiamata dentro
    un try/except da chi la usa."""
    if not _ensure_clone():
        return False

    folder_name = bot_output_dir.name   # es. "Thailandia_bot_output"
    dest = PUBLISH_DIR / "BOT_OUTPUT" / folder_name
    dest.mkdir(parents=True, exist_ok=True)

    for fname in ("places.json", "dishes.json", "culture_topics.json"):
        src = bot_output_dir / fname
        if not src.exists():
            continue
        data = json.loads(src.read_text(encoding="utf-8"))
        if fname == "places.json":
            data = _strip_personal_fields(data)
        (dest / fname).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # FONTI/<Nazione>/{<Nazione>.txt,<Nazione>_culture.txt}: notebook_path dà già il percorso giusto,
    # sia nello schema nuovo (BOT_OUTPUT/<Nazione>_bot_output) che vecchio.
    notebook_txt = build_places.notebook_path(bot_output_dir)
    fonti_dir = notebook_txt.parent
    nazione = notebook_txt.stem
    if fonti_dir.exists():
        fonti_dest = PUBLISH_DIR / "FONTI" / nazione
        fonti_dest.mkdir(parents=True, exist_ok=True)
        for fname in (f"{nazione}.txt", f"{nazione}_culture.txt"):
            src = fonti_dir / fname
            if src.exists():
                shutil.copy(src, fonti_dest / fname)

    if not _run_git("add", "-A", cwd=PUBLISH_DIR):
        return False
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=PUBLISH_DIR).returncode == 0:
        return True   # nessuna modifica rispetto a quanto già pubblicato

    if not _run_git("commit", "-m", f"Aggiorna dati pubblici: {nazione}", cwd=PUBLISH_DIR):
        return False
    return _run_git_networked("push", "origin", "gh-pages", cwd=PUBLISH_DIR, timeout=120)
