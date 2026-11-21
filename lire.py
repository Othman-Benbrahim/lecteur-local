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
# « https://cert.cyber.gouv.fr/flux-rss/ » est une page HTML (passage 44939079964) : flux réels, essayés dans l'ordre
FLUX_CANDIDATS = ["https://www.cert.ssi.gouv.fr/feed/", "https://cert.cyber.gouv.fr/feed/"]
SERVEUR = "http://127.0.0.1:8080/v1/chat/completions"
SORTIE = Path("resultats")
MAX_ELEMENTS = 25
MAX_CARACTERES = 2500                                         # borne le temps de lecture (environ 14 jetons/s à l'entrée)

# Passage 44939689290 : sur le seul résumé du flux (une ligne), le modèle a INVENTÉ 58 identifiants CVE sur 61
# (« CVE-2026-1234 », « CVE-2026-5678 »), mis « CERT-FR » comme éditeur et classé presque tout en « bulletin ».
# Correctifs : (1) le modèle lit la page de l'avis (robots.txt vérifié), pas le résumé ; (2) tout ce qu'une règle peut
# lire est lu par le code (identifiants CVE, type d'après l'adresse, risques au vocabulaire fixe du CERT-FR) ; (3) le
# modèle ne rend que ce que le code ne sait pas lire, et chaque affirmation doit venir avec une CITATION EXACTE du texte,
# que le code vérifie mot pour mot : une affirmation sans citation retrouvée est rejetée et comptée.
SCHEMA = {
    "type": "object",
    "properties": {
        "editeurs": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 60}},
        "exploitation_active": {"type": "boolean"},
        "citation_exploitation": {"type": ["string", "null"], "maxLength": 200},
        "attaquant_nomme": {"type": ["string", "null"], "maxLength": 60},
        "role_du_nom": {"type": ["string", "null"], "enum": ["auteur de l'attaque", "chercheur ou rapporteur", "victime",
                                                              "éditeur", None]},
        "pays_nomme": {"type": ["string", "null"], "maxLength": 40},
        "citation_attaquant_ou_pays": {"type": ["string", "null"], "maxLength": 200},
    },
    "required": ["editeurs", "exploitation_active", "citation_exploitation", "attaquant_nomme", "role_du_nom", "pays_nomme",
                 "citation_attaquant_ou_pays"],
    "additionalProperties": False,
}
CONSIGNE = ("Tu lis une publication de l'agence française de cybersécurité (CERT-FR). Réponds UNIQUEMENT par le JSON "
            "demandé. Règles strictes : n'invente rien ; si l'information n'est pas écrite, mets une liste vide, faux "
            "ou null. editeurs = les éditeurs ou fabricants des produits concernés, écrits exactement comme dans le "
            "texte (jamais « CERT-FR » ni « ANSSI »). exploitation_active = vrai seulement si le texte dit que la "
            "vulnérabilité est exploitée ; alors citation_exploitation = la phrase exacte du texte qui le dit, copiée "
            "mot pour mot. attaquant_nomme, pays_nomme = un groupe d'attaquants ou un pays nommé comme auteur d'une "
            "attaque, sinon null ; alors citation_attaquant_ou_pays = la phrase exacte, copiée mot pour mot, et "
            "role_du_nom = le rôle que la phrase donne à ce nom. Attention : une entreprise qui DÉCOUVRE ou SIGNALE une "
            "attaque (par exemple Google, Mandiant, Microsoft) n'est pas l'attaquant.")
RISQUES = ["exécution de code arbitraire à distance", "exécution de code arbitraire", "déni de service à distance",
           "déni de service", "élévation de privilèges", "atteinte à la confidentialité des données",
           "atteinte à l'intégrité des données", "contournement de la politique de sécurité", "injection de code indirecte",
           "usurpation d'identité", "falsification de requêtes côté serveur"]
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


