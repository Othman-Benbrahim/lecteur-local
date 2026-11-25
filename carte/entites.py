"""Vue « Entités » de la carte (demande de l'auteur du 2026-11-25, 13 h 36-13 h 37) : les entités prioritaires des briefs
(dirigeants publics de la CIA via OpenSanctions, PDG, banques centrales et organisations internationales via Wikidata)
et leurs liens. Lu dans le bot : registre/entites/entites.csv, briefs/journal.jsonl, ecosystemes/acteurs-nommes.yaml ;
écrit docs/entites.json, chargé par la carte seulement quand on ouvre la vue.

Liens :
- directs (structurels) : le chef d'État ou de gouvernement et les membres de son gouvernement ; un dirigeant et son
  organisation (entreprise, banque centrale, organisation internationale) ; une organisation et son pays ;
- passerelle : une entité qui est aussi un acteur nommé de la carte (réseau des acteurs nommés) ;
- indirects : co-cités dans un brief (une sonde, pas la preuve d'un lien).
    python carte/entites.py <chemin vers metac-bot-template>
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

ICI = Path(__file__).resolve().parent.parent
SORTIE = ICI / "docs" / "entites.json"


def construire(bot: Path) -> dict:
    f = bot / "registre" / "entites" / "entites.csv"
    if not f.exists():
        return {"genere_le": None, "entites": [], "pays": {}, "cocites": []}
    with f.open(encoding="utf-8") as h:
        rows = list(csv.DictReader(h))
    # entités validées à la main (listes/entites-prioritaires-proposees.txt), absentes des sources automatiques
    deja = {r["nom"].lower() for r in rows}
    for l in (ICI / "listes" / "entites-prioritaires-proposees.txt").read_text(encoding="utf-8").splitlines():
        p = [x.strip() for x in l.split("|")]
        if len(p) >= 3 and p[0] == "+":
            v = [x for x in p[1].split("/") if x.strip()]
            if v[0].lower() not in deja:
                rows.append({"variantes": "/".join(v), "nom": v[0], "sorte": p[2], "pays": "", "pays_nom": "",
                             "organisation": "", "poste": p[3] if len(p) > 3 else "", "chef": "0", "source": "liste validée"})
    noms_pays = defaultdict(Counter)
    for r in rows:
        ps = [p for p in (r.get("pays") or "").split(";") if p]
        if len(ps) == 1 and r.get("pays_nom"):
            noms_pays[ps[0]][r["pays_nom"]] += 1
    pays = {k: v.most_common(1)[0][0] for k, v in noms_pays.items()}
    nommes = {}
    fa = bot / "ecosystemes" / "acteurs-nommes.yaml"
    if fa.exists():
        for a in yaml.safe_load(fa.read_text(encoding="utf-8")).get("acteurs", []):
            nommes[a["nom"].split(" (")[0].lower()] = a["id"]
    ent = []
    for i, r in enumerate(rows):
        e = {"id": i, "nom": r["nom"], "sorte": r["sorte"], "pays": [p for p in (r.get("pays") or "").split(";") if p],
             "org": r.get("organisation") or "", "poste": r.get("poste") or "", "chef": r.get("chef") == "1", "source": r["source"]}
        if e["nom"].lower() in nommes:
            e["acteur"] = nommes[e["nom"].lower()]
        ent.append(e)
    # co-citations dans les briefs (90 derniers jours ou tout le journal : petit volume)
    var = [(i, [v for v in r["variantes"].split("/") if len(v) >= 4]) for i, r in enumerate(rows)]
    motifs = [(i, re.compile(r"(?<![\w-])(" + "|".join(re.escape(v) for v in vs) + r")(?![\w-])", re.I)) for i, vs in var if vs]
    paires = defaultdict(list)
    jb = bot / "briefs" / "journal.jsonl"
    for l in (jb.read_text(encoding="utf-8").splitlines() if jb.exists() else []):
        try:
            x = json.loads(l)
        except ValueError:
            continue
        if x.get("statut") != "ok":
            continue
        b = x.get("brief", {})
        t = " ".join([b.get("titre", ""), b.get("ce_que_ca_change", "")] + [f.get("fait", "") for f in b.get("faits", [])])
        ici = sorted({i for i, m in motifs if m.search(t)})
        for a in range(len(ici)):
            for c in range(a + 1, len(ici)):
                paires[(ici[a], ici[c])].append({"jour": x.get("jour"), "titre": b.get("titre"), "url": x.get("url")})
        for i in ici:
            ent[i].setdefault("briefs", []).append({"jour": x.get("jour"), "titre": b.get("titre"), "url": x.get("url")})
    par_pays = Counter(p for e in ent for p in e["pays"])
    return {"genere_le": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "pays": {k: {"nom": pays.get(k, k), "n": n} for k, n in par_pays.most_common()},
            "entites": ent, "cocites": [{"a": a, "b": b, "briefs": v[:5]} for (a, b), v in paires.items()]}


if __name__ == "__main__":
    d = construire(Path(sys.argv[1]) if len(sys.argv) > 1 else ICI.parent / "metac-bot-template")
    SORTIE.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("entités :", len(d["entites"]), "; pays :", len(d["pays"]), "; co-citations :", len(d["cocites"]),
          "; avec briefs :", sum(1 for e in d["entites"] if e.get("briefs")), "; acteurs nommés reconnus :",
          sum(1 for e in d["entites"] if e.get("acteur")))
