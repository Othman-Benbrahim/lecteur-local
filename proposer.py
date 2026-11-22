"""Propositions de termes pour le lecteur sans modèle (décisions de l'auteur du 2026-11-21 / 22 : 23 h 59 - 0 h 01).

Les listes téléchargées ne font que PROPOSER ; l'auteur valide à la main (règle c). Le workflow écrit
listes/candidats.txt ; l'auteur le télécharge, marque chaque ligne, et le renvoie ; il devient listes/valides.txt,
seule liste que le collecteur comptera. Un terme déjà décidé (validé ou refusé) n'est jamais reproposé.

Format d'une ligne (texte, séparateur « | ») :
    ? | terme | langue | rattachement | source | vu dans N passages | depuis AAAA-MM-JJ | émetteurs
« ? » = à décider. Décision de l'auteur (0 h 05) : le tri (rattachement, refus motivés) est fait par Claude, l'auteur
valide et en discute.

Décision de l'auteur (0 h 16) : chaque terme est daté et sourcé, et les émetteurs d'information sont repérés et
identifiés (cadre écosystémique). D'où deux fichiers de plus :
  - listes/references.txt : chaque apparition d'un terme (terme | émetteur | rubrique | lien de l'article | date de
    l'article | date du relevé), cumulée de passage en passage, sans doublon (terme, lien) ; jamais le texte ;
  - listes/emetteurs-a-identifier.txt : les émetteurs vus qui ne figurent pas encore dans listes/emetteurs.txt
    (registre des émetteurs identifiés, tenu par Claude avec ses sources, validé par l'auteur).

Sources de ce premier passage :
  1. Wikidata (CC0), par son service de requêtes public : noms et alias, en anglais et en français, des acteurs DÉJÀ NOMMÉS dans les fiches (premier
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
SPARQL = "https://query.wikidata.org/sparql"
PAUSE_WIKIDATA = 10         # secondes entre deux requêtes (exception datée du 2026-11-22, 0 h 24 ; 10 s demandées à 0 h 34)
ENTETE = """# Termes proposés au lecteur sans modèle (fichier écrit par le workflow « proposer » ; ne pas éditer dans le dépôt).
# Marquer chaque ligne : « + » validé, « - » refusé, à la place du « ? ». Le rattachement peut être corrigé
# (fiche:compartiment:élément ou acteur, comme dans listes/inventaire-fiches.csv ; « ? » si inconnu).
# Format : décision | terme | langue | rattachement | source | vu dans N passages | depuis AAAA-MM-JJ | émetteurs
# Chaque apparition (émetteur, lien de l'article, date) : listes/references.txt
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
            out.append(champs + [""] * (8 - len(champs)))
    return out


def cle(terme: str, langue: str) -> tuple[str, str]:
    return (terme.strip().lower(), langue.strip().lower())


