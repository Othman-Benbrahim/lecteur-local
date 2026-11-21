"""Premier passage du lecteur local : le flux public du CERT-FR (ANSSI) lu par un modèle ouvert servi en local par
llama-server (API compatible OpenAI sur http://127.0.0.1:8080).

Pour chaque élément du flux (titre et résumé seulement, tels que publiés dans le flux) le modèle rend un JSON fermé :
type de publication, produits ou éditeurs visés, identifiants CVE, gravité annoncée, attaquant nommé, pays nommé.
Contrôle de fidélité : les identifiants CVE rendus par le modèle sont comparés à ceux qu'une expression régulière trouve
dans le même texte (vérité connue) ; rappel, précision et inventions sont publiés.

Sorties : resultats/faits-cyber.json (champs extraits + lien, jamais le texte) et resultats/fidelite.json.

    python lire.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import httpx

UA = "lecteur-local/0.1 (recherche non commerciale ; https://github.com/Othman-Benbrahim/lecteur-local)"
FLUX = "https://cert.cyber.gouv.fr/flux-rss/"
SERVEUR = "http://127.0.0.1:8080/v1/chat/completions"
SORTIE = Path("resultats")
MAX_ELEMENTS = 40

SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["avis", "alerte", "incident", "menace", "bulletin", "autre"]},
        "editeurs": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "cve": {"type": "array", "items": {"type": "string", "pattern": "^CVE-[0-9]{4}-[0-9]{4,7}$"}, "maxItems": 40},
        "gravite": {"type": "string", "enum": ["critique", "elevee", "moyenne", "faible", "non_precisee"]},
        "exploitation_active": {"type": "boolean"},
        "attaquant_nomme": {"type": ["string", "null"]},
        "pays_nomme": {"type": ["string", "null"]},
    },
    "required": ["type", "editeurs", "cve", "gravite", "exploitation_active", "attaquant_nomme", "pays_nomme"],
    "additionalProperties": False,
}
CONSIGNE = ("Tu lis une publication de l'agence française de cybersécurité (CERT-FR). Réponds UNIQUEMENT par le JSON "
            "demandé. Règles strictes : ne recopie que ce qui est écrit dans le texte, n'invente rien ; cve = les "
            "identifiants CVE écrits dans le texte, aucun autre ; exploitation_active = vrai seulement si le texte dit "
            "que la faille est exploitée ; attaquant_nomme et pays_nomme = null si le texte n'en nomme aucun ; "
            "gravite = non_precisee si le texte ne la dit pas.")
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}")


def autorise(c: httpx.Client, url: str) -> bool:
    base = re.match(r"https?://[^/]+", url).group(0)
    try:
        r = c.get(base + "/robots.txt")
        if r.status_code >= 400:
            return True
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp.can_fetch(UA, url)
    except Exception:
        return True


def elements(xml: str) -> list[dict]:
    racine = ET.fromstring(xml)
    out = []
    for it in racine.iter("item"):
        g = lambda t: (it.findtext(t) or "").strip()
        texte = re.sub(r"<[^>]+>", " ", f"{g('title')}. {g('description')}")
        out.append({"lien": g("link"), "date": g("pubDate"), "texte": re.sub(r"\s+", " ", texte).strip()})
    return out


def extraire(c: httpx.Client, texte: str) -> tuple[dict | None, float]:
    t0 = time.time()
    r = c.post(SERVEUR, json={"messages": [{"role": "system", "content": CONSIGNE}, {"role": "user", "content": texte[:6000]}],
                              "temperature": 0, "max_tokens": 600,
                              "response_format": {"type": "json_schema", "json_schema": {"name": "fait", "schema": SCHEMA}}},
               timeout=900)
    duree = time.time() - t0
    try:
        return json.loads(r.json()["choices"][0]["message"]["content"]), duree
    except Exception:
        return None, duree


def principal() -> int:
    SORTIE.mkdir(exist_ok=True)
    with httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as c:
        if not autorise(c, FLUX):
            print("robots.txt refuse le flux ; rien lu")
            return 0
        els = elements(c.get(FLUX).text)[:MAX_ELEMENTS]
        faits, controle, durees = [], {"vrais": 0, "trouves": 0, "inventes": 0, "manques": 0, "illisibles": 0}, []
        for e in els:
            f, d = extraire(c, e["texte"])
            durees.append(d)
            if f is None:
                controle["illisibles"] += 1
                continue
            vrais, rendus = set(CVE_RE.findall(e["texte"])), set(f.get("cve", []))
            controle["vrais"] += len(vrais)
            controle["trouves"] += len(vrais & rendus)
            controle["inventes"] += len(rendus - vrais)
            controle["manques"] += len(vrais - rendus)
            faits.append({"lien": e["lien"], "date": e["date"], **f})
            print(f"{d:5.1f} s  {f['type']:9s} {len(rendus)} CVE  {e['lien']}", flush=True)
    fid = {"date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "elements": len(els), **controle,
           "rappel_cve": round(controle["trouves"] / controle["vrais"], 3) if controle["vrais"] else None,
           "precision_cve": round(controle["trouves"] / (controle["trouves"] + controle["inventes"]), 3)
           if controle["trouves"] + controle["inventes"] else None,
           "secondes_par_element": round(sum(durees) / len(durees), 1) if durees else None}
    (SORTIE / "faits-cyber.json").write_text(json.dumps({"source": FLUX, "faits": faits}, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    (SORTIE / "fidelite.json").write_text(json.dumps(fid, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(fid, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(principal())
