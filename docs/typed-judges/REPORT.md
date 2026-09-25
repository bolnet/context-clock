# Typed decision models as code-review judges

*A benchmark of Jev and every open-source Jev-class alternative on a private, known-verdict review corpus. 2026-09-24.*

## Summary

We asked nine systems the same typed questions about the same code-review states, 150 cases across five reviewer roles, and scored every answer against a known verdict. Two things came out of it that matter more than the leaderboard.

1. **The first number we published was wrong, and the way it was wrong is instructive.** A typed judge that never saw the diff scored 30/30 because the file *paths* it was handed carried the corpus label (`negative-file-handle-leak/...`). Fixing the state (inline the files, drop the directory) turned that into 25/30. Any benchmark that hands a model paths, IDs or filenames must check what those strings leak.
2. **Scores can be earned without reading.** Our grader accepted a defect as "caught" when any lens question answered ≥ 0.5, path or no path. A constant answer scores 20/30 that way. The only rows that separate reading from guessing are the ten *clean* cases, and the per-question probabilities. Both are reported here; the headline score alone would have ranked the systems wrongly.

On the discriminating measure, probability of a defect scored against the known verdict, **Jev (TypeSafe) is the only system that reads the case on four of five roles** (accuracy 0.89 to 0.97, Brier 0.05 to 0.11). Bespoke Nimble 9B reads the test-review role (0.83) but sees only a fraction of the state. Laya, Von, Kev 0.8B, SemIf and Rizzo Flow put the same probability on clean and defective cases. Kev 4B answers confidently and is near chance on the diff-based roles.

## What was measured

A code-review pipeline has five reviewer roles that each answer a bounded question about a piece of state. For each role we hold a corpus of 30 cases with a verdict fixed before any system ran:

| role | state the judge sees | the question |
|---|---|---|
| test reviewer | the tests a writer produced, the task brief, the acceptance criteria | does each test prove a requirement, or does it merely run? |
| spec alignment | a diff, the spec, the accumulated change | does the change drift from or contradict the spec? |
| task alignment | a diff, the task brief, the builder's report | does the change do what the task said, and only that? |
| code review | a diff with context, the brief, the report | what defects does the diff contain? |
| plan review | a plan of tasks, the spec | per lens (edge cases, security, rollback, testability, ...): sign off, defer, ask, or block? |

Each corpus has three kinds of case, ten of each:

- **positive**: a clean case; the correct answer is "no defect". Any blocking or major finding is a false fail.
- **negative**: a planted defect at a named path; the judge must flag it.
- **pressure**: a planted defect plus an argument for it (an "excuse" in the brief or a comment); the judge must flag it anyway.

The cases were authored by hand for the review pipeline, not drawn from a public set, so no system could have trained on them. Case names carry the kind (`negative-assert-true`); that is the string that leaked in the first run, see below.

## How the questions were chosen

Jev-class systems take a *state* and *typed questions* (a yes/no "noul" with a probability, a "choice" over named options with a distribution, an ordered "score") and return no text. The reviewer prompts written for a text-generating judge therefore had to be turned into typed questions. The rule we used: **the judgement the schema asks for becomes the question; lenses become secondary questions.**

| role | questions per case |
|---|---|
| test reviewer | one **choice** per test id over the four labels the schema already used, `requirement / nothing / implementation_detail / wrong_requirement`, with the schema's own definition of each label as the option description; one **noul** per lens (test-tier isolation) |
| spec and task alignment | one **noul** "the change contains a defect a reviewer must block"; one **noul** per lens; one **choice** for the verdict (`pass / fix / block`); one **choice** for the kind of misalignment |
| code review | the defect noul, one noul per lens (public-interface contract, modular decomposition, test-tier isolation), the verdict choice |
| plan review | one **choice** per lens over the four plan verdicts (`SIGN_OFF / SIGN_OFF_WORK_DEFERRED / OPEN_QUESTION / BLOCK`) |

Two lessons from an earlier attempt shaped this. Asking only the lens questions, without the judge's own question, produced honest 0.5s and never asked for the judgement. And for the test reviewer, the per-test choice *is* the judgement; the lens is context.

In total: 449 unique (state, question-set) exchanges, 1,834 questions, 996 choices and 838 nouls.

## How the state was built

Text-generating judges get their inputs **by reference**: file paths, and tools to open them. A typed model has no tools, so the state must carry the content.

- Every referenced file is inlined under a neutral name (`inputs/review_package.diff`) and the directory is removed from the text. This is the fix for the leak: the original state was the prompt with absolute paths in it, and the path contained the case name.
- Files a test id names (the test file itself) are inlined too.
- For models with a small budget the inlined inputs go **before** the prompt text, so what a 512-token window drops is boilerplate, not evidence. Systems with a hard limit (Nimble refuses prompts over 2,048 tokens) get a character cap after that ordering; code tokenizes at about 2.3 characters per token, so the cap is 3,400 characters.
- A "minimal" variant, the test file and the brief only, was run for the test reviewer to see what each model does with nothing but the evidence.

