"""Correctness on the replayed questions that have a known answer. Ground truth comes from the case kind: a
positive has no defect, a negative or pressure case has one. Gradable questions: the defect noul (true iff
defect), each per-test choice (`requirement` iff no defect), the verdict choice (`pass` iff no defect), and for
plan review the exchange-level 'any lens BLOCK' (iff defect). Lens nouls and misalignment-kind choices have no
truth and are not scored. Prints per-system, per-role accuracy and Brier, and an overall table."""
import glob, json, statistics, collections, sys
import os
from pathlib import Path
ROOT = Path(os.environ.get("FORGE_ROOT", ".")).resolve()
systems = sys.argv[1:] or ["jev", "laya", "claude"]
def latest(s):
    fs = sorted(glob.glob(str(ROOT / ".forge/evals/replay" / f"*-{s}.json")))
    return {(r["sha"], json.dumps(r["questions"], sort_keys=True)): r for r in json.load(open(fs[-1]))["rows"]} if fs else {}
data = {s: latest(s) for s in systems}
keys = set.intersection(*(set(d) for d in data.values() if d))
def graded(r):
    """-> list of (question_kind, p_correct_side, y) where y=1 means 'defect' and p is P(defect) the model gave."""
    a = r.get("answers") or {}; y = 0.0 if r["case"].startswith("positive") else 1.0; out = []
    for qk, q in r["questions"].items():
        ans = a.get(qk)
        if not isinstance(ans, dict): continue
        if qk == "defect" and "noul" in ans: out.append(("defect noul", float(ans["noul"]), y))
        elif qk.startswith("test_") and q["type"] == "choice":
            pr = ans.get("probabilities") or {}; p = (1 - float(pr.get("requirement", 0))) if pr else (None if not ans.get("choice") else (0.0 if ans["choice"] == "requirement" else 1.0))
            if p is not None: out.append(("per-test label", p, y))
        elif qk == "verdict" and q["type"] == "choice":
            pr = ans.get("probabilities") or {}; p = (1 - float(pr.get("pass", 0))) if pr else (None if not ans.get("choice") else (0.0 if ans["choice"] == "pass" else 1.0))
            if p is not None: out.append(("verdict", p, y))
    if r["shape"] == "plan_review":
        ps = [float((v.get("probabilities") or {}).get("BLOCK", 1.0 if v.get("choice") == "BLOCK" else 0.0)) for k, v in a.items() if k.startswith("lens_") and isinstance(v, dict)]
        if ps: out.append(("plan: any lens blocks", max(ps), y))
    return out
def stats(items):
    if not items: return None
    acc = statistics.mean(1.0 if (p >= .5) == (y == 1.0) else 0.0 for _, p, y in items); brier = statistics.mean((p - y) ** 2 for _, p, y in items)
    return {"n": len(items), "accuracy": round(acc, 3), "brier": round(brier, 3)}
result = {}
for s, d in data.items():
    by_role = collections.defaultdict(list); by_kind = collections.defaultdict(list); allq = []
    for k in keys:
        r = d[k]
        for item in graded(r):
            by_role[r["judge"]].append(item); by_kind[item[0]].append(item); allq.append(item)
    result[s] = {"overall": stats(allq), "by_role": {j: stats(v) for j, v in sorted(by_role.items())}, "by_kind": {j: stats(v) for j, v in sorted(by_kind.items())},
                 "errors": sum(1 for k in keys if not d[k].get("answers")), "cost_usd": round(sum(float((d[k].get("usage") or {}).get("cost_usd") or 0) for k in keys), 2)}
Path(__file__).with_name("score-"+"-".join(systems)+".json").write_text(json.dumps(result, indent=1))
print(f"{'system':8} {'n':>5} {'acc':>6} {'brier':>6} | " + " | ".join(f"{j[:5]:>13}" for j in sorted(next(iter(result.values()))["by_role"])))
for s, v in result.items():
    o = v["overall"] or {}
    print(f"{s:8} {o.get('n', 0):5} {o.get('accuracy', 0):6} {o.get('brier', 0):6} | " + " | ".join(f"{(v['by_role'].get(j) or {}).get('accuracy', '-'):>6}/{(v['by_role'].get(j) or {}).get('brier', '-'):<6}" for j in sorted(v["by_role"])) + f"  errors {v['errors']} cost ${v['cost_usd']}")
    print("   by kind:", {k: (x['n'], x['accuracy'], x['brier']) for k, x in v["by_kind"].items()})
