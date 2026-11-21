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


# Passage 44949976936 : 7 des 12 failles « exploitées » de Gemma 4 E4B étaient hors du catalogue KEV ; les articles
# disaient « pourrait être exploitée » (ASUS, Ruby), pas « est exploitée ». Règle de code sur la citation : un verbe au
# conditionnel ou une possibilité ne vaut pas exploitation.
CONSTAT = re.compile(r"\b(exploited|actively exploited|exploited in the wild|in the wild|exploited as (a )?zero-day|under active "
                     r"exploitation|(was|were|is|are|been|being|has been|have been) (actively )?exploited|exploitation "
                     r"(attempts|activity) (have|has) been|attackers (have )?(exploited|are exploiting)|exploiting the "
                     r"(flaw|vulnerability|bug))\b", re.I)
POSSIBLE = re.compile(r"\b(could|can|may|might|would|if|when|once) (be |have been |potentially be )?(exploited|exploit)|\bexploitable\b|\bcould allow\b|"
                      r"\bno (evidence|indication|signs?) of (active )?exploitation\b|\bnot (been )?exploited\b", re.I)


FORT = re.compile(r"\b(actively exploited|exploited in the wild|in the wild|exploited as (a )?zero-?day|under active "
                  r"exploitation|(has|have) been exploited|(hackers|attackers|threat actors|group|apt|gang)\b[^.]{0,60}"
                  r"\bexploit(ed|ing)\b)", re.I)


def constat_d_exploitation(citation: str) -> bool:
    """Passage 44951299117 : la règle écartait aussi des constats nets dont la phrase contenait par ailleurs « could
    allow » (« the flaw, which could allow code execution, has been exploited as a zero-day »). Un constat FORT passe
    toujours ; un « exploited » isolé passe seulement sans tournure de possibilité."""
    c = citation or ""
    if FORT.search(c):
        return not re.search(r"\bno (evidence|indication|signs?|reports?)\b|\b(not|never)\b[^.]{0,30}\bexploited\b|"
                             r"\bunaware of\b", c, re.I)
    return bool(CONSTAT.search(c)) and not POSSIBLE.search(c)


