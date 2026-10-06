"""Paired comparison on identical graded questions: wrong counts, discordant pairs, exact McNemar p."""
import glob, json, math, sys
import os
from pathlib import Path
RP = Path(os.environ.get("FORGE_ROOT", ".")) / ".forge/evals/replay"
src = Path(__file__).with_name("score_three.py").read_text(); ns = {}
exec("import json\ndef graded" + src.split("def graded")[1].split("def stats")[0], ns); graded = ns["graded"]
def load(s): return {(r["sha"], json.dumps(r["questions"], sort_keys=True)): r for r in json.load(open(sorted(glob.glob(str(RP / f"*-{s}.json")))[-1]))["rows"]}
def right(s, d, keys):
    out = {}
    for k in keys:
        for i, (kind, p, y) in enumerate(graded(d[k])): out[(k, kind, i)] = (p >= .5) == (y == 1)
    return out
def mcnemar(b, c):
    n = b + c
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n) if n else 1.0
pairs = [tuple(a.split(":")) for a in sys.argv[1:]]
names = sorted({x for p in pairs for x in p}); data = {s: load(s) for s in names}
keys = set.intersection(*(set(d) for d in data.values()))
R = {s: right(s, data[s], keys) for s in names}
q = set.intersection(*(set(r) for r in R.values()))
res = {s: {"n": len(q), "wrong": sum(not R[s][x] for x in q)} for s in names}
for a, b in pairs:
    ab = sum(R[a][x] and not R[b][x] for x in q); ba = sum(R[b][x] and not R[a][x] for x in q)
    both = sum(not R[a][x] and not R[b][x] for x in q)
    res[f"{a} vs {b}"] = {"a_right_b_wrong": ab, "b_right_a_wrong": ba, "both_wrong": both, "mcnemar_p": round(mcnemar(ab, ba), 4)}
print(json.dumps(res, indent=1)); Path(__file__).with_name("paired.json").write_text(json.dumps(res, indent=1))
