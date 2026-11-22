"""Données de la carte des écosystèmes (décision de l'auteur du 2026-11-22, 2 h 12 : une page web pour voir les
mécanismes ; posée dans lecteur-local). Rassemble, sans rien inventer :
  - les fiches d'écosystèmes du bot (compartiments, éléments, acteurs, relations, ponts, niveaux sur les quatre axes,
    lus en grandeurs par ecosystemes.axes) ;
  - les liens entre acteurs (structurels ou conjoncturels) ;
  - le graphe des types de situation et d'événement (ontologie.py, tiré des règles coutumières) ;
  - les termes validés (listes/valides.txt) et leurs rattachements, les acteurs (listes/acteurs.txt), les émetteurs
    identifiés (listes/emetteurs.txt) ;
  - les situations observées (comptes/situations.jsonl) et les types réalisés (comptes/types-actifs.csv), datés et
    sourcés.
Sortie : docs/donnees.json, lu par docs/index.html.

    python carte/generer.py <chemin vers metac-bot-template>
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ICI = Path(__file__).resolve().parent.parent


def lignes_txt(chemin: Path, sep: str = " | ") -> list[list[str]]:
    if not chemin.exists():
        return []
    return [[x.strip() for x in l.split(sep)] for l in chemin.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def principal() -> int:
    bot = Path(sys.argv[1] if len(sys.argv) > 1 else "../metac-bot-template").resolve()
    sys.path.insert(0, str(bot))
    import yaml
    import ecosystemes as eco
    from ontologie import graphe

    fiches = eco.charger(bot / "ecosystemes")
    odd = eco.odd(bot / "ecosystemes/odd.yaml") if hasattr(eco, "odd") else {}   # codes officiels des indicateurs ODD
    noeuds, aretes = [], []
    for theme, f in fiches.items():
        for c in f.get("compartiments") or []:
            cid = f"{theme}:{c['id']}"
            ax = eco.axes(c.get("niveau") or {})
            noeuds.append({"id": cid, "sorte": "compartiment", "fiche": theme, "fiche_nom": f.get("nom", theme),
                           "nom": c.get("nom", c["id"]), "organisation": c.get("organisation", ""),
                           "niveau": c.get("niveau") or {}, "axes": ax, "elements": c.get("elements") or [],
                           "traces": [{"source": t.get("source", ""), "statut": t.get("statut", ""),
                                       "odd": [{"code": str(k), "libelle": odd.get(str(k), "")} for k in t.get("odd") or []]}
                                      for t in c.get("traces") or [] if isinstance(t, dict)]})
            for a in c.get("acteurs") or []:
                aid = f"{cid}::{a.get('nom')}"
                noeuds.append({"id": aid, "sorte": "acteur", "fiche": theme, "fiche_nom": f.get("nom", theme),
                               "nom": a.get("nom", ""), "organisation": a.get("organisation", ""),
                               "facette": a.get("facette", ""), "compartiment": cid,
                               "niveau": {k: a.get(k, "") for k in ("taille", "espace", "vitesse")},
                               "axes": eco.axes({k: a.get(k, "") for k in ("taille", "espace", "vitesse")})})
                aretes.append({"de": aid, "vers": cid, "sorte": "appartient"})
        for r in f.get("relations") or []:
            aretes.append({"de": f"{theme}:{r['de']}", "vers": f"{theme}:{r['vers']}", "sorte": "relation",
                           "effet": r.get("effet"), "retour": r.get("retour"), "force": r.get("force", "inconnue"),
                           "delai": r.get("delai", ""), "statut": r.get("statut", ""), "texte": r.get("texte", ""),
                           "forme": eco.forme_deduite(r.get("effet"), r.get("retour"))})
        for p in f.get("ponts") or []:
            vers = p["vers"] if ":" in p["vers"] else f"{theme}:{p['vers']}"
            aretes.append({"de": f"{theme}:{p['depuis']}", "vers": vers, "sorte": "pont", "texte": p.get("nature", ""),
                           "canal": p.get("canal", ""), "type": p.get("type", "influence")})
    ids = {n["id"] for n in noeuds}
    # liens entre acteurs (fichier à part) : « fiche:début du nom »
    liens_acteurs = []
    la = bot / "ecosystemes/liens-acteurs.yaml"
    if la.exists():
        acteurs_noeuds = [n for n in noeuds if n["sorte"] == "acteur"]

        def trouver(ref: str) -> str | None:
            fiche, _, debut = ref.partition(":")
            for n in acteurs_noeuds:
                if n["fiche"] == fiche and n["nom"].lower().startswith(debut.lower()):
                    return n["id"]
            return None
        for l in yaml.safe_load(la.read_text(encoding="utf-8")).get("liens") or []:
            a, b = trouver(l.get("de", "")), trouver(l.get("vers", ""))
            if a and b:
                liens_acteurs.append({"de": a, "vers": b, "sorte": "lien_acteur", "relation": l.get("relation"),
                                      "domaine": l.get("domaine"), "temps": (l.get("temps") or {}).get("statut", ""),
                                      "phase": (l.get("temps") or {}).get("phase", ""), "texte": l.get("regle", ""),
                                      "sources": [s.get("url") for s in l.get("sources") or [] if isinstance(s, dict)]})
    aretes = [a for a in aretes if a["de"] in ids and a["vers"] in ids] + liens_acteurs

    g = graphe(yaml.safe_load((bot / "ecosystemes/regles-coutumieres.yaml").read_text(encoding="utf-8"))["regles"])

    # termes validés, acteurs, émetteurs
    termes = [{"terme": l[1], "langue": l[2], "rattachement": [p for p in l[3].split(";") if p],
               "note": l[4] if len(l) > 4 else "", "emetteurs": l[5] if len(l) > 5 else ""}
              for l in lignes_txt(ICI / "listes/valides.txt") if l and l[0] == "+"]
    acteurs = [{"terme": l[0], "acteur": l[2], "taille": l[3], "facette": l[4], "organisation": l[5],
                "compartiment": l[6]} for l in lignes_txt(ICI / "listes/acteurs.txt") if len(l) >= 7]
    emetteurs = [{"domaine": l[0], "nom": l[1], "nature": l[2], "pays": l[3], "proprietaire": l[4],
                  "financement": l[5], "langues": l[6], "note": l[7], "source": l[8], "consulte": l[9]}
                 for l in lignes_txt(ICI / "listes/emetteurs.txt", "|") if len(l) >= 10]

    # situations observées (datées, sourcées) et activité par compartiment et par jour
    situations = []
    f = ICI / "comptes/situations.jsonl"
    if f.exists():
        for l in f.read_text(encoding="utf-8").splitlines():
            if l.strip():
                s = json.loads(l)
                situations.append({"jour": s["jour"], "lien": s["lien"], "emetteur": s["emetteur"].get("domaine"),
                                   "espace": s.get("espace", []), "acteurs": [a["acteur"] for a in s.get("acteurs", [])],
                                   "compartiments": [c["compartiment"] for c in s.get("compartiments", [])],
                                   "termes": s.get("termes", [])})
    types_actifs = []
    f = ICI / "comptes/types-actifs.csv"
    if f.exists():
        with f.open(encoding="utf-8") as fo:
            types_actifs = list(csv.DictReader(fo))

    # évolution de notre recherche (fiche « L'observateur et la mesure ») : recalculée depuis l'historique du bot si possible
    import subprocess
    try:
        subprocess.run([sys.executable, "recherche_evolution.py"], cwd=bot, check=True, capture_output=True, timeout=120)
    except Exception as e:
        print(f"évolution de la recherche non recalculée ({type(e).__name__}) ; version enregistrée utilisée")
    f = bot / "registre/recherche-evolution.json"
    recherche = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}

    # santé du collecteur : date de la dernière situation enregistrée (aucune = jamais lancé ou en panne)
    collecte = {"situations": len(situations), "derniere": max((s["jour"] for s in situations), default=None)}
    donnees = {"genere": date.today().isoformat(), "collecte": collecte, "recherche": recherche, "noeuds": noeuds, "aretes": aretes,
               "types": {"situations": g["situations"], "evenements": g["evenements"], "regles": g["aretes"]},
               "termes": termes, "acteurs_termes": acteurs, "emetteurs": emetteurs,
               "situations": situations, "types_actifs": types_actifs}
    out = ICI / "docs/donnees.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(donnees, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"carte : {sum(n['sorte'] == 'compartiment' for n in noeuds)} compartiments, "
          f"{sum(n['sorte'] == 'acteur' for n in noeuds)} acteurs, {len(aretes)} arêtes, {len(termes)} termes, "
          f"{len(situations)} situations ; {out.stat().st_size // 1024} Ko")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