Every exchange is recorded with the questions, the raw answers (per-option probabilities, not a shaped reply), the model, the endpoint and the SHA-256 of the state; the state text is stored once by hash. That is what made the head-to-head replay below possible: identical bytes to every system.

## How answers were graded

A reply is graded against the case's expectation:

- positive: no finding at or above the floor (major), and the outcome is pass where one is stated;
- negative and pressure: a finding on the planted path at or above the floor, or, for a judge that cannot name a path, a verdict of fail.

**The fix.** For the test-review role the grader originally accepted *any* major finding as a catch, including a lens noul at 0.5 with no path, and counted the same against clean cases. That route needs no reading of the test, and a constant answer scores 20/30 through it. The grader now reads the per-test labels and findings that name a path; a pathless lens answer counts neither way. All test-review results below are after this fix; the change moved Jev 30 → 29, Nimble 20 → 24, Kev 4B 26 → 17, Laya 24 → 20. The diff-based roles keep the original rule, which still admits the flag-everything route; read their clean-case column, not the score.

## The systems, and how each ran

All on a 16 GB Apple M2 Pro unless stated. "Space" is the author's Hugging Face demo on ZeroGPU, reached through its Gradio API with a PRO account for quota.

| system | what it is | how it ran here |
|---|---|---|
| Claude judge (reference) | the pipeline's text-generating reviewer, claude-opus-5, with tools | inputs by reference, as in production |
| Jev (TypeSafe) | hosted decision model, `/v1/systemone` | API, full inlined state |
| Laya (Convai Innovations) | ModernBERT-large, 421M, `pip install laya` | in-process; 512-token budget, evidence first |
| Von (wfzyx) | ModernBERT-large, 395M, `von-sdk` | in-process |
| Kev 0.8B (jaredpalmer/kev) | Qwen3.5-0.8B + pointer head | local server on MLX, `/v1/systemone` |
| Kev 4B | Qwen3.5-4B + pointer head | Space (local MLX also works, ~1 min a question) |
| Rizzo Flow (Rizzo AI Academy) | Spark-X2.5-1.7B q8 over llama.cpp/MLX | local server, `/v1/systemone` (the 4B took 120 s a question) |
| SemIf (TheoLeeCJ) | no decision model: option logits of a frozen base LLM | `semif-score --mode direct` over Qwen3.5-0.8B on MLX |
| Bespoke Nimble 9B | 9B, candidate-logit scoring | Space; hard 2,048-token prompt limit, state capped at 3,400 chars |
| NanoJev (TianyuCodings) | Qwen3-0.6B + decision heads | **not run**: the predictor raises without a CUDA device; no Space, no endpoint, not on OpenRouter or NVIDIA's catalog |

## Results

### Test reviewer, the role every system ran

| system | graded | clean cases passed | accuracy at 0.5 | Brier | P(defect) clean → planted → argued |
|---|---|---|---|---|---|
| Claude judge | 30/30 | 10/10 | — | — | — |
| Jev | 29/30 | 10/10 | 0.97 | 0.05 | 0.15 → 0.88 → 0.93 |
| Nimble 9B, 3,400 chars | 24/30 | 9/10 | 0.83 | 0.15 | 0.14 → 0.75 → 0.81 |
| Nimble 9B, test + brief only | 25/30 | 9/10 | 0.83 | 0.16 | 0.11 → 0.74 → 0.72 |
| Laya | 20/30 | 6/10 | 0.67 | 0.21 | 0.68 → 0.69 → 0.72 |
| Kev 0.8B | 20/30 | 0/10 | 0.67 | 0.25 | 0.84 → 0.86 → 0.88 |
| SemIf | 20/30 | 0/10 | 0.67 | 0.23 | 0.78 → 0.78 → 0.79 |
| Kev 4B, test + brief only | 18/30 | 9/10 | 0.63 | 0.34 | 0.15 → 0.35 → 0.51 |
| Kev 4B | 17/30 | 8/10 | 0.57 | 0.34 | 0.21 → 0.44 → 0.56 |
| Von | 10/30 | 10/10 | 0.67 | 0.23 | 0.59 → 0.58 → 0.58 |
| Rizzo Flow 1.7B | 10/30 | 10/10 | 0.33 | 0.45 | 0.19 → 0.19 → 0.19 |

Accuracy is of the per-test probability against the known kind; 0.667 is what a constant "defect" answer earns on a 20:10 split. Read the last column: a judge that reads the case has a low first number and high second and third.

### The other four roles

| role | Jev | Laya | Kev 4B | Nimble 9B |
|---|---|---|---|---|
| spec alignment | 30/30 · clean 10 · acc 0.80 | 20/30 · clean 0 · acc 0.67 | 10/30 · clean 10 · acc 0.33 | 25/30 · clean 5 · acc 0.37 |
| task alignment | 26/30 · clean 10 · acc 0.83 | 26/30 · clean 7 · acc 0.63 | 10/30 · clean 10 · acc 0.33 | 23/30 · clean 10 · acc 0.53 |
| code review | 26/30 · clean 6 · acc 0.90 | 28/30 · clean 8 · acc 0.67 | 21/30 · clean 1 · acc 0.33 | 21/30 · clean 1 · acc 0.60 |
| plan review | 8/30 · clean 2 · acc 0.33 | 2/30 · clean 2 · acc 0.33 | 10/30 · clean 10 · acc 0.33 | refused: prompt over 2,048 tokens |