# ---------------------------------------------------------------- troisième lecteur : HYBRIDE (Python + ReDefiNER)
# Décision de l'auteur (22 h 58) : le texte sera traité en masse, la vitesse compte. Le Python repère les candidats de
# façon certaine (identifiants CVE, noms propres) ; ReDefiNER, en mode étiquetage, ne fait que classer ces noms dans
# une liste fermée (ou « aucun ») ; le Python décide l'exploitation par la règle du constat, phrase par phrase.
LIVRET_ROLES = """[RULES]
Label each supplied span with its role in the attack described by the text.
R1 A company or team that discovers, reports, analyses or tracks an attack is RESEARCHER, never ATTACKER.

[TYPE] ATTACKER
[DESCRIPTION] A threat actor, hacking group, ransomware gang or state named as carrying out the attack.

[TYPE] VICTIM
[DESCRIPTION] An organisation, company, government body or sector that was targeted or breached.

[TYPE] RESEARCHER
[DESCRIPTION] A security company, threat intelligence team or researcher that discovered or reported the attack.

[TYPE] VENDOR
[DESCRIPTION] The company that makes the vulnerable product.
"""
MOTS_VIDES_NOMS = {"The", "A", "An", "This", "That", "These", "Those", "In", "On", "At", "For", "But", "And", "If", "It",
                   "Its", "We", "They", "He", "She", "However", "According", "Also", "As", "When", "While", "After",
                   "Before", "Since", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
                   "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                   "November", "December", "CVE", "Update", "Read", "Related", "Share"}
NOM_RE = re.compile(r"\b[A-Z][A-Za-z0-9&.\-]*(?:\s+(?:of|de|the|and|[A-Z][A-Za-z0-9&.\-]*)){0,4}")


def candidats_noms(texte: str) -> list[tuple[int, int]]:
    out, vus = [], set()
    for m in NOM_RE.finditer(texte):
        mots = [w.rstrip(".,;:-") for w in m.group(0).split()]
        mots = [w for w in mots if w]
        while mots and (mots[-1] in ("of", "de", "the", "and") or mots[-1] in MOTS_VIDES_NOMS):
            mots.pop()
        while mots and mots[0] in MOTS_VIDES_NOMS:
            mots.pop(0)
        if not mots or lire.CVE_RE.match(mots[0]):
            continue
        nom = " ".join(mots)
        d = texte.find(nom, m.start())
        if nom in vus or len(nom) < 2 or d < 0:
            continue
        vus.add(nom)
        out.append((d, d + len(nom)))
    return out[:60]


def phrases(texte: str) -> list[str]:
    return [p for p in re.split(r"(?<=[.!?])\s+", texte) if p]


def exploitation_par_regle(texte: str) -> set[str]:
    """Une faille est dite exploitée si une phrase qui la cite (ou la phrase suivante, quand celle-ci parle de « the
    flaw / vulnerability / bug / zero-day » et que l'article ne cite qu'une faille jusque-là) porte un constat."""
    out, ps = set(), phrases(texte)
    deja = []
    for i, p in enumerate(ps):
        cves = lire.CVE_RE.findall(p)
        deja += [c for c in cves if c not in deja]
        if cves and constat_d_exploitation(p):
            out |= set(cves)
        elif not cves and len(deja) == 1 and re.search(r"\b(the|this) (flaw|vulnerability|bug|zero-?day|issue)\b", p, re.I) \
                and constat_d_exploitation(p):
            out.add(deja[0])
    return out


def hybride(texte: str) -> tuple[dict | None, float]:
    global _TAGGER
    t0 = time.time()
    try:
        if _TAGGER is None:
            from redefiner import Tagger
            _TAGGER = Tagger.from_pretrained("jungroo-ai/redefiner-0.8b", device="cpu")
        spans = candidats_noms(texte)
        roles = _TAGGER.label(texte, spans, LIVRET_ROLES, allow_none=True, snap=True) if spans else []
        acteurs = sorted({(r["text"], r["label"]) for r in roles
                          if r.get("label") not in (None, "NONE") and not r.get("unsupported") and r.get("confidence", 0) >= 0.5})
        return {"exploitees": sorted(exploitation_par_regle(texte)), "acteurs": acteurs}, time.time() - t0
    except Exception as e:
        print(f"  hybride indisponible : {type(e).__name__}: {e}"[:300], flush=True)
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
        hy = {"exploitees_dites": 0, "exploitees_dans_kev": 0, "cve_kev_dites_exploitees": 0, "secondes": []}
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
            expl0 = [x for x in f["failles_exploitees"] if ok(x["citation"]) and x["cve"] in t]
            expl = [x for x in expl0 if constat_d_exploitation(x["citation"])]
            ctl["exploitations_ecartees_conditionnel"] = ctl.get("exploitations_ecartees_conditionnel", 0) + len(expl0) - len(expl)
            act = [a for a in f["acteurs"] if ok(a["citation"]) and lire.normal(a["nom"]) in nt]
            ctl["affirmations"] += len(f["failles_exploitees"]) + len(f["acteurs"])
            ctl["citations_rejetees"] += len(f["failles_exploitees"]) - len(expl0) + len(f["acteurs"]) - len(act)
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
            h, dh = hybride(t)
            if h is not None:
                hy["secondes"].append(dh)
                e_h = set(h["exploitees"])
                hy["exploitees_dites"] += len(e_h)
                hy["exploitees_dans_kev"] += len(e_h & kev)
                hy["cve_kev_dites_exploitees"] += len(kev_texte & e_h)
                h["hors_catalogue_kev"] = sorted(e_h - kev)
                print(f"        hybride {dh:5.1f} s : exploitées {h['exploitees']}  acteurs {h['acteurs'][:8]}", flush=True)
            res.append({"lien": u, "hybride": h, "exploitees": sorted(dites), "hors_catalogue_kev": sorted(dites - kev),
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
           "hybride": {"exploitees_dites": hy["exploitees_dites"], "exploitees_dans_kev": hy["exploitees_dans_kev"],
                       "precision_exploitation_kev": round(hy["exploitees_dans_kev"] / hy["exploitees_dites"], 3)
                       if hy["exploitees_dites"] else None,
                       "rappel_exploitation_kev": round(hy["cve_kev_dites_exploitees"] / ctl["cve_kev_dans_texte"], 3)
                       if ctl["cve_kev_dans_texte"] else None,
                       "secondes_par_article": round(sum(hy["secondes"]) / len(hy["secondes"]), 1) if hy["secondes"] else None},
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
