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
SORTIE_ROLES = ICI / "docs" / "entites-roles.json"
# 2026-11-25, 13 h 48 (remarque de l'auteur) : chaque dirigeant joue un rôle qui le met en interaction avec une partie
# d'un écosystème, hors de son réseau d'origine (le gouvernement). Le rôle se lit dans l'intitulé du poste (CIA, en
# anglais) ou dans la sorte d'entité ; règles explicites ci-dessous, un poste peut toucher plusieurs compartiments.
ROLES = [
    (r"defen[cs]e|armed forces|military|\bwar\b|veterans", ["militaire:forces", "militaire:depenses"]),
    (r"foreign|external affairs|international cooperation|diplomac", ["dirigeants-elus:diplomatie_personnelle", "organisations-internationales:onu"]),
    (r"financ|econom|treasury|budget|planning|fiscal", ["inflation-mondiale:budget", "dette-emergents:dette_publique", "economie-entreprises:conjoncture"]),
    (r"interior|home affairs|internal security|public security|public order|police", ["politique-interieure:regime", "crime-organise:repression"]),
    (r"justice|attorney|legal affairs", ["dirigeants-elus:justice_immunites"]),
    (r"health|medical", ["epidemies:autorites", "corps-humain:soins"]),
    (r"energy|petroleum|\boil\b|\bgas\b|hydrocarbon|electricity|\bpower\b", ["energie-mondiale:decisions"]),
    (r"\bmines?\b|mining|mineral", ["economie-entreprises:extraction"]),
    (r"agricultur|food|livestock|rural", ["alimentation-mondiale:cultures"]),
    (r"fisher|maritime|ocean|blue economy", ["oceans-et-fleuves:peche"]),
    (r"environment|climate|ecolog|forest", ["environnement-dechets:climat_politique"]),
    (r"water|irrigation|sanitation", ["eau-douce-potable:gouvernance"]),
    (r"trade|commerce|industr|investment", ["organisations-internationales:commerce_mondial", "economie-entreprises:echanges"]),
    (r"educat|school|universit", ["cultures-emboitees:cultures_nationales"]),
    (r"(?<!agri)(?<!horti)cultur|heritage|touris|\barts?\b", ["culture-memoire:patrimoine"]),
    (r"digital|telecom|communication|information technology|science|technolog|innovation|research", ["sciences-techniques:regulation"]),
    (r"\binformation\b|media|press", ["medias-diffusion:presse"]),
    (r"labou?r|employment|social|welfare|pension", ["classes-sociales:classes_populaires"]),
    (r"transport|infrastructure|public works|aviation|railway", ["infrastructures-critiques:reseaux"]),
    (r"migration|immigration|refugee|diaspora", ["migrations-demographie:politiques_migratoires"]),
    (r"women|gender|family", ["famille-genre:femmes_pouvoir"]),
    (r"religio|endowment|awqaf|islamic affairs", ["croyances-religions:religion_politique"]),
    (r"intelligence|state security", ["surveillance-espionnage:renseignement_etats"]),
    (r"nuclear|atomic", ["nucleaire-radiologique:surete"]),
    (r"central bank|national bank|reserve bank|bundesbank|\bbank of\b", ["inflation-mondiale:banques_centrales", "monnaie-pouvoir:banques_centrales_puissance"]),
    (r"election", ["politique-interieure:elections"]),
]
# 2026-11-25, 13 h 52 (demande de l'auteur) : même lecture pour les entreprises, par leurs secteurs Wikidata (P452) et leur nom
SECTEURS = [
    (r"petroleum|\boil\b|natural gas|\bgas\b|energy|electric|utilit|power generation|coal|renewable", ["energie-mondiale:production"]),
    (r"nuclear", ["nucleaire-radiologique:electricite"]),
    (r"mining|metal|steel|alumin|copper|gold|lithium", ["economie-entreprises:extraction"]),
    (r"semiconductor|integrated circuit|\bchip", ["sciences-techniques:calcul"]),
    (r"artificial intelligence|machine learning", ["sciences-techniques:ia"]),
    (r"software|information technology|computer|cloud|internet|e-commerce|online", ["sciences-techniques:adoption"]),
    (r"social media|social network|search engine", ["medias-diffusion:plateformes_sociales"]),
    (r"\bbank|banking|financial services|insurance|asset management|investment|brokerage|stock exchange", ["crises-bancaires:bilans"]),
    (r"crypto", ["crises-bancaires:cryptomonnaies"]),
    (r"pharmac|biotech|drug|vaccin|health care|healthcare|medical", ["epidemies:technique", "corps-humain:soins"]),
    (r"defen[cs]e|aerospace|arms|weapon|military", ["militaire:industrie_armement"]),
    (r"automo|vehicle|car manufactur|motor", ["economie-entreprises:industrie_manufacturiere"]),
    (r"telecom|wireless|mobile network|broadband|satellite", ["infrastructures-critiques:reseaux"]),
    (r"television|broadcast", ["medias-diffusion:television"]),
    (r"film|entertainment|streaming|music", ["medias-diffusion:cinema_series"]),
    (r"video game", ["medias-diffusion:jeux_video"]),
    (r"publishing|newspaper|\bmedia\b", ["medias-diffusion:presse"]),
    (r"retail|consumer|apparel|luxury|real estate|construction", ["economie-entreprises:immobilier_consommation"]),
    (r"\bfood|beverage|agricultur|agribusiness", ["alimentation-mondiale:commerce"]),
    (r"fertili[sz]er", ["alimentation-mondiale:engrais"]),
    (r"shipping|logistic|transport|airline|aviation|railway|freight", ["infrastructures-critiques:reseaux"]),
    (r"chemical", ["environnement-dechets:pollution_chimique"]),
]
PAR_SORTE = {"entreprise": ["economie-entreprises:dirigeants_entreprise"],
             "banque centrale": ["inflation-mondiale:banques_centrales", "monnaie-pouvoir:banques_centrales_puissance"],
             "organisation internationale": ["organisations-internationales:onu"]}