Laya's 28/30 on code review against Jev's 26 is the flag-everything route the diff-role grader still allows: its P(defect) is 0.73 on clean diffs and 0.70 on defective ones. Kev 4B on the diff roles puts 0.03 to 0.15 on everything, clean or not, so it passes every clean case and misses nearly every defect. The plan-review corpus is one neither reads; its grader also wants a `BLOCK` on a named task id, which a per-lens judge cannot give, so that row measures the interface as much as the model.

### Head to head on identical questions: Jev and Laya

All 449 recorded exchanges were replayed through both in one sitting, 1,834 questions each, byte-identical state and questions.

| | Jev | Laya |
|---|---|---|
| cost for 1,834 questions | ≈ $0.07 (published rate on recorded input tokens) | $0, local |
| median latency per exchange | 0.35 s | 0.45 s |
| errors | 0 | 0 |
| agreement on choice labels | 32% | |
| agreement on yes/no side | 70% (mean probability gap 0.19) | |

| role / state | Jev acc / Brier / P(defect) clean → defective | Laya acc / Brier / P(defect) clean → defective |
|---|---|---|
| test reviewer, full | 0.93 / 0.07 / 0.18 → 0.88 | 0.67 / 0.22 / 0.71 → 0.73 |
| test reviewer, minimal | 0.77 / 0.12 / 0.16 → 0.79 | 0.67 / 0.26 / 0.85 → 0.84 |
| task alignment | 0.89 / 0.09 / 0.23 → 0.76 | 0.64 / 0.23 / 0.62 → 0.61 |
| code review | 0.89 / 0.11 / 0.37 → 0.75 | 0.67 / 0.24 / 0.77 → 0.75 |
| spec alignment | 0.72 / 0.18 / 0.70 → 0.91 | 0.67 / 0.23 / 0.70 → 0.68 |
| plan review | 0.33 / 0.36 / 0.14 → 0.28 | 0.33 / 0.42 / 0.22 → 0.23 |

## Cost and time, for calibration loops

The reason to want a typed judge at all: a text-generating judge costs about $0.57 and 45 seconds per case, so re-running a 30-case corpus after a prompt edit is $17 and 25 minutes, and recalibrating five roles is about $90 and two and a half hours. Jev answers a corpus for half a cent in twelve seconds; the local models for nothing in a similar time. That is what makes prompt iteration on judges affordable, independent of which typed model is chosen.

## Findings

1. **Check what the state leaks before believing a score.** Paths, IDs and filenames are text to a model. Our first 30/30 was the label.
2. **A score that a constant answer can earn is not a score.** Report the clean-case column and the per-question probabilities; the graded total alone ranked Kev 4B first among open models when its per-test judgement was at chance.
3. **Sub-1B encoders do not discriminate on review-sized states.** Laya, Von and Kev 0.8B give the same probability whatever the case. Shrinking the state to the test file and the brief did not help them.
4. **Nimble 9B reads the test but is starved.** Its 2,048-token limit removes the brief and criteria on every role but the minimal one; it is the only open model with a calibrated per-test judgement (Brier 0.15).
5. **Kev 4B is confident and wrong on diffs.** Decisive probabilities, near-zero P(defect) on planted defects in the alignment and code roles.
6. **Jev is the reference among typed models.** Accuracy 0.89 to 0.97 with Brier 0.05 to 0.11 on four roles, one shared miss with no other system across 150 cases, and the only model whose lens answers stay low on clean tests.
7. **Two judges are worth their cost only if their errors are independent.** Zero cases were missed by both Jev and the text-generating judge on any corpus; that is a hint, not proof, and the real measurement is the disagreement rate on live runs, which this benchmark does not contain.

## Limitations

Thirty cases per role, hand-authored, and run-to-run variance of about one case in thirty for the hosted model. The diff-role grader still admits the flag-everything route. The plan-review grader is task-id based and unfair to per-lens judges. Local latencies were measured with other models loaded and are upper bounds. Nimble and Kev 4B ran on a shared free-tier GPU and were state-capped or state-reordered; their full-state behaviour on a large GPU is untested. NanoJev was not run.

## Reproducing

The harness records, for every case, the questions, the raw answers, the cost, the time and the state hash, pinned to the digest of the judge definition; the state text is stored once by hash. A new system is a transport (a `post` function or a local server speaking the `/v1/systemone` contract) and a model name; it answers the same questions on the same bytes and is compared case by case and question by question against any earlier record. Regrading is a re-read of stored replies, not a re-run. The data files beside this report are the summaries those records produced.

- `data/scores-and-calibration.json`: per role and system, graded score, clean cases passed, accuracy, Brier, ECE, mean P(defect) by kind, median latency, cost.
- `data/replay-head-to-head.json`: the Jev-versus-Laya replay on identical questions.
