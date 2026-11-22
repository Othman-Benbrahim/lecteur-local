"""Ontologie, étape 2 (décision de l'auteur du 2026-11-22, 1 h 35) : quels TYPES DE SITUATION (graphe tiré des règles
coutumières du bot, ontologie.py) les situations observées réalisent-elles, jour par jour, chez quels émetteurs ?

Règle de réalisation (révisée le 2026-11-22, 2 h 45, après l'exploration GDELT : le seul compartiment était trop
grossier, toute mention de Trump activait trois règles) : une situation observée réalise le type de situation d'une
règle si
  1. elle touche AU MOINS DEUX compartiments de la règle, OU un compartiment de la règle et un de ses indices (champ
     « indices » de la règle : termes propres à la situation, validés par l'auteur) ; une règle à un seul compartiment
     et sans indices n'est donc jamais réalisée (elle attend ses indices) ;
  2. et son espace est compatible avec le périmètre de la règle : « monde » accepte tout ; un pays exige ce pays dans
     l'espace de l'article ; un continent (europe, afrique…) exige un pays de ce continent ou la région nommée.
Une règle sans compartiment ne peut être réalisée (elle est listée comme manque par ontologie.py).
Ce qui est mesuré : qu'un type de situation est PRÉSENT dans ce que disent les émetteurs, pas qu'il est vrai.

Sortie (recalculée en entier à chaque passage, pour suivre les changements de règles) :
  comptes/types-actifs.csv : jour, type de situation, règle, articles, émetteurs, acteurs les plus cités, liens (5 au plus)

    python realiser.py <chemin vers metac-bot-template>
"""
from __future__ import annotations

import csv
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

PAYS_MONDE = "https://raw.githubusercontent.com/mledoze/countries/master/countries.json"   # ODbL ; régions et noms fr
CONTINENTS = {"europe": "Europe", "afrique": "Africa", "asie": "Asia", "oceanie": "Oceania", "ameriques": "Americas",
              "amerique_du_nord": "Americas", "amerique_latine": "Americas"}
REGIONS_NOMMEES = {"europe": ("europe",), "afrique": ("afrique", "afrique subsaharienne"),
                   "asie": ("asie",), "ameriques": ("ameriques", "amerique latine", "amerique du nord")}


def cle(t: str) -> str:
    t = "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def continents_des_pays() -> dict[str, str]:
    import httpx
    try:
        data = httpx.get(PAYS_MONDE, timeout=60).json()
    except Exception as e:
        print(f"table des pays indisponible : {type(e).__name__} ; seuls « monde » et les pays nommés seront reconnus")
        return {}
    out = {}
    for p in data:
        noms = {p.get("name", {}).get("common", ""), p.get("translations", {}).get("fra", {}).get("common", ""),
                p.get("translations", {}).get("fra", {}).get("official", "")}
        for n in noms:
            if n:
                out[cle(n)] = p.get("region", "")
    return out


def compatible(espace: list[str], perimetre: list[str], continent_de: dict[str, str]) -> bool:
    if not perimetre or any(cle(p) == "monde" for p in perimetre):
        return True
    pays = [cle(e.split(":", 1)[1]) for e in espace if e.startswith("pays:")]
    regions = [cle(e.split(":", 1)[1]) for e in espace if e.startswith("région:")]
    for p in perimetre:
        k = cle(p).replace(" ", "_")
        if k in CONTINENTS:
            if any(continent_de.get(x) == CONTINENTS[k] for x in pays):
                return True
            if any(r in REGIONS_NOMMEES.get(k, ()) for r in regions):
                return True
        elif cle(p) in pays:
            return True
    return False


def realise(s: dict, regle: dict, continent_de: dict[str, str]) -> bool:
    comps = {c["compartiment"] if isinstance(c, dict) else c for c in s.get("compartiments", [])}
    communs = comps & set(regle["compartiments"])
    if not communs:
        return False
    indices = {i.lower() for i in regle.get("indices") or []}
    if len(communs) < 2 and not (indices & {t.lower() for t in s.get("termes", [])}):
        return False
    return compatible(s.get("espace", []), regle["perimetre"], continent_de)


def principal() -> int:
    bot = Path(sys.argv[1] if len(sys.argv) > 1 else "../metac-bot-template").resolve()
    sys.path.insert(0, str(bot))
    import yaml
    from ontologie import graphe
    g = graphe(yaml.safe_load((bot / "ecosystemes/regles-coutumieres.yaml").read_text(encoding="utf-8"))["regles"])
    noms = {n["id"]: n["texte"] for n in g["situations"]}
    regles = [a for a in g["aretes"] if a["compartiments"]]
    continent_de = continents_des_pays()
    f = Path("comptes/situations.jsonl")
    situations = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] if f.exists() else []
    agr: dict[tuple[str, str, str], dict] = defaultdict(lambda: {"articles": 0, "emetteurs": Counter(),
                                                                  "acteurs": Counter(), "liens": []})
    for s in situations:
        for a in regles:
            if realise(s, a, continent_de):
                x = agr[(s["jour"], noms[a["de"]], a["regle"])]
                x["articles"] += 1
                x["emetteurs"][s["emetteur"]["domaine"]] += 1
                x["acteurs"].update(ac["acteur"] for ac in s.get("acteurs", []))
                if len(x["liens"]) < 5:
                    x["liens"].append(s["lien"])
    Path("comptes").mkdir(exist_ok=True)
    with Path("comptes/types-actifs.csv").open("w", encoding="utf-8", newline="") as fo:
        w = csv.writer(fo)
        w.writerow(["jour", "type_de_situation", "regle", "articles", "emetteurs", "acteurs", "liens"])
        for (jour, typ, regle), x in sorted(agr.items()):
            w.writerow([jour, typ, regle, x["articles"],
                        "; ".join(f"{k} {v}" for k, v in x["emetteurs"].most_common()),
                        "; ".join(f"{k} {v}" for k, v in x["acteurs"].most_common(8)), " ".join(x["liens"])])
    print(f"situations lues : {len(situations)} ; règles reconnaissables : {len(regles)} sur {len(g['aretes'])} ; "
          f"lignes (jour × type × règle) : {len(agr)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(principal())
