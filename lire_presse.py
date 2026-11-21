"""Jeu difficile du lecteur local (décision de l'auteur du 2026-11-21, 21 h 56 : seulement sur le dernier modèle).

Textes : articles de presse spécialisée en cybersécurité (flux publics, robots.txt vérifié), retenus seulement s'ils
citent au moins un identifiant CVE (pour qu'il y ait quelque chose à contrôler). Le texte est lu le temps du passage,
jamais gardé.

Le modèle rend, pour chaque article : les failles dont le texte dit qu'elles sont exploitées (avec citation), et les
acteurs nommés avec leur rôle en liste fermée (auteur de l'attaque, victime, chercheur ou rapporteur, éditeur) et
leur citation. Contrôles :
1. citation retrouvée mot pour mot (sinon rejet, compté) ;
2. VÉRITÉ EXTÉRIEURE : chaque faille dite exploitée est cherchée dans le catalogue KEV de la CISA (failles dont
   l'exploitation réelle est établie), lu au moment du passage. Limite connue : la presse peut annoncer une
   exploitation avant son entrée au catalogue ; ces cas comptent comme désaccords et sont listés pour relecture.
3. rôles : échantillon publié pour relecture à la main (aucune vérité automatique pour les rôles).

    python lire_presse.py <nom du modèle>
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

import lire

FLUX = ["https://therecord.media/feed", "https://www.bleepingcomputer.com/feed/",
        "https://feeds.feedburner.com/securityweek"]
KEV = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
MAX_ARTICLES = 15
MAX_CARACTERES = 4000
ROLES = ["auteur de l'attaque", "victime", "chercheur ou rapporteur", "éditeur"]
SCHEMA = {
    "type": "object",
    "properties": {
        "failles_exploitees": {"type": "array", "maxItems": 10, "items": {
            "type": "object", "additionalProperties": False, "required": ["cve", "citation"],
            "properties": {"cve": {"type": "string", "pattern": "^CVE-[0-9]{4}-[0-9]{4,7}$"},
                           "citation": {"type": "string", "maxLength": 250}}}},
        "acteurs": {"type": "array", "maxItems": 8, "items": {
            "type": "object", "additionalProperties": False, "required": ["nom", "role", "pays", "citation"],
            "properties": {"nom": {"type": "string", "maxLength": 60}, "role": {"type": "string", "enum": ROLES},
                           "pays": {"type": ["string", "null"], "maxLength": 40},
                           "citation": {"type": "string", "maxLength": 250}}}},
    },
    "required": ["failles_exploitees", "acteurs"],
    "additionalProperties": False,
}
CONSIGNE = ("You read a cybersecurity news article. Answer ONLY with the requested JSON. Strict rules: invent "
            "nothing. failles_exploitees = only CVE identifiers that the article says are exploited (in the wild, by "
            "attackers), each with the exact sentence from the article, copied word for word. acteurs = organisations, "
            "groups or companies named in the article, each with its role: 'auteur de l'attaque' (the attacker), "
            "'victime' (the target), 'chercheur ou rapporteur' (who discovered, reported or analysed the attack, e.g. "
            "Google, Mandiant, Microsoft threat intelligence), 'éditeur' (vendor of the vulnerable product); pays = "
            "the country the article links to that actor, or null; citation = the exact sentence, word for word. A "
            "company that reports an attack is NOT the attacker.")


# Second lecteur, NON GÉNÉRATIF (décision de l'auteur, 22 h 12) : ReDefiNER (Jungroo AI Labs, 0,8 milliard de
# paramètres, Apache 2.0) ne fait que marquer des passages du texte selon un livret de règles ; il ne peut pas écrire
# un nom absent du texte. Limites annoncées par ses auteurs : anglais seulement, types qui dépendent du contexte peu
# fiables, abstention faible. Il lit les mêmes articles que le modèle génératif, pour comparer.
LIVRET = """[RULES]
Extract every entity of the supplied types. Return exact spans from the text.
R1 A company or team that discovers, reports, analyses or tracks an attack is RESEARCHER, never ATTACKER.
R2 EXPLOITED_CVE only when the text states that this CVE is exploited by attackers or in the wild.

