"""Propositions de termes pour le lecteur sans modèle (décisions de l'auteur du 2026-11-21 / 22 : 23 h 59 - 0 h 01).

Les listes téléchargées ne font que PROPOSER ; l'auteur valide à la main (règle c). Le workflow écrit
listes/candidats.txt ; l'auteur le télécharge, marque chaque ligne, et le renvoie ; il devient listes/valides.txt,
seule liste que le collecteur comptera. Un terme déjà décidé (validé ou refusé) n'est jamais reproposé.

Format d'une ligne (texte, séparateur « | ») :
    ? | terme | langue | rattachement | source | vu dans N passages | depuis AAAA-MM-JJ
« ? » = à décider ; l'auteur le remplace par « + » (validé) ou « - » (refusé), et peut corriger le rattachement
(fiche:compartiment:élément ou acteur, tel qu'écrit dans listes/inventaire-fiches.csv).

Sources de ce premier passage :
  1. Wikidata (CC0) : noms et alias, en anglais et en français, des acteurs DÉJÀ NOMMÉS dans les fiches (premier
     résultat de la recherche Wikidata, identifiant et description donnés pour que l'auteur vérifie que c'est le bon).
  2. Étiquettes (<category>) posées par les rédactions dans les flux RSS du bot (collecte_rss.FLUX).
Les types génériques, les forces non humaines et les éléments (thésaurus) viendront dans des passages suivants.

    python proposer.py <chemin vers metac-bot-template>
"""
from __future__ import annotations

import csv
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

LISTES = Path("listes")
CANDIDATS = LISTES / "candidats.txt"
VALIDES = LISTES / "valides.txt"
WD_API = "https://www.wikidata.org/w/api.php"
ENTETE = """# Termes proposés au lecteur sans modèle (fichier écrit par le workflow « proposer » ; ne pas éditer dans le dépôt).
# Marquer chaque ligne : « + » validé, « - » refusé, à la place du « ? ». Le rattachement peut être corrigé
# (fiche:compartiment:élément ou acteur, comme dans listes/inventaire-fiches.csv ; « ? » si inconnu).
# Format : décision | terme | langue | rattachement | source | vu dans N passages | depuis AAAA-MM-JJ
"""


def lire_lignes(chemin: Path) -> list[list[str]]:
    if not chemin.exists():
        return []
    out = []
    for l in chemin.read_text(encoding="utf-8").splitlines():
        if not l.strip() or l.lstrip().startswith("#"):
            continue
        champs = [x.strip() for x in l.split("|")]
        if len(champs) >= 3:
            out.append(champs + [""] * (7 - len(champs)))
    return out


def cle(terme: str, langue: str) -> tuple[str, str]:
    return (terme.strip().lower(), langue.strip().lower())


