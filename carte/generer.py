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
import re
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
    # 2026-11-22, 11 h 36 : comptes du collecteur RSS du bot (articles nouveaux par famille et par jour), rattachés aux
    # compartiments dont une trace nomme la famille (« famille « X » ») ; 30 derniers jours
    par_famille: dict[str, dict] = {}
    ff = bot / "registre/rss-familles.csv"
    if ff.exists():
        import csv as _csv
        for r in _csv.DictReader(ff.open(encoding="utf-8")):
            par_famille.setdefault(r["famille"], {})[r["jour"]] = int(r["articles"])
    from datetime import timedelta
    jours30 = [(date.today() - timedelta(days=k)).isoformat() for k in range(29, -1, -1)]

    def flux_de(traces):
        out = []
        for t in traces or []:
            m = re.search(r"famille « (\w+) »", str(t.get("source", ""))) if isinstance(t, dict) else None
            if m and m.group(1) not in [x["famille"] for x in out]:
                serie = par_famille.get(m.group(1), {})
                out.append({"famille": m.group(1), "serie": [serie.get(j, 0) for j in jours30],
                            "debut": min(serie) if serie else None})
        return out
    odd = eco.odd(bot / "ecosystemes/odd.yaml") if hasattr(eco, "odd") else {}   # codes officiels des indicateurs ODD
    noeuds, aretes = [], []
    for theme, f in fiches.items():
        for c in f.get("compartiments") or []:
            cid = f"{theme}:{c['id']}"
            ax = eco.axes(c.get("niveau") or {})
            noeuds.append({"id": cid, "sorte": "compartiment", "fiche": theme, "fiche_nom": f.get("nom", theme),
                           "nom": c.get("nom", c["id"]), "organisation": c.get("organisation", ""),
                           # 0.10.473 : pays concernés (champ « geopolitique ») ; l'état UCDP s'y accroche
                           "pays": list((c.get("geopolitique") or {}).get("pays") or []),
                           "niveau": c.get("niveau") or {}, "axes": ax, "elements": c.get("elements") or [],
                           "traces": [{"source": t.get("source", ""), "statut": t.get("statut", ""),
                                       "odd": [{"code": str(k), "libelle": odd.get(str(k), "")} for k in t.get("odd") or []]}
                                      for t in c.get("traces") or [] if isinstance(t, dict)],
                           "flux": flux_de(c.get("traces")),
                           # 0.10.461 : cycles qui touchent ce compartiment, tensions dont il est un pôle
                           "cycles": [{k: y.get(k) for k in ("id", "nom", "sorte", "mecanisme", "periode",
                                                            "phase_vulnerable", "statut")}
                                      for y in f.get("cycles") or [] if c["id"] in (y.get("compartiments") or [])],
                           "tensions": [{k: y.get(k) for k in ("id", "nom", "forme", "entre", "stock", "module_par",
                                                              "seuil", "statut", "texte", "tampons")}
                                        for y in f.get("tensions") or [] if c["id"] in (y.get("entre") or [])]})
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
    signaux = []   # 2026-11-22, 16 h 26 : signaux forts et faibles (realiser.py)
    fs = ICI / "comptes/signaux.csv"
    if fs.exists():
        with fs.open(encoding="utf-8") as fo:
            signaux = [r for r in csv.DictReader(fo) if r["classe"] != "bruit"]
    suivi = {}
    fv = ICI / "comptes/signaux-faibles-suivi.csv"
    if fv.exists():
        with fv.open(encoding="utf-8") as fo:
            suivi = {r["jour"] + "|" + r["regle"]: r["statut"] for r in csv.DictReader(fo)}
    # mécanique de bascule en ombre (bot 0.10.464, décision de l'auteur du 2026-11-22, 20 h 51 - 20 h 57) : étincelles
    # de la semaine, prédiction « prend dans les 8 semaines », issues constatées et score
    mecanique = {"journal": [], "issues": {}, "score": {}, "noms": {}}
    fj, fi, fsc = bot / "registre/mecanique-journal.csv", bot / "registre/mecanique-issues.csv", bot / "registre/mecanique-score.json"
    if fj.exists():
        with fj.open(encoding="utf-8") as fo:
            mecanique["journal"] = [{k: r[k] for k in ("id", "inscrit_le", "pays", "iso3", "semaine", "rapport", "p_prend",
                                                       "version", "modules")} for r in csv.DictReader(fo)]
    if fi.exists():
        with fi.open(encoding="utf-8") as fo:
            mecanique["issues"] = {r["id"]: {"prend": int(r["prend"]), "semaines_elevees": int(r["semaines_elevees"]),
                                             "constate_le": r["constate_le"]} for r in csv.DictReader(fo)}
    if fsc.exists():
        mecanique["score"] = json.loads(fsc.read_text(encoding="utf-8"))
    fc = bot / "ecosystemes/codes-pays-fips.csv"
    if fc.exists():
        lc = [l for l in fc.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
        mecanique["noms"] = {r["fips"]: r["nom"] for r in csv.DictReader(lc)}
    # cartes publiées lues par le bot (0.10.470-471, standard de lecture ; décision de l'auteur du 2026-11-23) : UCDP,
    # état du mois par pays et enchaînements d'un mois au suivant (1989-2025)
    # 0.10.478 (décision de l'auteur, 2026-11-23, 19 h 14 : « cliquer sur la bulle pour atterrir sur les indicateurs de
    # la fiche, en bas, valeurs à jour quand ils existent ») : pour chaque fiche, chaque série branchée sur une trace
    # (adaptateur du bot), lue dans le registre du bot : dernier relevé, sa date, les 30 derniers points, l'écart du
    # dernier relevé à la moyenne des relevés précédents (en écarts-types), et « périmée » si le dernier relevé est vieux.
    indicateurs = {}
    try:
        import adaptateurs as _ad
        import statistics as _st
        cache = {}
        for theme, f in fiches.items():
            vus = {}
            for c in f.get("compartiments") or []:
                for t in c.get("traces") or []:
                    if not isinstance(t, dict) or not t.get("adaptateur"):
                        continue
                    a = t["adaptateur"]
                    if a in vus:
                        vus[a]["compartiments"].append(c.get("nom", c["id"]))
                        continue
                    if a not in cache:
                        try:
                            cache[a] = _ad.lire(a, bot / "registre")
                        except Exception:
                            cache[a] = []
                    s = cache[a]
                    it = {"adaptateur": a, "source": t.get("source", ""), "compartiments": [c.get("nom", c["id"])]}
                    if s:
                        v = [x for _, x in s]
                        avant = v[-31:-1]
                        ec = sorted((date.fromisoformat(y[0][:10]) - date.fromisoformat(x[0][:10])).days
                                    for x, y in zip(s[-30:], s[-29:]))
                        pas = ec[len(ec) // 2] if ec else 1        # pas de la série : jour, semaine, mois
                        it.update(dernier_jour=s[-1][0][:10], derniere_valeur=round(v[-1], 3),
                                  serie=[round(x, 3) for x in v[-30:]], debut=s[0][0][:10], pas_jours=pas,
                                  perimee=(date.today() - date.fromisoformat(s[-1][0][:10])).days > max(45, 3 * pas))
                        if len(avant) >= 5 and (_st.pstdev(avant) or 0) > 0:
                            it["ecart"] = round((v[-1] - _st.mean(avant)) / _st.pstdev(avant), 2)
                    vus[a] = it
            if vus:
                indicateurs[theme] = sorted(vus.values(), key=lambda x: (not x.get("serie"), x.get("perimee", False), x["source"]))
    except Exception as e:
        print(f"indicateurs : {type(e).__name__} {e}")
    # 0.10.480 (décision de l'auteur, 2026-11-23, 19 h 08 - 20 h 10) : veille de la carte — liens « Ressources » et flux
    # RSS de chaque fiche, flux RSS de chaque compartiment (catalogue du bot, veille/catalogue.yaml) ; derniers articles
    # lus par le bot (registre/veille-flux.json : titre, lien, date seulement)
    veille = {}
    try:
        import veille as _v
        cat = _v.catalogue(bot / "veille/catalogue.yaml")
        lus = {}
        fv = bot / "registre/veille-flux.json"
        if fv.exists():
            lus = json.loads(fv.read_text(encoding="utf-8"))
        tab = _v.regions_pays()
        comps, fiches_v = {}, {}
        for theme, f in fiches.items():
            fiches_v[theme] = {"liens": _v.liens_fiche(cat, theme), "flux": _v.flux_fiche(cat, theme),
                               "nom": f.get("nom", theme), "but": (f.get("declaration") or {}).get("but", "")}
            for c in f.get("compartiments") or []:
                comps[f"{theme}:{c['id']}"] = _v.flux_compartiment(cat, theme, c, tab, sauf=_v.muets(lus) if lus else frozenset())
        utiles = {x for l in comps.values() for x in l} | {x for d in fiches_v.values() for x in d["flux"]}
        veille = {"fiches": fiches_v, "compartiments": comps,
                  "flux": {k: {"titre": (lus.get(k) or {}).get("titre") or v["titre"],
                               "url": (lus.get(k) or {}).get("url") or v["url"], "statut": (lus.get(k) or {}).get("statut", "pas_encore_lu"),
                               "lu_le": (lus.get(k) or {}).get("lu_le"), "articles": ((lus.get(k) or {}).get("articles") or [])[:6]}
                           for k, v in (cat.get("flux") or {}).items() if k in utiles}}
    except Exception as e:
        print(f"veille : {type(e).__name__} {e}")
    ucdp = {}
    fu = bot / "registre/cartes/ucdp-etats.json"
    if fu.exists():
        ucdp = json.loads(fu.read_text(encoding="utf-8"))
        nf = {}
        fc2 = bot / "ecosystemes/codes-pays-fips.csv"
        if fc2.exists():
            lc2 = [l for l in fc2.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
            nf = {r["iso3"]: r["nom_fr"] for r in csv.DictReader(lc2) if r.get("nom_fr")}
        import re as _re
        for p_ in ucdp.get("pays", []):
            p_["nom_fr"] = nf.get(p_["iso3"]) or _re.sub(r"\s*\(.*?\)", "", p_["nom"])
        # 0.10.473 : état de chaque pays rattaché à un compartiment (champ « geopolitique »), même hors des 40 premiers
        vises = {x for n in noeuds for x in n.get("pays") or []}
        ucdp["par_pays"] = {p_["iso3"]: p_ for p_ in ucdp.get("pays", []) if p_["iso3"] in vises}
        ucdp["noms_pays"] = {x: nf.get(x, x) for x in sorted(vises)}
    # 0.10.479 (décision de l'auteur, 2026-11-23, 18 h 49 et 20 h 10) : relevé statistique de chaque pays rattaché à un
    # compartiment, pour l'infobulle « pays » : dernières valeurs de la Banque mondiale (WDI, registre du bot) et
    # manifestations des 30 derniers jours (GDELT, adaptateur du bot)
    releves = {}
    try:
        import gzip as _gz
        import csv as _csv2
        INDIC = {"SP.POP.TOTL": "population", "NY.GDP.MKTP.CD": "PIB (dollars courants)", "NY.GDP.PCAP.CD": "PIB par habitant (dollars)",
                 "NY.GDP.MKTP.KD.ZG": "croissance du PIB (%)", "FP.CPI.TOTL.ZG": "inflation (%)",
                 "SL.UEM.TOTL.ZS": "chômage (%)", "SL.UEM.1524.ZS": "chômage des 15-24 ans (%)",
                 "MS.MIL.XPND.GD.ZS": "dépenses militaires (% du PIB)", "SP.DYN.LE00.IN": "espérance de vie (ans)",
                 "SP.DYN.TFRT.IN": "enfants par femme", "EG.ELC.ACCS.ZS": "accès à l'électricité (%)",
                 "SP.URB.TOTL.IN.ZS": "population urbaine (%)", "GOV_WGI_PV_EST": "stabilité politique (indice −2,5 à +2,5)"}
        vises_all = {x for n in noeuds for x in n.get("pays") or []}
        fw = bot / "registre/banque-mondiale-wdi.csv.gz"
        if fw.exists():
            with _gz.open(fw, "rt", encoding="utf-8") as h:
                for r in _csv2.DictReader(h):
                    if r["pays"] not in vises_all:
                        continue
                    d = releves.setdefault(r["pays"], {})
                    for k, lib in INDIC.items():
                        x = r.get(k)
                        if x and (k not in d or int(r["annee"]) >= d[k][0]):
                            d[k] = [int(r["annee"]), float(x)]
        iso2 = {}
        fc = bot / "ecosystemes/codes-pays-fips.csv"
        for r in _csv2.DictReader(l for l in fc.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")):
            if r.get("iso2"):
                iso2[r["iso3"]] = r["iso2"]
        import adaptateurs as _ad2
        out_r = {}
        for x in sorted(vises_all):
            e = {"wdi": {INDIC[k]: v for k, v in releves.get(x, {}).items()}, "iso2": iso2.get(x, "")}
            try:
                m = _ad2.lire(f"manifestations:{iso2.get(x, '')}", bot / "registre") if iso2.get(x) else []
            except Exception:
                m = []
            if m:
                e["manifestations_30j"] = int(sum(v for _, v in m[-30:]))
                e["manifestations_30j_avant"] = int(sum(v for _, v in m[-60:-30]))
                e["manifestations_jusquau"] = m[-1][0][:10]
            out_r[x] = e
        ucdp["releves"] = out_r
    except Exception as e:
        print(f"relevés : {type(e).__name__} {e}")
        ucdp["pays"] = [p_ for p_ in ucdp.get("pays", []) if p_["morts_mois"] > 0 or p_["mediane_12"] > 0][:40]
        # 0.10.472 : manifestations politiques violentes et déclenchements (noms en français, tables d'index retirées)
        for p_ in (ucdp.get("vpp") or {}).get("pays", []) + (ucdp.get("declenchements") or {}).get("recents", []):
            p_["nom_fr"] = nf.get(p_["iso3"]) or _re.sub(r"\s*\(.*?\)", "", p_["nom"])
        for p_ in ucdp["pays"]:
            if p_.get("dernier_declenchement"):
                p_["dernier_declenchement"].pop("nom", None)
        (ucdp.get("vpp") or {}).pop("par_pays", None)
        (ucdp.get("declenchements") or {}).pop("dernier_par_pays", None)
    types_actifs = []
    f = ICI / "comptes/types-actifs.csv"
    if f.exists():
        with f.open(encoding="utf-8") as fo:
            types_actifs = list(csv.DictReader(fo))

    # évolution de notre recherche (fiche « L'observateur et la mesure ») : recalculée depuis l'historique du bot si possible
    import os
    import subprocess
    import tempfile
    f = bot / "registre/recherche-evolution.json"
    with tempfile.TemporaryDirectory() as tmp:          # 2026-11-24 : calcul hors du registre du bot (dépôt laissé propre)
        sortie = Path(tmp) / "recherche-evolution.json"
        try:
            subprocess.run([sys.executable, "recherche_evolution.py"], cwd=bot, check=True, capture_output=True, timeout=120,
                           env={**os.environ, "RECHERCHE_EVOLUTION_SORTIE": str(sortie)})
            if sortie.exists():
                f = sortie
        except Exception as e:
            print(f"évolution de la recherche non recalculée ({type(e).__name__}) ; version enregistrée utilisée")
        recherche = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}

    # acteurs nommés et leurs réseaux (bot 0.10.444, décision de l'auteur du 2026-11-22, 7 h 41 - 7 h 49) : à part des
    # noeuds des fiches ; chacun rattaché à des compartiments ; liens entre acteurs et vers la population
    nommes = {"acteurs": [], "liens": []}
    fn = bot / "ecosystemes/acteurs-nommes.yaml"
    if fn.exists():
        dn = yaml.safe_load(fn.read_text(encoding="utf-8")) or {}
        src = lambda x: [{"titre": s.get("titre", ""), "url": s.get("url", ""), "lecture": s.get("lecture", "")}
                         for s in x.get("sources") or [] if isinstance(s, dict)]
        nommes["acteurs"] = [{"id": a["id"], "nom": a["nom"], "sorte": a.get("sorte", ""), "role": a.get("role", ""),
                              "depuis": str(a.get("depuis", "")), "cycle": a.get("cycle", ""),
                              "compartiments": a.get("compartiments") or [], "sources": src(a)} for a in dn.get("acteurs") or []]
        nommes["liens"] = [{"id": l["id"], "de": l["de"], "vers": l["vers"], "relation": l.get("relation", ""),
                            "via": l.get("via", ""), "domaine": l.get("domaine", ""), "texte": l.get("texte", ""),
                            "temps": (l.get("temps") or {}).get("statut", ""), "phase": (l.get("temps") or {}).get("phase", ""),
                            "sources": src(l)} for l in dn.get("liens") or []]

    # santé du collecteur : date de la dernière situation enregistrée (aucune = jamais lancé ou en panne)
    collecte = {"situations": len(situations), "derniere": max((s["jour"] for s in situations), default=None)}
    donnees = {"genere": date.today().isoformat(), "collecte": collecte, "recherche": recherche, "noeuds": noeuds, "aretes": aretes,
               "types": {"situations": g["situations"], "evenements": g["evenements"], "regles": g["aretes"]},
               "termes": termes, "acteurs_termes": acteurs, "emetteurs": emetteurs,
               "situations": situations, "types_actifs": types_actifs, "signaux": signaux, "suivi": suivi, "nommes": nommes, "mecanique": mecanique, "ucdp": ucdp, "indicateurs": indicateurs, "veille": veille,
               "jours30": [jours30[0], jours30[-1]]}
    out = ICI / "docs/donnees.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(donnees, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"carte : {sum(n['sorte'] == 'compartiment' for n in noeuds)} compartiments, "
          f"{sum(n['sorte'] == 'acteur' for n in noeuds)} acteurs, {len(aretes)} arêtes, {len(termes)} termes, "
          f"{len(situations)} situations ; {out.stat().st_size // 1024} Ko")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