def nettoyer_nom(nom: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", nom).strip()


# ------------------------------------------------------------------------------------------------- 1. Wikidata
REQUETE = """SELECT ?item ?n ?desc WHERE {
  SERVICE wikibase:mwapi { bd:serviceParam wikibase:api "EntitySearch" ; wikibase:endpoint "www.wikidata.org" ;
    mwapi:search "%s" ; mwapi:language "fr" . ?item wikibase:apiOutputItem mwapi:item . ?ord wikibase:apiOrdinal true . }
  FILTER(?ord = 0)
  ?item rdfs:label|skos:altLabel ?n . FILTER(LANG(?n) = "en" || LANG(?n) = "fr")
  OPTIONAL { ?item schema:description ?desc . FILTER(LANG(?desc) = "fr") }
}"""


def wikidata(c, politesse, acteurs: list[dict]) -> list[tuple[str, str, str, str]]:
    """Service de requêtes public de Wikidata (query.wikidata.org). Son robots.txt refuse les robots ; EXCEPTION datée,
    décision de l'auteur du 2026-11-22, 0 h 24 : « je valide, mais avec un intervalle raisonnable, il faut respecter
    Wikidata ». D'où : requêtes une par une, jamais en parallèle, PAUSE_WIKIDATA secondes entre deux, identité claire
    (User-Agent avec l'adresse du dépôt), et respect de l'en-tête Retry-After en cas de refus (429) ; au troisième refus,
    on s'arrête pour ce passage. Cette exception ne vaut que pour ce service."""
    refus = 0
    out, vus = [], {}
    for a in acteurs:
        nom = nettoyer_nom(a["texte"])
        if not nom:
            continue
        place = f'{a["fiche"]}:{a["compartiment"]}:{a["texte"]}'
        if nom.lower() not in vus:
            time.sleep(PAUSE_WIKIDATA)
            try:
                r = c.get(SPARQL, params={"query": REQUETE % nom.replace("\\", " ").replace('"', " "), "format": "json"},
                          headers={"Accept": "application/sparql-results+json"})
                if r.status_code == 429:
                    refus += 1
                    attente = int(r.headers.get("Retry-After", "60") or 60)
                    print(f"Wikidata : refus 429, attente de {attente} s", flush=True)
                    if refus >= 3:
                        print("Wikidata : trois refus, arrêt pour ce passage", flush=True)
                        break
                    time.sleep(attente)
                    r = c.get(SPARQL, params={"query": REQUETE % nom.replace("\\", " ").replace('"', " "), "format": "json"},
                              headers={"Accept": "application/sparql-results+json"})
                r.raise_for_status()
                vus[nom.lower()] = r.json()["results"]["bindings"]
            except Exception as e:
                print(f"Wikidata « {nom} » : {type(e).__name__}: {e}"[:200], flush=True)
                vus[nom.lower()] = []
        res = vus[nom.lower()]
        if not res:
            print(f"Wikidata « {nom} » : aucun résultat", flush=True)
            continue
        qid = res[0]["item"]["value"].rsplit("/", 1)[-1]
        desc = next((b["desc"]["value"] for b in res if "desc" in b), "")
        source = f"wikidata:{qid} ({desc[:60]})" if desc else f"wikidata:{qid}"
        for b in res:
            n, lg = b["n"]["value"], b["n"].get("xml:lang", "")
            if n and "|" not in n and lg in ("en", "fr"):
                out.append((n, lg, place, source, "wikidata.org", "wikidata", f"https://www.wikidata.org/wiki/{qid}",
                            date.today().isoformat()))
    return out


# ------------------------------------------------------------------------------------------- 2. Étiquettes RSS
def _date_iso(txt: str) -> str:
    from email.utils import parsedate_to_datetime
    txt = (txt or "").strip()
    if not txt:
        return ""
    try:
        return parsedate_to_datetime(txt).date().isoformat()
    except Exception:
        m = re.match(r"\d{4}-\d{2}-\d{2}", txt)
        return m.group(0) if m else ""


def etiquettes(xml: bytes) -> list[tuple[str, str, str]]:
    """Étiquettes par article : (étiquette, lien de l'article, date de l'article)."""
    try:
        racine = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for item in racine.iter():
        if item.tag.split("}")[-1] not in ("item", "entry"):
            continue
        lien, quand, cats = "", "", []
        for el in item:
            nom = el.tag.split("}")[-1]
            if nom == "link":
                lien = lien or (el.get("href") or el.text or "").strip()
            elif nom in ("pubDate", "published", "updated", "date"):
                quand = quand or _date_iso(el.text or "")
            elif nom == "category":
                t = (el.get("term") or el.text or "").strip()
                if t and len(t) <= 80 and "|" not in t and not t.startswith("http"):
                    cats.append(t)
        out += [(t, lien.replace("|", "%7C"), quand) for t in dict.fromkeys(cats)]
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
            for t, lien, quand in etiquettes(r.content):
                # l'émetteur est le site de l'article (un relais comme FeedBurner n'est pas un émetteur)
                em = re.sub(r"^www\.", "", lien.split("/")[2]) if lien.startswith("http") and lien.count("/") >= 2 else dom
                out.append((t, "?", connus.get(t.lower(), "?"), f"étiquette:{rubrique}:{em}", em, rubrique, lien, quand))
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
    termes_decides = {k[0] for k in decides}

    def deja_decide(k: tuple[str, str]) -> bool:
        # une étiquette RSS arrive sans langue (« ? ») : décidée si le terme l'est, quelle que soit la langue notée au tri
        return k in decides or (k[1] == "?" and k[0] in termes_decides)
    anciens = {cle(l[1], l[2]): l for l in lire_lignes(CANDIDATS) if l[0] == "?" and not deja_decide(cle(l[1], l[2]))}
    aujourdhui = date.today().isoformat()
    vus_ce_passage: dict[tuple[str, str], list] = {}
    with httpx.Client(headers={"User-Agent": politesse.UA.replace("metac-bot-template", "lecteur-local")},
                      timeout=30, follow_redirects=True) as c:
        nommes = [l for l in inv if l["sorte"] == "acteur" and l["generique"] == "non"]
        props = wikidata(c, politesse, nommes) + flux_rss(c, politesse, FLUX, connus)
    REFS = LISTES / "references.txt"
    refs = {}
    if REFS.exists():
        for l in REFS.read_text(encoding="utf-8").splitlines():
            ch = [x.strip() for x in l.split("|")]
            if len(ch) >= 6 and not l.startswith("#"):
                refs[(ch[0].lower(), ch[3])] = ch[:6]
    emetteurs_par_terme: dict[str, set] = {}
    for terme, lg, place, source, emetteur, rubrique, lien, quand in props:
        if (terme.lower(), lien) not in refs:
            refs[(terme.lower(), lien)] = [terme, emetteur, rubrique, lien, quand, aujourdhui]
    for ch in refs.values():
        emetteurs_par_terme.setdefault(ch[0].lower(), set()).add(ch[1])
    LISTES.mkdir(exist_ok=True)
    REFS.write_text("# terme | émetteur | rubrique | lien de l'article | date de l'article | date du relevé\n" + "\n".join(
        " | ".join(ch) for ch in sorted(refs.values(), key=lambda c: (c[0].lower(), c[4], c[3]))) + "\n", encoding="utf-8")
    connus_em = set()
    if (LISTES / "emetteurs.txt").exists():
        connus_em = {l.split("|")[0].strip() for l in (LISTES / "emetteurs.txt").read_text(encoding="utf-8").splitlines()
                     if l.strip() and not l.startswith("#")}
    a_identifier = sorted({ch[1] for ch in refs.values()} - connus_em)
    (LISTES / "emetteurs-a-identifier.txt").write_text(
        "# émetteurs vus, absents de listes/emetteurs.txt\n" + "\n".join(a_identifier) + "\n", encoding="utf-8")
    for terme, lg, place, source, *_ in props:
        k = cle(terme, lg)
        if deja_decide(k):
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
        f"? | {l[1]} | {l[2]} | {l[3]} | {l[4]} | vu dans {l[5]} passages | depuis {l[6]} | "
        f"{', '.join(sorted(emetteurs_par_terme.get(l[1].lower(), [])))}" for l in ordre) + "\n", encoding="utf-8")
    wd = sum(1 for l in ordre if l[4].startswith("wikidata"))
    print(f"candidats : {len(ordre)} à décider ({wd} venus de Wikidata, {len(ordre) - wd} d'étiquettes RSS) ; "
          f"{len(decides)} termes déjà décidés écartés", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(principal())
