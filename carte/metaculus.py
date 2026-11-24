"""Tableau Metaculus (décision de l'auteur du 2026-11-24, 18 h 43) : questions traitées par le bot, prévisions publiées,
issues à échéance, taux de réussite. Lu dans le registre du bot (lecture seule) ; écrit docs/metaculus.json.

Sources (registre du bot) :
- journal-bot.jsonl (questions binaires) et journal-autres.jsonl (autres types) : prévisions publiées, scellées ;
  lignes d'essai exclues (mode « test_questions » ou publie = false), comme score_registre.py ;
- issues.jsonl (binaires, 0/1) et issues-autres.jsonl (texte de résolution de Metaculus), relevées chaque lundi par le
  workflow « Score du bot alphabet » ;
- journal-<brique>.jsonl : brique mesurée appliquée à la question (dernière application).
Scores des binaires : réussite (prévision du bon côté de 50 %), Brier, score de référence de Metaculus
100 × (log2 p + 1) (0 = pile ou face, 100 = certitude juste).

    python carte/metaculus.py <chemin vers metac-bot-template>
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ICI = Path(__file__).resolve().parent.parent
SORTIE = ICI / "docs" / "metaculus.json"
BRIQUES = {"journal-seuils.jsonl": "seuils de prix", "journal-evenements.jsonl": "événements comptés",
           "journal-maintien.jsonl": "maintien au pouvoir", "journal-escalade.jsonl": "escalade",
           "journal-competitions.jsonl": "compétitions", "journal-banque-centrale.jsonl": "banque centrale",
           "journal-accords.jsonl": "accords (guerres, rencontres, justice…)"}
TYPES = {"binaire": "binaire", "numerique": "numérique", "choix_multiple": "choix multiple", "date": "date",
         "conditionnelle": "conditionnelle", "discrete": "discrète"}


def _jsonl(f: Path) -> list[dict]:
    if not f.is_file():
        return []
    out = []
    for l in f.read_text(encoding="utf-8").splitlines():
        if l.strip():
            try:
                out.append(json.loads(l))
            except ValueError:
                pass
    return out


def _essai(x: dict) -> bool:
    return x.get("publie") is False or x.get("mode") == "test_questions"


def _borne(p: float) -> float:
    return min(max(float(p), 0.001), 0.999)


def _resume_autre(x: dict) -> str:
    p = x.get("prediction") or {}
    if isinstance(p, dict) and p.get("predicted_options"):
        o = max(p["predicted_options"], key=lambda z: z.get("probability", 0))
        return f"{o.get('option_name')} ({o.get('probability', 0):.0%})"
    if isinstance(p, dict) and p.get("declared_percentiles"):
        med = [d for d in p["declared_percentiles"] if abs(d.get("percentile", 0) - 0.5) < 0.11]
        d = (med or p["declared_percentiles"])[len(med or p["declared_percentiles"]) // 2]
        v = d.get("value")
        if isinstance(v, (int, float)):
            t = f"{v:,.0f}".replace(",", " ") if abs(v) >= 1000 else f"{v:.3g}"
            return f"médiane ≈ {t}"
        return f"médiane ≈ {v}"
    return "—"


def construire(bot: Path) -> dict:
    reg = bot / "registre"
    auj = date.today().isoformat()
    issues = {x["question_id"]: int(x["issue"]) for x in _jsonl(reg / "issues.jsonl") if "issue" in x}
    issues_autres = {x["question_id"]: x.get("issue") for x in _jsonl(reg / "issues-autres.jsonl")}
    briques = {}
    for f, nom in BRIQUES.items():
        for x in _jsonl(reg / f):
            q = str(x.get("question_id"))
            briques[q] = {"brique": nom, "p_bot": x.get("p_pipeline"), "p_poste": x.get("p_poste"),
                          "le": x.get("date_information")}
    Q: dict[str, dict] = {}
    for x in _jsonl(reg / "journal-bot.jsonl"):
        if _essai(x):
            continue
        q = str(x["question_id"])
        e = Q.setdefault(q, {"id": q, "type": "binaire", "mises_a_jour": 0, "historique": []})
        e["mises_a_jour"] += 1
        e["historique"].append([x.get("date_information"), round(float(x["p_poste"]), 4)])
        e.update({"titre": x.get("titre"), "url": x.get("url"), "echeance": x.get("resolution_prevue"),
                  "prevision": round(float(x["p_poste"]), 4), "date_prevision": x.get("date_information")})
    for x in _jsonl(reg / "journal-autres.jsonl"):
        if _essai(x):
            continue
        q = str(x["question_id"])
        e = Q.setdefault(q, {"id": q, "type": TYPES.get(x.get("type"), x.get("type") or "?"), "mises_a_jour": 0,
                             "historique": []})
        e["mises_a_jour"] += 1
        e.update({"titre": x.get("titre"), "url": x.get("url"), "echeance": x.get("resolution_prevue"),
                  "prevision_texte": _resume_autre(x), "date_prevision": x.get("date_information")})
    for q, e in Q.items():
        if e["type"] == "binaire" and q in issues:
            y, p = issues[q], _borne(e["prevision"])
            e.update({"statut": "résolue", "issue": "oui" if y else "non", "reussite": (p >= .5) == bool(y),
                      "brier": round((p - y) ** 2, 4),
                      "score_reference": round(100 * (math.log2(p if y else 1 - p) + 1), 1)})
        elif q in issues_autres:
            e.update({"statut": "résolue", "issue": str(issues_autres[q])})
        elif e.get("echeance") and e["echeance"] < auj:
            e["statut"] = "échéance passée, issue à relever"
        else:
            e["statut"] = "ouverte"
        if q in briques:
            e["brique"] = briques[q]
    L = sorted(Q.values(), key=lambda z: (z.get("echeance") or "9999", z["id"]))
    R = [e for e in L if e["type"] == "binaire" and e.get("statut") == "résolue"]
    moy = lambda v: round(sum(v) / len(v), 3) if v else None
    stats = {"questions": len(L), "binaires": sum(e["type"] == "binaire" for e in L),
             "ouvertes": sum(e["statut"] == "ouverte" for e in L),
             "a_relever": sum(e["statut"].startswith("échéance") for e in L),
             "resolues": sum(e["statut"] == "résolue" for e in L), "resolues_binaires": len(R),
             "taux_reussite": moy([1.0 if e["reussite"] else 0.0 for e in R]),
             "brier_moyen": moy([e["brier"] for e in R]),
             "score_reference_moyen": moy([e["score_reference"] for e in R]),
             "avec_brique": sum("brique" in e for e in L),
             "prochaine_echeance": next((e["echeance"] for e in L if e["statut"] == "ouverte" and e.get("echeance")), None)}
    cal = []                                          # calibration des binaires résolues, par tranche de 20 points
    for lo in range(0, 100, 20):
        T = [e for e in R if lo / 100 <= e["prevision"] < (lo + 20) / 100 or (lo == 80 and e["prevision"] == 1)]
        if T:
            cal.append({"tranche": f"{lo}-{lo + 20} %", "n": len(T), "prevision_moyenne": moy([e["prevision"] for e in T]),
                        "part_oui": moy([1.0 if e["issue"] == "oui" else 0.0 for e in T])})
    stats["calibration"] = cal
    par_brique = {}
    for e in R:
        b = (e.get("brique") or {}).get("brique", "sans brique (prévision du bot)")
        par_brique.setdefault(b, []).append(e)
    stats["par_brique"] = {b: {"n": len(T), "taux_reussite": moy([1.0 if e["reussite"] else 0.0 for e in T]),
                               "score_reference_moyen": moy([e["score_reference"] for e in T])}
                           for b, T in par_brique.items()}
    return {"genere_le": datetime.now(timezone.utc).isoformat(timespec="minutes"), "stats": stats, "questions": L}


def principal() -> int:
    bot = Path(sys.argv[1] if len(sys.argv) > 1 else "../metac-bot-template").resolve()
    d = construire(bot)
    SORTIE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    s = d["stats"]
    print(f"metaculus : {s['questions']} questions, {s['resolues']} résolues, {s['ouvertes']} ouvertes → {SORTIE}")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
