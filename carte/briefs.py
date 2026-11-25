"""Page « briefs » (demande de l'auteur du 2026-11-25, 12 h 41) : briefs standardisés produits par le bot (brief.py),
lus dans son journal briefs/journal.jsonl (lecture seule) ; écrit docs/briefs.json, affiché par docs/briefs.html.
On garde les 90 derniers jours ; seuls les briefs réussis (statut « ok ») sont publiés.

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


def construire(bot: Path) -> dict:
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
    limite = (date.today() - timedelta(days=JOURS)).isoformat()
    lignes = [x for x in lignes if x.get("jour", "") >= limite]
    lignes.sort(key=lambda x: (x.get("jour", ""), x.get("brief", {}).get("titre", "")), reverse=True)
    briefs = [{"jour": x.get("jour"), "url": x.get("url"), "media": x.get("media"), "classe": x.get("classe"),
               "modele": x.get("modele"), "page_lue": x.get("page_lue", True), **x["brief"]} for x in lignes]
    statuts = Counter(f["statut"].split(" (")[0] for b in briefs for f in b.get("faits", []))
    return {"genere_le": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "resume": {"briefs": len(briefs), "jours": len({b["jour"] for b in briefs}),
                       "dernier": briefs[0]["jour"] if briefs else None, "faits": dict(statuts)},
            "briefs": briefs}


if __name__ == "__main__":
    d = construire(Path(sys.argv[1]) if len(sys.argv) > 1 else ICI.parent / "metac-bot-template")
    SORTIE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print("briefs :", d["resume"])
