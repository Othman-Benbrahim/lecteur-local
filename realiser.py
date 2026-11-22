"""Ontologie, étape 2 (décision de l'auteur du 2026-11-22, 1 h 35) : quels TYPES DE SITUATION (graphe tiré des règles
coutumières du bot, ontologie.py) les situations observées réalisent-elles, jour par jour, chez quels émetteurs ?

Règle de réalisation (révisée le 2026-11-22, 2 h 45, après l'exploration GDELT : le seul compartiment était trop
grossier, toute mention de Trump activait trois règles) : une situation observée réalise le type de situation d'une
règle si
  0. (2026-11-22, 12 h 16) indices combinables : « a + b » exige a ET b, « a | b » a OU b ; champ « exclure » ;
     champs « periodes » [[début, fin]] et « mois » [1-12] pour une situation datée ;
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
    # 2026-11-22, 6 h 38 (décision de l'auteur, après la première collecte) : un indice de la règle est désormais EXIGÉ ;
    # deux compartiments communs ne suffisent plus (96 réalisations sur 172 n'avaient aucun indice, dont une vérification
    # sur une manifestation allemande comptée comme « contestation contre un régime »)
    termes = {t.lower() for t in s.get("termes", [])}
    remplis = [i for i in regle.get("indices") or [] if indice_rempli(i, termes)]
    if not remplis:
        return False
    # indice spécifique : combiné (« a + b ») ou expression de plusieurs mots (« coup d'État », « former president ») ;
    # un mot seul et générique (« protest », « summit ») ne l'est pas
    s["_combine"] = any(" + " in i or " " in i.strip() for i in remplis)
    # 2026-11-22, 12 h 16 (correction après le test 1 : 46,6 % de détections justes) : un terme d'exclusion annule la
    # réalisation ; une situation datée (périodes, mois) n'est réalisée que dans sa fenêtre
    if any(indice_rempli(x, termes) for x in regle.get("exclure") or []):
        return False
    jour = str(s.get("jour", ""))
    if regle.get("periodes") and not any(str(a) <= jour <= str(b) for a, b in regle["periodes"]):
        return False
    if regle.get("mois") and jour[5:7].isdigit() and int(jour[5:7]) not in regle["mois"]:
        return False
    return compatible(s.get("espace", []), regle["perimetre"], continent_de)


# 2026-11-22, 16 h 26 (décision de l'auteur) : signaux forts et signaux faibles. Seuils de départ, non démontrés, à
# ajuster une seule fois d'après le journal de suivi.
FORT_ARTICLES, FORT_EMETTEURS, FORT_JOURS = 3, 2, 7
RARETE_JOURS = 30
ENJEU_REGLES = {"chefs_d_etat_sortie_irreguliere", "coups_d_etat_frequence", "regimes_fermes_contestation"}


def signaux(agr: dict, regles: dict, sortie: Path = Path("comptes")) -> list[dict]:
    """Classe chaque (jour, règle) réalisé : FORT si, sur les 7 jours finissant ce jour-là, au moins 3 articles de 2
    émetteurs ; FAIBLE si isolé mais rare (rien sur cette règle dans les 30 jours d'avant), spécifique (indice combiné),
    à fort enjeu (événement rare) et d'un émetteur identifié qui n'est pas un agrégateur ; BRUIT sinon (gardé, masqué).
    Écrit comptes/signaux.csv (recalculé), ajoute les signaux faibles nouveaux à comptes/signaux-faibles-journal.csv
    (ajout seul, jamais réécrit) et recalcule comptes/signaux-faibles-suivi.csv (devenu fort dans les 30 jours ?)."""
    from datetime import date, timedelta
    sys.path.insert(0, str(Path(__file__).parent))
    from compter import lire_emetteurs
    em = lire_emetteurs()
    def primaire(d):
        e = em.get(d)
        return bool(e) and "agrégateur" not in (e["nature"] + e.get("proprietaire", "")).lower()
    par_regle: dict[str, dict] = defaultdict(dict)
    for (jour, typ, regle), x in agr.items():
        par_regle[regle][jour] = (typ, x)
    lignes = []
    for regle, jours in par_regle.items():
        r = regles.get(regle, {})
        enjeu = r.get("structure") == "evenement_rare" or regle in ENJEU_REGLES
        for jour, (typ, x) in sorted(jours.items()):
            j = date.fromisoformat(jour)
            fen = [(j - timedelta(days=k)).isoformat() for k in range(FORT_JOURS)]
            art = sum(jours[d][1]["articles"] for d in fen if d in jours)
            ems = set().union(*[set(jours[d][1]["emetteurs"]) for d in fen if d in jours])
            avant = [(j - timedelta(days=k)).isoformat() for k in range(1, RARETE_JOURS + 1)]
            crit = {"rare": not any(d in jours for d in avant), "specifique": bool(x.get("combine")),
                    "enjeu": enjeu, "emetteur_primaire": any(primaire(d) for d in x["emetteurs"])}
            if art >= FORT_ARTICLES and len(ems) >= FORT_EMETTEURS:
                classe = "fort"
            elif all(crit.values()):
                classe = "faible"
            else:
                classe = "bruit"
            lignes.append({"jour": jour, "regle": regle, "type_de_situation": typ, "classe": classe,
                           "articles_7j": art, "emetteurs_7j": len(ems),
                           "criteres": " ".join(k for k, v in crit.items() if v), "liens": " ".join(x["liens"])})
    lignes.sort(key=lambda l: (l["jour"], l["regle"]))
    sortie.mkdir(exist_ok=True)
    champs = ["jour", "regle", "type_de_situation", "classe", "articles_7j", "emetteurs_7j", "criteres", "liens"]
    with (sortie / "signaux.csv").open("w", encoding="utf-8", newline="") as fo:
        w = csv.DictWriter(fo, fieldnames=champs); w.writeheader(); w.writerows(lignes)
    # journal des signaux faibles : ajout seul
    jf = sortie / "signaux-faibles-journal.csv"
    deja = set()
    if jf.exists():
        deja = {(r["jour"], r["regle"]) for r in csv.DictReader(jf.open(encoding="utf-8"))}
    neufs = [l for l in lignes if l["classe"] == "faible" and (l["jour"], l["regle"]) not in deja]
    if neufs:
        nouveau = not jf.exists()
        with jf.open("a", encoding="utf-8", newline="") as fo:
            w = csv.DictWriter(fo, fieldnames=["inscrit_le"] + champs)
            if nouveau:
                w.writeheader()
            for l in neufs:
                w.writerow({"inscrit_le": date.today().isoformat(), **l})
    # suivi : le signal faible est-il devenu fort dans les 30 jours ?
    forts = defaultdict(list)
    for l in lignes:
        if l["classe"] == "fort":
            forts[l["regle"]].append(l["jour"])
    if jf.exists():
        with (sortie / "signaux-faibles-suivi.csv").open("w", encoding="utf-8", newline="") as fo:
            w = csv.writer(fo)
            w.writerow(["jour", "regle", "type_de_situation", "devenu_fort_le", "echeance_30j", "statut"])
            for r in csv.DictReader(jf.open(encoding="utf-8")):
                j = date.fromisoformat(r["jour"]); fin = (j + timedelta(days=30)).isoformat()
                f = [d for d in forts[r["regle"]] if r["jour"] < d <= fin]
                statut = "devenu fort" if f else ("en attente" if date.today().isoformat() <= fin else "resté isolé")
                w.writerow([r["jour"], r["regle"], r["type_de_situation"], f[0] if f else "", fin, statut])
    from collections import Counter as _C
    print("signaux :", dict(_C(l["classe"] for l in lignes)), f"; faibles nouveaux au journal : {len(neufs)}", flush=True)
    return lignes


def indice_rempli(indice: str, termes: set[str]) -> bool:
    """« a + b » : a ET b ; « a | b » : a OU b ; les deux se combinent (« a | b + c »). Comparaison sans casse, sur les
    termes validés relevés dans l'article (un mot isolé ne suffit plus quand la règle demande une combinaison)."""
    return all(any(alt.strip().lower() in termes for alt in part.split("|")) for part in indice.split(" + "))


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
                x["combine"] = x.get("combine", False) or s.get("_combine", False)
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
    signaux(agr, {a["regle"]: a for a in regles})
    print(f"situations lues : {len(situations)} ; règles reconnaissables : {len(regles)} sur {len(g['aretes'])} ; "
          f"lignes (jour × type × règle) : {len(agr)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(principal())
