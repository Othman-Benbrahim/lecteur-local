"""Collecteur sans modèle (décisions de l'auteur du 2026-11-22, 1 h 01 - 1 h 03 : collecteur oui, passage chaque nuit).

Chaque nuit, relit les flux RSS du bot (collecte_rss.FLUX) — titre, résumé et étiquettes de chaque article, jamais gardés
— et compte les termes VALIDÉS de listes/valides.txt (seuls les « + »). Un article n'est compté qu'une fois, même s'il
reste plusieurs jours dans son flux.

Ce que mesurent ces comptes : l'ATTENTION que des émetteurs portent à un compartiment, pas le phénomène (« les mots ne
mesurent rien »). Ils sont rangés par compartiment (rattachement validé), par émetteur (registre listes/emetteurs.txt)
et par jour (date de l'article).

Règles de comptage (simples, fixées ici) :
  - un terme compte au plus une fois par article (présence, pas fréquence) ;
  - frontières de mots strictes ; casse respectée pour les sigles (au moins deux majuscules, sans minuscule) et pour
    tout terme de 4 caractères ou moins (forme validée, ou avec sa première lettre en majuscule : « Fed » ne compte pas
    le verbe « fed ») ; insensible à la casse sinon ;
  - les codes du flux de l'USGS (ci, nc, us…) ne comptent que comme étiquettes des articles de l'USGS, jamais dans le texte
    (« us » est aussi un mot courant) ;
  - un terme dont la note dit « anglais seulement » (RSF, décision de 0 h 59) n'est compté que chez un émetteur dont la
    première langue au registre est l'anglais ;
  - un rattachement multiple (« a;b ») compte dans chaque compartiment ;
  - un article compte une fois par compartiment, même si plusieurs termes du compartiment y figurent.

Sorties (cumulées) :
  comptes/articles-par-compartiment.csv : jour, compartiment, emetteur, articles
  comptes/apparitions.csv               : jour, terme, compartiment, emetteur, lien  (la source de chaque compte)
  comptes/vus.txt                       : liens déjà comptés

    python compter.py <chemin vers metac-bot-template>
"""
from __future__ import annotations

import csv
import html
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from pathlib import Path

LISTES = Path("listes")
COMPTES = Path("comptes")
SIGLE = re.compile(r"^[A-Z0-9&.+\-]{2,8}$")


def lire_valides(chemin: Path = LISTES / "valides.txt") -> list[dict]:
    out = []
    for l in chemin.read_text(encoding="utf-8").splitlines():
        if not l.strip() or l.startswith("#"):
            continue
        ch = [x.strip() for x in l.split(" | ")]
        if len(ch) < 4 or ch[0] != "+" or not ch[1] or ch[3] in ("", "à identifier", "?"):
            continue
        note = ch[4] if len(ch) > 4 else ""
        out.append({"terme": ch[1], "places": [p for p in ch[3].split(";") if p],
                    "anglais_seul": "anglais seulement" in note,
                    "etiquette_seule": note.startswith("code de réseau sismique")})
    return out


def motif(terme: str) -> re.Pattern:
    sigle = bool(SIGLE.match(terme)) and sum(c.isupper() for c in terme) >= 2
    if sigle:
        return re.compile(r"(?<![\w])" + re.escape(terme) + r"(?![\w])")
    if len(terme) <= 4:
        formes = sorted({terme, terme[:1].upper() + terme[1:]})
        return re.compile(r"(?<![\w])(?:" + "|".join(map(re.escape, formes)) + r")(?![\w])")
    return re.compile(r"(?<![\w])" + re.escape(terme) + r"(?![\w])", re.IGNORECASE)


def lire_emetteurs(chemin: Path = LISTES / "emetteurs.txt") -> dict[str, str]:
    """domaine → première langue déclarée au registre."""
    out = {}
    if chemin.exists():
        for l in chemin.read_text(encoding="utf-8").splitlines():
            if l.strip() and not l.startswith("#"):
                ch = [x.strip() for x in l.split("|")]
                if len(ch) >= 7:
                    out[ch[0]] = ch[6].split()[0].strip(",;()").lower() if ch[6] else ""
    return out


def _date_iso(txt: str) -> str:
    from email.utils import parsedate_to_datetime
    txt = (txt or "").strip()
    try:
        return parsedate_to_datetime(txt).date().isoformat()
    except Exception:
        m = re.match(r"\d{4}-\d{2}-\d{2}", txt)
        return m.group(0) if m else ""


