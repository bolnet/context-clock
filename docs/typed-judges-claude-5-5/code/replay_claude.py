"""Rerun of the 2026-09-24 typed-judges replay on a newer Claude model.

Reconstructed byte-for-byte (prompt, JSON schema, timeout + one retry, subscription auth) from the session that
produced `.forge/evals/replay/20260925T014301-claude.json` (claude-opus-5). Only the model and the system name
change, so the new answers sit on the identical 449 states (by SHA-256) and 1,834 questions.

Usage: replay_claude.py <model-id> <system-name> [--workers N]
Checkpoints every answer to `.forge/evals/replay/<system>.partial.jsonl`; rerunning resumes.
Final record: `.forge/evals/replay/<stamp>-<system>.json`, scored with `score_three.py jev claude <system>`.
`total_cost_usd` from `claude -p` is an API-equivalent price on the subscription, not a charge."""
import argparse, concurrent.futures as cf, glob, json, os, signal, subprocess, threading, time
from pathlib import Path

ROOT = Path(os.environ.get("FORGE_ROOT", ".")).resolve()
RP = ROOT / ".forge/evals/replay"
KEYS = ("judge", "case", "shape", "sha", "questions", "mode", "state_chars")
CALL_TIMEOUT_S = 300  # normal answers took 15 to 130 s on Opus 5
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answers"], "properties": {"answers": {"type": "object", "additionalProperties": {
    "type": "object", "additionalProperties": False, "properties": {"noul": {"type": "number", "minimum": 0, "maximum": 1},
    "choice": {"type": "string"}, "probabilities": {"type": "object", "additionalProperties": {"type": "number"}}}}}}}
# Subscription only: strip API credentials so `claude -p` authenticates with the claude.ai login.
SUB_ENV = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")}


def key(u):
    return (u["judge"], u["case"], u["shape"], u["sha"], u["mode"])


def prompt_for(u):
    return ("You are a calibrated decision model. Read the STATE, then answer every QUESTION with a probability. "
            "For a 'noul' question return {\"noul\": p} where p is the probability the statement is true. For a 'choice' question return "
            "{\"choice\": <option key>, \"probabilities\": {<each option key>: p}} with probabilities summing to 1. Use the question keys exactly. "
            "No prose.\n\nSTATE:\n" + (ROOT / ".forge/evals/states" / f"{u['sha']}.txt").read_text() + "\n\nQUESTIONS (JSON):\n" + json.dumps(u["questions"], indent=1))


def ask_once(u, model, system, attempt):
    t = time.time()
    try:
        proc = subprocess.Popen(["claude", "-p", "--output-format", "json", "--model", model, "--no-session-persistence", "--json-schema", json.dumps(SCHEMA),
                                 "--allowedTools", "Read"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=SUB_ENV,
                                start_new_session=True, cwd=ROOT)
        try:
            stdout, _ = proc.communicate(prompt_for(u), timeout=CALL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL); proc.wait()
            raise TimeoutError(f"no reply in {CALL_TIMEOUT_S}s, process group killed")
        env = json.loads(stdout)
        if env.get("is_error"):
            raise RuntimeError(str(env.get("result"))[:200])
        ans = env.get("structured_output") or json.loads(env.get("result", "{}"))
        return {**{k: u[k] for k in KEYS}, "system": system, "model": model, "answers": ans.get("answers"),
                "usage": {"cost_usd": env.get("total_cost_usd"), "billing": "subscription (API-equivalent price, not charged)"},
                "latency_s": round(time.time() - t, 2), "error": None, "attempt": attempt}
    except Exception as e:  # noqa: BLE001 - every failure is recorded on the row and retried on resume
        return {**{k: u[k] for k in KEYS}, "system": system, "model": model, "answers": None, "usage": {},
                "latency_s": round(time.time() - t, 2), "error": f"{type(e).__name__}: {str(e)[:200]}", "attempt": attempt}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model"); ap.add_argument("system"); ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    rows = json.load(open(sorted(glob.glob(str(RP / "*-jev.json")))[-1]))["rows"]
    part = RP / f"{a.system}.partial.jsonl"
    done = {}
    if part.exists():
        for line in part.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if not r["error"] and r["answers"]:
                    done[key(r)] = r  # failed rows are retried on resume
    lock = threading.Lock()

    def ask(u):
        if key(u) in done:
            return done[key(u)]
        row = None
        for attempt in (1, 2):
            row = ask_once(u, a.model, a.system, attempt)
            if not row["error"]:
                break
        with lock, part.open("a") as f:
            f.write(json.dumps(row) + "\n")
        return row

    out, t0 = [], time.time()
    print(f"[{a.system}] resuming with {len(done)} answered of {len(rows)}", flush=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(ask, rows)):
            out.append(r)
            if (i + 1) % 25 == 0:
                errs = sum(1 for x in out if x["error"])
                print(f"[{a.system}] {i+1}/{len(rows)} in {time.time()-t0:.0f}s, errors {errs}", flush=True)
    cost = sum(float((r["usage"] or {}).get("cost_usd") or 0) for r in out)
    p = RP / f"{time.strftime('%Y%m%dT%H%M%S')}-{a.system}.json"
    p.write_text(json.dumps({"system": a.system, "model": a.model, "n_exchanges": len(out), "n_questions": sum(len(r["questions"]) for r in out),
                             "errors": sum(1 for r in out if r["error"]), "cost_usd": cost, "rows": out}, indent=1))
    print(f"[{a.system}] wrote {p} errors {sum(1 for r in out if r['error'])} api-equivalent ${cost:.2f} {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