def texte_page(c: httpx.Client, url: str) -> str | None:
    """Texte de la page de l'avis, lu le temps du passage (jamais gardé)."""
    if not autorise(c, url):
        return None
    r = c.get(url)
    if r.status_code != 200:
        return None
    h = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", r.text)
    # le corps de l'article d'abord (<article>), puis <main> : les barres latérales (« articles liés ») avaient fait
    # lire « Russian APT » ou « Chinese APT » dans des articles qui n'en parlaient pas (passage 44951299117)
    m = re.search(r"(?is)<article[^>]*>(.*?)</article>", h) or re.search(r"(?is)<main[^>]*>(.*?)</main>", h)
    corps = m.group(1) if m else h
    corps = re.sub(r"(?is)<(aside|figure)[^>]*>.*?</\1>", " ", corps)
    corps = re.sub(r'(?is)<div[^>]+class="[^"]*(related|sidebar|newsletter|share|comments?|recommend)[^"]*"[^>]*>.*?</div>', " ", corps)
    t = re.sub(r"<[^>]+>", " ", corps)
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(t)).strip()


def normal(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").lower().replace("’", "'")).strip()


def verifier(f: dict, texte: str) -> tuple[dict, dict]:
    """Ne garde que les affirmations dont la preuve est dans le texte ; compte les rejets."""
    nt, rej = normal(texte), {"editeurs": 0, "exploitation": 0, "attaquant_pays": 0}
    eds = [e for e in f.get("editeurs", []) if normal(e) and normal(e) in nt and normal(e) not in ("cert-fr", "anssi")]
    rej["editeurs"] = len(f.get("editeurs", [])) - len(eds)
    expl = bool(f.get("exploitation_active"))
    if expl and not (f.get("citation_exploitation") and normal(f["citation_exploitation"]) in nt):
        expl, rej["exploitation"] = False, 1
    att, pays = f.get("attaquant_nomme"), f.get("pays_nomme")
    if att and f.get("role_du_nom") != "auteur de l'attaque":    # passage 44941605565 : « Google et Mandiant » (qui ont
        att = None                                              # signalé l'attaque) rendus comme attaquants
    if (att or pays) and not (f.get("citation_attaquant_ou_pays") and normal(f["citation_attaquant_ou_pays"]) in nt
                              and all(normal(x) in nt for x in (att, pays) if x)):
        att, pays, rej["attaquant_pays"] = None, None, 1
    return {"editeurs": eds, "exploitation_active": expl, "attaquant_nomme": att, "pays_nomme": pays}, rej


def extraire(c: httpx.Client, texte: str) -> tuple[dict | None, float]:
    t0 = time.time()
    r = c.post(SERVEUR, json={"messages": [{"role": "system", "content": CONSIGNE}, {"role": "user", "content": texte[:6000]}],
                              "temperature": 0, "max_tokens": 600,
                              "response_format": {"type": "json_schema", "json_schema": {"name": "fait", "schema": SCHEMA}}},
               timeout=900)
    duree = time.time() - t0
    try:
        ch = r.json()["choices"][0]
        return json.loads(ch["message"]["content"]), duree
    except Exception as e:                                     # on montre POURQUOI rien n'est lu (passage Gemma 4 E4B)
        try:
            ch = r.json().get("choices", [{}])[0]
            print(f"  réponse illisible ({type(e).__name__}) : HTTP {r.status_code}, fin {ch.get('finish_reason')}, "
                  f"contenu {str(ch.get('message', {}).get('content'))[:150]!r}, "
                  f"raisonnement {len(str(ch.get('message', {}).get('reasoning_content') or ''))} car.", flush=True)
        except Exception:
            print(f"  réponse illisible : HTTP {r.status_code}, {r.text[:200]!r}", flush=True)
        return None, duree


def principal() -> int:
    SORTIE.mkdir(exist_ok=True)
    with httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as c:
        els, FLUX = [], None
        for u in FLUX_CANDIDATS:
            if not autorise(c, u):
                print(f"robots.txt refuse {u}")
                continue
            r = c.get(u)
            if r.status_code == 200 and r.text.lstrip()[:200].lower().find("<rss") >= 0 or "<feed" in r.text[:500].lower():
                try:
                    els, FLUX = elements(r.text)[:MAX_ELEMENTS], str(r.url)
                    break
                except ET.ParseError as e:
                    print(f"{u} : XML illisible ({e})")
            else:
                print(f"{u} : pas un flux (HTTP {r.status_code}, début : {r.text[:60]!r})")
        if not els:
            print("aucun flux lisible ; rien lu")
            return 1
        print(f"flux : {FLUX}, {len(els)} éléments")
        faits, durees = [], []
        controle = {"pages_lues": 0, "illisibles": 0, "editeurs_proposes": 0, "editeurs_rejetes": 0,
                    "exploitations_proposees": 0, "exploitations_rejetees": 0, "attaquants_pays_proposes": 0,
                    "attaquants_pays_rejetes": 0}
        for e in els:
            t = texte_page(c, e["lien"]) if e["lien"] else None
            if not t:
                controle["illisibles"] += 1
                continue
            controle["pages_lues"] += 1
            t = t[:MAX_CARACTERES]
            f, d = extraire(c, t)
            durees.append(d)
            if f is None:
                controle["illisibles"] += 1
                print(f"{d:5.1f} s  ILLISIBLE  {e['lien']}", flush=True)
                continue
            garde, rej = verifier(f, t)
            controle["editeurs_proposes"] += len(f.get("editeurs", []))
            controle["editeurs_rejetes"] += rej["editeurs"]
            controle["exploitations_proposees"] += int(bool(f.get("exploitation_active")))
            controle["exploitations_rejetees"] += rej["exploitation"]
            controle["attaquants_pays_proposes"] += int(bool(f.get("attaquant_nomme") or f.get("pays_nomme")))
            controle["attaquants_pays_rejetes"] += rej["attaquant_pays"]
            m = re.search(r"/(avis|alerte|cti|ioc|actualite|dur)/", e["lien"])
            nt = normal(t)
            faits.append({"lien": e["lien"], "date": e["date"], "type": m.group(1) if m else "autre",
                          "cve": sorted(set(CVE_RE.findall(t))),
                          "risques": [r_ for r_ in RISQUES if r_ in nt and not any(r_ != q and r_ in q and q in nt
                                                                                    for q in RISQUES)], **garde})
            print(f"{d:5.1f} s  {faits[-1]['type']:8s} {len(faits[-1]['cve']):2d} CVE  éditeurs {garde['editeurs']}  "
                  f"exploitée {garde['exploitation_active']}  rejets {rej}  {e['lien']}", flush=True)
    propose = controle["editeurs_proposes"] + controle["exploitations_proposees"] + controle["attaquants_pays_proposes"]
    rejete = controle["editeurs_rejetes"] + controle["exploitations_rejetees"] + controle["attaquants_pays_rejetes"]
    fid = {"date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "elements": len(els), **controle,
           "affirmations_du_modele": propose, "rejetees_faute_de_preuve": rejete,
           "fidelite": round(1 - rejete / propose, 3) if propose else None,
           "secondes_par_element": round(sum(durees) / len(durees), 1) if durees else None,
           "note": "identifiants CVE, type et risques lus par le code ; le modèle ne rend que les éditeurs, "
                   "l'exploitation active et l'attaquant ou le pays, chacun vérifié mot pour mot dans le texte"}
    nom = sys.argv[1] if len(sys.argv) > 1 else "modele"
    fid["modele"] = nom
    (SORTIE / f"faits-cyber-{nom}.json").write_text(json.dumps({"source": FLUX, "modele": nom, "faits": faits},
                                                              ensure_ascii=False, indent=1), encoding="utf-8")
    (SORTIE / f"fidelite-{nom}.json").write_text(json.dumps(fid, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(fid, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(principal())