def articles(xml: bytes) -> list[dict]:
    """(lien, date, texte) de chaque article ; le texte (titre, résumé, étiquettes) ne sert qu'au comptage."""
    try:
        racine = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for item in racine.iter():
        if item.tag.split("}")[-1] not in ("item", "entry"):
            continue
        lien, quand, morceaux, etiq = "", "", [], []
        for el in item:
            nom = el.tag.split("}")[-1]
            if nom == "link":
                lien = lien or (el.get("href") or el.text or "").strip()
            elif nom in ("pubDate", "published", "updated", "date"):
                quand = quand or _date_iso(el.text or "")
            elif nom in ("title", "description", "summary"):
                morceaux.append(el.text or "")
            elif nom == "category":
                morceaux.append(el.get("term") or el.text or "")
                etiq.append((el.get("term") or el.text or "").strip())
        texte = re.sub(r"<[^>]+>", " ", html.unescape(" ".join(morceaux)))
        if lien:
            out.append({"lien": lien, "date": quand, "texte": texte, "etiquettes": etiq})
    return out


def compter_article(texte: str, emetteur: str, langue_em: str, termes: list[dict],
                    etiquettes: list[str] | None = None) -> dict[str, list[str]]:
    """compartiment → termes trouvés dans l'article."""
    trouves: dict[str, list[str]] = defaultdict(list)
    for t in termes:
        if t["anglais_seul"] and langue_em != "anglais":
            continue
        if t["etiquette_seule"]:
            present = emetteur == "earthquake.usgs.gov" and t["terme"] in (etiquettes or [])
        else:
            present = bool(t["re"].search(texte))
        if present:
            for p in t["places"]:
                if t["terme"] not in trouves[p]:
                    trouves[p].append(t["terme"])
    return trouves


def principal() -> int:
    bot = Path(sys.argv[1] if len(sys.argv) > 1 else "../metac-bot-template").resolve()
    sys.path.insert(0, str(bot))
    import httpx
    import politesse
    from collecte_rss import FLUX
    termes = lire_valides()
    for t in termes:
        t["re"] = motif(t["terme"])
    langues = lire_emetteurs()
    COMPTES.mkdir(exist_ok=True)
    vus_f = COMPTES / "vus.txt"
    vus = set(vus_f.read_text(encoding="utf-8").split()) if vus_f.exists() else set()
    nouveaux_vus, apparitions = [], []
    agregat: dict[tuple[str, str, str], int] = defaultdict(int)
    lus = 0
    with httpx.Client(headers={"User-Agent": politesse.UA.replace("metac-bot-template", "lecteur-local")},
                      timeout=30, follow_redirects=True) as c:
        for rubrique, urls in FLUX.items():
            for u in urls:
                if not politesse.autorise(c, u):
                    continue
                try:
                    r = c.get(u)
                    if r.status_code != 200:
                        continue
                except Exception:
                    continue
                for a in articles(r.content):
                    if a["lien"] in vus:
                        continue
                    vus.add(a["lien"])
                    nouveaux_vus.append(a["lien"])
                    lus += 1
                    em = re.sub(r"^www\.", "", a["lien"].split("/")[2]) if a["lien"].count("/") >= 2 else "?"
                    jour = a["date"] or date.today().isoformat()
                    for place, ts in compter_article(a["texte"], em, langues.get(em, ""), termes, a["etiquettes"]).items():
                        agregat[(jour, place, em)] += 1
                        for t in ts:
                            apparitions.append([jour, t, place, em, a["lien"]])
                time.sleep(0.5)
    # cumul
    f_agr = COMPTES / "articles-par-compartiment.csv"
    ancien: dict[tuple[str, str, str], int] = defaultdict(int)
    if f_agr.exists():
        with f_agr.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ancien[(r["jour"], r["compartiment"], r["emetteur"])] += int(r["articles"])
    for k, v in agregat.items():
        ancien[k] += v
    with f_agr.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["jour", "compartiment", "emetteur", "articles"])
        for k in sorted(ancien):
            w.writerow([*k, ancien[k]])
    f_app = COMPTES / "apparitions.csv"
    neuf = not f_app.exists()
    with f_app.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if neuf:
            w.writerow(["jour", "terme", "compartiment", "emetteur", "lien"])
        w.writerows(apparitions)
    with vus_f.open("a", encoding="utf-8") as f:
        f.write("".join(x + "\n" for x in nouveaux_vus))
    print(f"articles nouveaux lus : {lus} ; comptes ajoutés : {sum(agregat.values())} (article × compartiment) ; "
          f"apparitions de termes : {len(apparitions)} ; termes validés en jeu : {len(termes)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(principal())