def roles(e: dict, ids: set) -> list[str]:
    out = []
    if e["sorte"] == "dirigeant public":
        out += ["dirigeants-elus:executifs", "politique-interieure:gouvernement"] if e["chef"] else ["dirigeants-elus:ministres"]
        for motif, comps in ROLES:
            if re.search(motif, e["poste"], re.I):
                out += comps
    out += PAR_SORTE.get(e["sorte"], [])
    if e["sorte"] == "entreprise":
        texte = e.get("secteurs", "") + " " + e.get("org", "")
        for motif, comps in SECTEURS:
            if re.search(motif, texte, re.I):
                out += comps
    return [c for c in dict.fromkeys(out) if c in ids]


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
    ids = set()
    for fy in (bot / "ecosystemes").glob("*.yaml"):
        try:
            y = yaml.safe_load(fy.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if isinstance(y, dict) and isinstance(y.get("compartiments"), list):
            ids |= {f"{y.get('theme')}:{c['id']}" for c in y["compartiments"] if isinstance(c, dict) and "id" in c}
    ent = []
    for i, r in enumerate(rows):
        e = {"id": i, "nom": r["nom"], "sorte": r["sorte"], "pays": [p for p in (r.get("pays") or "").split(";") if p],
             "org": r.get("organisation") or "", "poste": r.get("poste") or "", "chef": r.get("chef") == "1", "source": r["source"],
             "secteurs": r.get("secteurs") or ""}
        if e["nom"].lower() in nommes:
            e["acteur"] = nommes[e["nom"].lower()]
        e["comps"] = roles(e, ids)
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


def index_roles(d: dict) -> dict:
    """Par compartiment : nombre d'entités en rôle et un échantillon (chefs, acteurs nommés et entités citées d'abord)."""
    par = defaultdict(list)
    for e in d["entites"]:
        for c in e.get("comps", []):
            par[c].append(e)
    out = {}
    for c, es in par.items():
        es.sort(key=lambda e: (not e.get("acteur"), not e.get("briefs"), not e["chef"], e["nom"]))
        out[c] = {"n": len(es), "pays": len({p for e in es for p in e["pays"]}),
                  "echantillon": [{"id": e["id"], "nom": e["nom"], "pays": e["pays"][:1], "poste": e["poste"][:60]} for e in es[:30]]}
    return out


if __name__ == "__main__":
    d = construire(Path(sys.argv[1]) if len(sys.argv) > 1 else ICI.parent / "metac-bot-template")
    SORTIE.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    R = index_roles(d)
    SORTIE_ROLES.write_text(json.dumps(R, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("rôles :", len(R), "compartiments ;", sum(1 for e in d["entites"] if len(e.get("comps", [])) > 1), "entités avec un rôle thématique")
    print("entités :", len(d["entites"]), "; pays :", len(d["pays"]), "; co-citations :", len(d["cocites"]),
          "; avec briefs :", sum(1 for e in d["entites"] if e.get("briefs")), "; acteurs nommés reconnus :",
          sum(1 for e in d["entites"] if e.get("acteur")))