[TYPE] ATTACKER
[DESCRIPTION] A threat actor, hacking group, ransomware gang or state named as carrying out the attack.

[TYPE] VICTIM
[DESCRIPTION] An organisation, company, government body or sector that was targeted or breached.

[TYPE] RESEARCHER
[DESCRIPTION] A security company, threat intelligence team or researcher that discovered or reported the attack.

[TYPE] VENDOR
[DESCRIPTION] The company that makes the vulnerable product.

[TYPE] EXPLOITED_CVE
[DESCRIPTION] A CVE identifier that the text says is actively exploited.
"""
_TAGGER = None


def redefiner(texte: str) -> tuple[list[dict] | None, float]:
    global _TAGGER
    t0 = time.time()
    try:
        if _TAGGER is None:
            from redefiner import Tagger
            _TAGGER = Tagger.from_pretrained("jungroo-ai/redefiner-0.8b", device="cpu")
        spans = [x for x in _TAGGER.extract(texte, LIVRET) if x.get("confidence", 0) >= 0.5]
        return spans, time.time() - t0
    except Exception as e:
        print(f"  ReDefiNER indisponible : {type(e).__name__}: {e}"[:300], flush=True)
        return None, time.time() - t0


def appeler(c: httpx.Client, texte: str) -> tuple[dict | None, float]:
    t0 = time.time()
    r = c.post(lire.SERVEUR, json={"messages": [{"role": "system", "content": CONSIGNE},
                                                {"role": "user", "content": texte}],
                                   "temperature": 0, "max_tokens": 900,
                                   "response_format": {"type": "json_schema",
                                                       "json_schema": {"name": "article", "schema": SCHEMA}}},
               timeout=1200)
    d = time.time() - t0
    try:
        return json.loads(r.json()["choices"][0]["message"]["content"]), d
    except Exception as e:
        print(f"  réponse illisible ({type(e).__name__}) : HTTP {r.status_code}, {r.text[:200]!r}", flush=True)
        return None, d


def principal() -> int:
    nom = sys.argv[1] if len(sys.argv) > 1 else "modele"
    with httpx.Client(headers={"User-Agent": lire.UA}, timeout=60, follow_redirects=True) as c:
        kev = {v["cveID"] for v in c.get(KEV).json().get("vulnerabilities", [])}
        print(f"catalogue KEV : {len(kev)} failles exploitées")
        liens = []
        for f in FLUX:
            if not lire.autorise(c, f):
                print(f"robots.txt refuse {f}")
                continue
            try:
                r = c.get(f)
                liens += [e["lien"] for e in lire.elements(r.text) if e["lien"]]
            except Exception as e:
                print(f"{f} : {type(e).__name__}")
        rd = {"exploitees_dites": 0, "exploitees_dans_kev": 0, "cve_kev_dites_exploitees": 0, "secondes": []}
        res, ctl, durees = [], {"articles_lus": 0, "citations_rejetees": 0, "affirmations": 0,
                                "exploitees_dites": 0, "exploitees_dans_kev": 0, "cve_kev_dans_texte": 0,
                                "cve_kev_dites_exploitees": 0}, []
        for u in liens:
            if ctl["articles_lus"] >= MAX_ARTICLES:
                break
            t = lire.texte_page(c, u)
            if not t or not lire.CVE_RE.search(t):
                continue
            t = t[:MAX_CARACTERES]
            ctl["articles_lus"] += 1
            f, d = appeler(c, t)
            durees.append(d)
            if f is None:
                continue
            nt = lire.normal(t)
            ok = lambda cit: bool(cit) and lire.normal(cit) in nt
            expl = [x for x in f["failles_exploitees"] if ok(x["citation"]) and x["cve"] in t]
            act = [a for a in f["acteurs"] if ok(a["citation"]) and lire.normal(a["nom"]) in nt]
            ctl["affirmations"] += len(f["failles_exploitees"]) + len(f["acteurs"])
            ctl["citations_rejetees"] += len(f["failles_exploitees"]) - len(expl) + len(f["acteurs"]) - len(act)
            dites = {x["cve"] for x in expl}
            ctl["exploitees_dites"] += len(dites)
            ctl["exploitees_dans_kev"] += len(dites & kev)
            kev_texte = set(lire.CVE_RE.findall(t)) & kev
            ctl["cve_kev_dans_texte"] += len(kev_texte)
            ctl["cve_kev_dites_exploitees"] += len(kev_texte & dites)
            sp, dr = redefiner(t)
            red = None
            if sp is not None:
                rd["secondes"].append(dr)
                cves_r = {x["text"] for x in sp if x["label"] == "EXPLOITED_CVE" and lire.CVE_RE.fullmatch(x["text"])}
                rd["exploitees_dites"] += len(cves_r)
                rd["exploitees_dans_kev"] += len(cves_r & kev)
                rd["cve_kev_dites_exploitees"] += len(kev_texte & cves_r)
                red = {"exploitees": sorted(cves_r), "hors_catalogue_kev": sorted(cves_r - kev),
                       "acteurs": sorted({(x["text"], x["label"]) for x in sp if x["label"] != "EXPLOITED_CVE"})}
            res.append({"lien": u, "exploitees": sorted(dites), "hors_catalogue_kev": sorted(dites - kev),
                        "kev_non_signalees": sorted(kev_texte - dites),
                        "acteurs": [{k: a[k] for k in ("nom", "role", "pays")} for a in act], "redefiner": red})
            if red:
                print(f"        ReDefiNER {dr:5.1f} s : exploitées {red['exploitees']}  acteurs {red['acteurs'][:8]}", flush=True)
            print(f"{d:6.1f} s  exploitées {sorted(dites)} (hors KEV {sorted(dites - kev)})  "
                  f"acteurs {[(a['nom'], a['role']) for a in act]}  {u}", flush=True)
    fid = {"date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "modele": nom, **ctl,
           "fidelite_citations": round(1 - ctl["citations_rejetees"] / ctl["affirmations"], 3) if ctl["affirmations"] else None,
           "precision_exploitation_kev": round(ctl["exploitees_dans_kev"] / ctl["exploitees_dites"], 3) if ctl["exploitees_dites"] else None,
           "rappel_exploitation_kev": round(ctl["cve_kev_dites_exploitees"] / ctl["cve_kev_dans_texte"], 3) if ctl["cve_kev_dans_texte"] else None,
           "secondes_par_article": round(sum(durees) / len(durees), 1) if durees else None,
           "redefiner": {"exploitees_dites": rd["exploitees_dites"], "exploitees_dans_kev": rd["exploitees_dans_kev"],
                         "precision_exploitation_kev": round(rd["exploitees_dans_kev"] / rd["exploitees_dites"], 3)
                         if rd["exploitees_dites"] else None,
                         "rappel_exploitation_kev": round(rd["cve_kev_dites_exploitees"] / ctl["cve_kev_dans_texte"], 3)
                         if ctl["cve_kev_dans_texte"] else None,
                         "secondes_par_article": round(sum(rd["secondes"]) / len(rd["secondes"]), 1) if rd["secondes"] else None},
           "note": "précision : part des failles dites exploitées présentes au catalogue KEV ; rappel : part des failles "
                   "du texte présentes au catalogue que le modèle a dites exploitées (une faille du catalogue n'est pas "
                   "forcément dite exploitée dans l'article : le rappel est une borne basse)"}
    out = Path("resultats")
    (out / f"faits-presse-{nom}.json").write_text(json.dumps({"modele": nom, "articles": res}, ensure_ascii=False,
                                                            indent=1), encoding="utf-8")
    (out / f"fidelite-presse-{nom}.json").write_text(json.dumps(fid, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(fid, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(principal())
