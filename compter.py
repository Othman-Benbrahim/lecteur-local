"""Collecteur sans modèle (décisions de l'auteur du 2026-11-22 : 1 h 03, passage chaque nuit ; 1 h 16, l'unité est la
SITUATION, pas le terme).

Décision de 1 h 16 : un terme isolé ne représente rien ; il a un sens dans un contexte précis, dans son écosystème, à
un temps T. Chaque article lu devient donc UNE situation datée qui relie, sans jamais garder le texte :
  - le temps : la date de l'article ;
  - l'espace : les pays, régions et lieux validés trouvés dans l'article ;
  - les acteurs présents ensemble (listes/acteurs.txt), chacun avec sa taille, sa facette et son niveau d'organisation ;
  - les compartiments touchés, chacun avec son niveau déclaré dans les fiches (espace, temps, vitesse, taille) ;
  - l'émetteur, avec son identité au registre (listes/emetteurs.txt : nature, pays, propriétaire, langues), dont la
    part de subjectivité et d'orientation se lit ensuite par comparaison entre émetteurs (ce qu'il dit, ce qu'il tait,
    les acteurs qu'il associe à un compartiment) ;
  - les termes trouvés, et le lien de l'article (la source).
La quantification se fait ensuite sur ces situations (acteur × compartiment × espace × émetteur × temps), pas sur des
termes détachés.

Règles de lecture (fixées ici) :
  - un terme compte au plus une fois par article ; frontières de mots strictes ;
  - casse respectée pour les sigles (au moins deux majuscules, sans minuscule) et pour tout terme de 4 caractères ou
    moins (forme validée, ou avec sa première lettre en majuscule : « Fed » ne compte pas le verbe « fed ») ;
  - les codes du flux de l'USGS (ci, nc, us…) ne comptent que comme étiquettes des articles de l'USGS, et les termes
    génériques marqués « étiquette seule » (langues de Global Voices, Health, Development, Justice… ; décision du
    2026-11-22, 2 h 45) seulement comme étiquettes posées par une rédaction, jamais dans le texte ;
  - un terme dont la note dit « anglais seulement » (RSF, décision de 0 h 59) ne compte que chez un émetteur dont la
    première langue au registre est l'anglais ;
  - un article sans aucun terme validé n'est pas enregistré (il est seulement noté comme lu).

Sorties : comptes/situations.jsonl (une ligne par article-situation, cumulée), comptes/vus.txt (liens déjà lus).

    python compter.py <chemin vers metac-bot-template>
"""
from __future__ import annotations

import csv
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
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
        out.append({"terme": ch[1], "langue": ch[2], "places": [p for p in ch[3].split(";") if p],
                    "anglais_seul": "anglais seulement" in note,
                    "etiquette_seule": note.startswith("code de réseau sismique") or "étiquette seule" in note,
                    "usgs": note.startswith("code de réseau sismique")})
    return out


def motif(terme: str) -> re.Pattern:
    sigle = bool(SIGLE.match(terme)) and sum(c.isupper() for c in terme) >= 2
    if sigle:
        return re.compile(r"(?<![\w])" + re.escape(terme) + r"(?![\w])")
    if len(terme) <= 4:
        formes = sorted({terme, terme[:1].upper() + terme[1:]})
        return re.compile(r"(?<![\w])(?:" + "|".join(map(re.escape, formes)) + r")(?![\w])")
    return re.compile(r"(?<![\w])" + re.escape(terme) + r"(?![\w])", re.IGNORECASE)


def lire_emetteurs(chemin: Path = LISTES / "emetteurs.txt") -> dict[str, dict]:
    """domaine → identité au registre (nom, nature, pays, propriétaire, langues, première langue)."""
    out = {}
    if chemin.exists():
        for l in chemin.read_text(encoding="utf-8").splitlines():
            if l.strip() and not l.startswith("#"):
                ch = [x.strip() for x in l.split("|")]
                if len(ch) >= 7:
                    out[ch[0]] = {"nom": ch[1], "nature": ch[2], "pays": ch[3], "proprietaire": ch[4],
                                  "langues": ch[6],
                                  "langue1": ch[6].split()[0].strip(",;()").lower() if ch[6] else ""}
    return out


def lire_acteurs(chemin: Path = LISTES / "acteurs.txt") -> dict[str, dict]:
    out = {}
    if chemin.exists():
        for l in chemin.read_text(encoding="utf-8").splitlines():
            if l.strip() and not l.startswith("#"):
                ch = [x.strip() for x in l.split(" | ")]
                if len(ch) >= 7:
                    out[ch[0]] = {"acteur": ch[2], "taille": ch[3], "facette": ch[4], "organisation": ch[5]}
    return out


def lire_niveaux(chemin: Path = LISTES / "inventaire-fiches.csv") -> dict[str, dict]:
    """fiche:compartiment → niveau d'action déclaré dans la fiche (espace, temps, vitesse, taille, organisation)."""
    out = {}
    with chemin.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = f'{r["fiche"]}:{r["compartiment"]}'
            if k not in out and r["sorte"] == "element":
                out[k] = {"espace": r["espace"], "temps": r["temps"], "vitesse": r["vitesse"], "taille": r["taille"],
                          "organisation": r["organisation"]}
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


