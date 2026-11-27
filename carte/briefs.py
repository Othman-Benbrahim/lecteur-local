"""Page « briefs » (demande de l'auteur du 2026-11-25, 12 h 41) : briefs standardisés produits par le bot (brief.py),
lus dans son journal briefs/journal.jsonl (lecture seule) ; écrit docs/briefs.json, affiché par docs/briefs.html.
docs/briefs.json garde les 90 derniers jours ; tous les briefs restent archivés par mois dans docs/briefs-archives/AAAA-MM.json
(liste des mois dans docs/briefs-archives/index.json), consultables depuis la page. Seuls les briefs réussis (statut « ok »)
sont publiés ; le journal complet reste dans le dépôt du bot.

    python carte/briefs.py <chemin vers metac-bot-template>
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ICI = Path(__file__).resolve().parent.parent
SORTIE = ICI / "docs" / "briefs.json"
JOURS = 90


RECOUPEMENT: dict = {}


def _forme(x: dict) -> dict:
    """2026-11-25 : « recoupement » = contexte chiffré et recoupement des chiffres (bot : recoupement_briefs.py)."""
    return {"jour": x.get("jour"), "url": x.get("url"), "media": x.get("media"), "classe": x.get("classe"),
            "modele": x.get("modele"), "page_lue": x.get("page_lue", True), **x["brief"],
            "recoupement": RECOUPEMENT.get(x.get("fichier") or "")}


def _resume(briefs: list[dict]) -> dict:
    statuts = Counter(f["statut"].split(" (")[0] for b in briefs for f in b.get("faits", []))
    return {"briefs": len(briefs), "jours": len({b["jour"] for b in briefs}),
            "dernier": briefs[0]["jour"] if briefs else None, "faits": dict(statuts)}


def lire(bot: Path) -> list[dict]:
    f = bot / "briefs" / "journal.jsonl"
    lignes = []
    if f.is_file():
        for l in f.read_text(encoding="utf-8").splitlines():
            try:
                x = json.loads(l)
            except ValueError:
                continue
            if x.get("statut") == "ok" and isinstance(x.get("brief"), dict):
                lignes.append(x)
    lignes.sort(key=lambda x: (x.get("jour", ""), x.get("brief", {}).get("titre", "")), reverse=True)
    return lignes


def construire(bot: Path, lignes: list[dict] | None = None) -> dict:
    lignes = lire(bot) if lignes is None else lignes
    limite = (date.today() - timedelta(days=JOURS)).isoformat()
    lignes = [x for x in lignes if x.get("jour", "") >= limite]
    briefs = [_forme(x) for x in lignes]
    return {"genere_le": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "resume": _resume(briefs), "briefs": briefs}


def archiver(lignes: list[dict]) -> list[str]:
    """Un fichier par mois, réécrit à chaque passage (le journal du bot fait foi) ; rend la liste des mois."""
    par = {}
    for x in lignes:
        par.setdefault(str(x.get("jour", ""))[:7], []).append(_forme(x))
    d = ICI / "docs" / "briefs-archives"
    d.mkdir(parents=True, exist_ok=True)
    maintenant = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for mois, briefs in par.items():
        (d / f"{mois}.json").write_text(json.dumps({"genere_le": maintenant, "mois": mois, "resume": _resume(briefs),
                                                    "briefs": briefs}, ensure_ascii=False, indent=1), encoding="utf-8")
    mois = sorted(par, reverse=True)
    (d / "index.json").write_text(json.dumps({"mois": [{"mois": m, "briefs": len(par[m])} for m in mois]}, ensure_ascii=False), encoding="utf-8")
    return mois


if __name__ == "__main__":
    bot = Path(sys.argv[1]) if len(sys.argv) > 1 else ICI.parent / "metac-bot-template"
    f = bot / "registre" / "briefs-recoupement.json"
    if f.is_file():
        RECOUPEMENT.update(json.loads(f.read_text(encoding="utf-8")))
    tout = lire(bot)
    d = construire(bot, tout)
    # 2026-11-27 (plan validé par l'auteur à 23 h 36) : couche non visible du bot (couches.py) — seules les alertes
    # de signaux faibles et le nuage de mots des articles non traités sont publiés (liens, titres, sources ; aucun texte)
    fc = bot / "registre" / "couche-invisible.json"
    if fc.is_file():
        try:
            c = json.loads(fc.read_text(encoding="utf-8"))
            d["couche_invisible"] = {k: c.get(k) for k in ("jour", "articles", "traites", "historique_jours",
                                                         "en_ombre_jusqu_au", "seuils", "alertes", "nuage")}
        except ValueError:
            pass
    SORTIE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print("briefs :", d["resume"], "; archives :", archiver(tout))
