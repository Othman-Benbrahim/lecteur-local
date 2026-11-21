"""Étape 1 du lecteur sans modèle (décision de l'auteur du 2026-11-21, 23 h 48 - 23 h 51) : inventaire, sans rien
télécharger, de ce que les fiches d'écosystèmes du bot déclarent déjà — chaque élément et chaque type d'acteur, avec sa
position (fiche, compartiment, niveau d'organisation, espace, temps, vitesse, taille, facette). C'est ce cadre, et lui
seul, auquel tout mot compté plus tard devra se rattacher. Aucun classement nouveau n'est ajouté.

    python inventaire_fiches.py <chemin vers metac-bot-template/ecosystemes>

Sorties : listes/inventaire-fiches.csv (une ligne par élément ou acteur) et listes/inventaire-fiches.md (comptes).
"""
from __future__ import annotations

import collections
import csv
import sys
from pathlib import Path

import yaml

ARTICLES = ("un ", "une ", "des ", "les ", "le ", "la ", "l'", "l’", "a ", "an ", "the ")
CHAMPS = ["fiche", "fiche_nom", "compartiment", "compartiment_nom", "organisation", "sorte", "texte", "generique",
          "taille", "espace", "temps", "vitesse", "facette", "nb_traces", "aussi_dans"]  # nb_traces : traces au statut « collecte »


def lire(dossier: Path) -> list[dict]:
    lignes = []
    for f in sorted(dossier.glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or not d.get("theme") or not d.get("compartiments"):
            continue
        fiche = d.get("theme") or f.stem
        for c in d["compartiments"]:
            niv = c.get("niveau") or {}
            base = {"fiche": fiche, "fiche_nom": d.get("nom", ""), "compartiment": c.get("id", ""),
                    "compartiment_nom": c.get("nom", ""), "organisation": c.get("organisation", ""),
                    "nb_traces": sum(1 for t in c.get("traces") or [] if isinstance(t, dict) and t.get("statut") == "collecte")}
            for e in c.get("elements") or []:
                lignes.append({**base, "sorte": "element", "texte": str(e), "generique": "",
                               "taille": niv.get("taille", ""), "espace": niv.get("espace", ""),
                               "temps": niv.get("temps", ""), "vitesse": niv.get("vitesse", ""), "facette": ""})
            for a in c.get("acteurs") or []:
                nom = str(a.get("nom", ""))
                lignes.append({**base, "sorte": "acteur", "texte": nom,
                               "generique": "oui" if nom.lower().startswith(ARTICLES) else "non",
                               "taille": a.get("taille", ""), "espace": a.get("espace", ""),
                               "temps": niv.get("temps", ""), "vitesse": a.get("vitesse", ""),
                               "facette": a.get("facette", ""),
                               "organisation": a.get("organisation") or base["organisation"]})
    # même texte présent dans plusieurs fiches
    ou = collections.defaultdict(set)
    for l in lignes:
        ou[(l["sorte"], l["texte"].lower())].add(l["fiche"])
    for l in lignes:
        autres = sorted(ou[(l["sorte"], l["texte"].lower())] - {l["fiche"]})
        l["aussi_dans"] = ";".join(autres)
    return lignes


def resume(lignes: list[dict]) -> str:
    el = [l for l in lignes if l["sorte"] == "element"]
    ac = [l for l in lignes if l["sorte"] == "acteur"]
    fiches = sorted({l["fiche"] for l in lignes})
    comps = {(l["fiche"], l["compartiment"]) for l in lignes}
    sans_trace = {(l["fiche"], l["compartiment"]) for l in lignes if l["nb_traces"] == 0}
    t = ["# Inventaire des fiches d'écosystèmes (étape 1 du lecteur sans modèle)", "",
         "Extrait automatiquement des fiches du bot, sans classement ajouté. Détail ligne par ligne : "
         "`inventaire-fiches.csv`.", "",
         f"- {len(fiches)} fiches, {len(comps)} compartiments ({len(sans_trace)} sans aucune trace collectée)",
         f"- {len(el)} éléments ({len({l['texte'].lower() for l in el})} textes distincts)",
         f"- {len(ac)} acteurs ({len({l['texte'].lower() for l in ac})} distincts) : "
         f"{sum(l['generique'] == 'oui' for l in ac)} types génériques (« un média d'État »…), à peupler de noms ; "
         f"{sum(l['generique'] == 'non' for l in ac)} désignés autrement (déjà nommés, ou à relire)", ""]
    def table(titre, cle, pop):
        c = collections.Counter(l[cle] or "—" for l in pop)
        t.extend([f"## {titre}", "", "| | nombre |", "|---|---|"] + [f"| {k} | {v} |" for k, v in c.most_common()] + [""])
    table("Acteurs par facette", "facette", ac)
    table("Acteurs par taille", "taille", ac)
    table("Éléments et acteurs par niveau d'organisation", "organisation", lignes)
    t.extend(["## Par fiche", "", "| fiche | compartiments | éléments | acteurs | compartiments sans trace collectée |",
              "|---|---|---|---|---|"])
    for f in fiches:
        lf = [l for l in lignes if l["fiche"] == f]
        t.append(f"| {f} | {len({l['compartiment'] for l in lf})} | {sum(l['sorte']=='element' for l in lf)} | "
                 f"{sum(l['sorte']=='acteur' for l in lf)} | {len({l['compartiment'] for l in lf if l['nb_traces']==0})} |")
    return "\n".join(t) + "\n"


def principal() -> int:
    dossier = Path(sys.argv[1] if len(sys.argv) > 1 else "../metac-bot-template/ecosystemes")
    lignes = lire(dossier)
    out = Path("listes")
    out.mkdir(exist_ok=True)
    with (out / "inventaire-fiches.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CHAMPS)
        w.writeheader()
        w.writerows(lignes)
    (out / "inventaire-fiches.md").write_text(resume(lignes), encoding="utf-8")
    print(resume(lignes)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(principal())