MOT = re.compile(r"\w+")
_INDEX: dict[int, dict[str, list[dict]]] = {}


def _index(termes: list[dict]) -> dict[str, list[dict]]:
    """Premier mot (en minuscules) → termes qui commencent par ce mot. Évite de tester chaque terme sur chaque texte
    (2026-11-22 : l'exploration GDELT, 1 200 termes × 200 000 titres, ne finissait pas en 25 minutes)."""
    k = id(termes)
    if k not in _INDEX:
        ix: dict[str, list[dict]] = {}
        for t in termes:
            m = MOT.search(t["terme"])
            ix.setdefault(m.group(0).lower() if m else "", []).append(t)
        _INDEX[k] = ix
    return _INDEX[k]


def situation(texte: str, langue_em: str, termes: list[dict], etiquettes: list[str] | None,
              acteurs: dict[str, dict], niveaux: dict[str, dict], emetteur: str = "") -> dict | None:
    """Ce que l'article relie : espace, acteurs, compartiments (avec leur niveau), termes. None s'il n'y a rien."""
    espace, comps, acts, vus_termes = set(), {}, {}, []
    ix = _index(termes)
    mots = {w.lower() for w in MOT.findall(texte)}
    candidats = [t for w in mots for t in ix.get(w, [])] + ix.get("", [])
    for t in candidats:
        if t["anglais_seul"] and langue_em != "anglais":
            continue
        if t["etiquette_seule"]:
            continue                      # traité à part (codes de l'USGS)
        if not t["re"].search(texte):
            continue
        vus_termes.append(t["terme"])
        for p in t["places"]:
            if p.startswith("espace:"):
                espace.add(p.split(":", 1)[1])
            elif p.startswith("émetteur:"):
                continue
            else:
                c = comps.setdefault(p, {"compartiment": p, "niveau": niveaux.get(p, {}), "termes": []})
                if t["terme"] not in c["termes"]:
                    c["termes"].append(t["terme"])
        a = acteurs.get(t["terme"])
        if a:
            lst = acts.setdefault(a["acteur"], {**a, "termes": []})["termes"]
            if t["terme"] not in lst:
                lst.append(t["terme"])
    for t in termes:                      # étiquettes seules (USGS)
        if t["etiquette_seule"] and t["terme"] in (etiquettes or []) and (not t["usgs"] or emetteur == "earthquake.usgs.gov"):
            vus_termes.append(t["terme"])
            for p in t["places"]:
                lst = comps.setdefault(p, {"compartiment": p, "niveau": niveaux.get(p, {}), "termes": []})["termes"]
                if t["terme"] not in lst:
                    lst.append(t["terme"])
    if not vus_termes:
        return None
    return {"espace": sorted(espace), "acteurs": sorted(acts.values(), key=lambda x: x["acteur"]),
            "compartiments": sorted(comps.values(), key=lambda x: x["compartiment"]), "termes": sorted(set(vus_termes))}


def principal() -> int:
    bot = Path(sys.argv[1] if len(sys.argv) > 1 else "../metac-bot-template").resolve()
    sys.path.insert(0, str(bot))
    import httpx
    import politesse
    from collecte_rss import FLUX
    termes = lire_valides()
    for t in termes:
        t["re"] = motif(t["terme"])
    emetteurs = lire_emetteurs()
    acteurs = lire_acteurs()
    niveaux = lire_niveaux()
    COMPTES.mkdir(exist_ok=True)
    vus_f = COMPTES / "vus.txt"
    vus = set(vus_f.read_text(encoding="utf-8").split()) if vus_f.exists() else set()
    nouveaux_vus, situations = [], []
    lus = 0
    releve = date.today().isoformat()
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
                    ident = emetteurs.get(em, {})
                    s_ = situation(a["texte"], ident.get("langue1", ""), termes, a["etiquettes"], acteurs, niveaux, em)
                    if s_ is None:
                        continue
                    situations.append({"jour": a["date"] or releve, "date_article_connue": bool(a["date"]),
                                       "releve": releve, "lien": a["lien"], "rubrique": rubrique,
                                       "emetteur": {"domaine": em, **{k: v for k, v in ident.items() if k != "langue1"},
                                                    "identifie": bool(ident)},
                                       **s_})
                time.sleep(0.5)
    with (COMPTES / "situations.jsonl").open("a", encoding="utf-8") as f:
        f.write("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in situations))
    with vus_f.open("a", encoding="utf-8") as f:
        f.write("".join(x + "\n" for x in nouveaux_vus))
    print(f"articles nouveaux lus : {lus} ; situations enregistrées : {len(situations)} ; "
          f"avec au moins un acteur : {sum(1 for x in situations if x['acteurs'])} ; avec un espace : "
          f"{sum(1 for x in situations if x['espace'])} ; termes validés en jeu : {len(termes)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(principal())