def nettoyer_nom(nom: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", nom).strip()


# ------------------------------------------------------------------------------------------------- 1. Wikidata
def wikidata(c, politesse, acteurs: list[dict]) -> list[tuple[str, str, str, str]]:
    if not politesse.autorise(c, WD_API + "?action=wbsearchentities"):
        print("Wikidata : robots.txt refuse l'API, rien proposé", flush=True)
        return []
    out, vus = [], {}
    for a in acteurs:
        nom = nettoyer_nom(a["texte"])
        if not nom:
            continue
        place = f'{a["fiche"]}:{a["compartiment"]}:{a["texte"]}'
        if nom.lower() not in vus:
            time.sleep(1)
            try:
                r = c.get(WD_API, params={"action": "wbsearchentities", "search": nom, "language": "fr",
                                          "uselang": "fr", "type": "item", "limit": 1, "format": "json"})
                res = r.json().get("search", [])
            except Exception as e:
                print(f"Wikidata « {nom} » : {type(e).__name__}"[:200], flush=True)
                res = []
            vus[nom.lower()] = res[0] if res else None
        ent = vus[nom.lower()]
        if not ent:
            print(f"Wikidata « {nom} » : aucun résultat", flush=True)
            continue
        qid = ent["id"]
        try:
            time.sleep(1)
            d = c.get(WD_API, params={"action": "wbgetentities", "ids": qid, "props": "labels|aliases|descriptions",
                                      "languages": "en|fr", "format": "json"}).json()["entities"][qid]
        except Exception as e:
            print(f"Wikidata {qid} : {type(e).__name__}"[:200], flush=True)
            continue
        desc = (d.get("descriptions", {}).get("fr") or d.get("descriptions", {}).get("en") or {}).get("value", "")
        source = f"wikidata:{qid} ({desc[:60]})" if desc else f"wikidata:{qid}"
        for lg in ("en", "fr"):
            noms = [d.get("labels", {}).get(lg, {}).get("value")] + [x["value"] for x in d.get("aliases", {}).get(lg, [])]
            for n in noms:
                if n and "|" not in n:
                    out.append((n, lg, place, source))
    return out


# ------------------------------------------------------------------------------------------- 2. Étiquettes RSS
def etiquettes(xml: bytes) -> list[str]:
    try:
        racine = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for el in racine.iter():
        if el.tag.split("}")[-1] != "category":
            continue
        t = (el.get("term") or el.text or "").strip()
        if t and len(t) <= 80 and "|" not in t and not t.startswith("http"):
            out.append(t)
    return out


def flux_rss(c, politesse, flux: dict, connus: dict[str, str]) -> list[tuple[str, str, str, str]]:
    out = []
    for rubrique, urls in flux.items():
        for u in urls:
            if not politesse.autorise(c, u):
                continue
            try:
                r = c.get(u)
                if r.status_code != 200:
                    continue
            except Exception:
                continue
            dom = re.sub(r"^www\.", "", u.split("/")[2])
            for t in etiquettes(r.content):
                out.append((t, "?", connus.get(t.lower(), "?"), f"étiquette:{rubrique}:{dom}"))
            time.sleep(0.5)
    return out


def principal() -> int:
    bot = Path(sys.argv[1] if len(sys.argv) > 1 else "../metac-bot-template").resolve()
    sys.path.insert(0, str(bot))
    import httpx
    import politesse
    from collecte_rss import FLUX
    with (LISTES / "inventaire-fiches.csv").open(encoding="utf-8") as f:
        inv = list(csv.DictReader(f))
    connus = {l["texte"].lower(): f'{l["fiche"]}:{l["compartiment"]}:{l["texte"]}' for l in inv}
    decides = {cle(l[1], l[2]) for l in lire_lignes(VALIDES) if l[0] in ("+", "-")}
    anciens = {cle(l[1], l[2]): l for l in lire_lignes(CANDIDATS) if l[0] == "?" and cle(l[1], l[2]) not in decides}
    aujourdhui = date.today().isoformat()
    vus_ce_passage: dict[tuple[str, str], list] = {}
    with httpx.Client(headers={"User-Agent": politesse.UA.replace("metac-bot-template", "lecteur-local")},
                      timeout=30, follow_redirects=True) as c:
        nommes = [l for l in inv if l["sorte"] == "acteur" and l["generique"] == "non"]
        props = wikidata(c, politesse, nommes) + flux_rss(c, politesse, FLUX, connus)
    for terme, lg, place, source in props:
        k = cle(terme, lg)
        if k in decides:
            continue
        if k in vus_ce_passage:                       # compté une fois par passage
            continue
        vus_ce_passage[k] = ["?", terme, lg, place, source, 1, aujourdhui]
    lignes = {}
    for k, l in anciens.items():
        n = int(re.sub(r"\D", "", l[5]) or 0)
        lignes[k] = ["?", l[1], l[2], l[3], l[4], n, l[6].replace("depuis", "").strip() or aujourdhui]
    for k, l in vus_ce_passage.items():
        if k in lignes:
            lignes[k][5] += l[5]
            if lignes[k][3] == "?" and l[3] != "?":
                lignes[k][3] = l[3]
        else:
            lignes[k] = l
    LISTES.mkdir(exist_ok=True)
    ordre = sorted(lignes.values(), key=lambda l: (l[4].split(":")[0], l[3] == "?", -l[5], l[1].lower()))
    CANDIDATS.write_text(ENTETE + "\n".join(
        f"? | {l[1]} | {l[2]} | {l[3]} | {l[4]} | vu dans {l[5]} passages | depuis {l[6]}" for l in ordre) + "\n", encoding="utf-8")
    wd = sum(1 for l in ordre if l[4].startswith("wikidata"))
    print(f"candidats : {len(ordre)} à décider ({wd} venus de Wikidata, {len(ordre) - wd} d'étiquettes RSS) ; "
          f"{len(decides)} termes déjà décidés écartés", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(principal())
