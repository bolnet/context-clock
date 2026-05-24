"""Session driver — runs the long-session failure arc and records it.

Each turn injects a uniquely-answerable fact and sends the growing transcript
to the model. Every ``cadence`` turns it probes the oldest facts to measure
recall. When the live context fills past ``threshold`` of the limit, it
self-compacts the oldest facts (a lossy LLM summary) and charges the cost.

Pure helpers (``make_fact`` / ``due_probe`` / ``probe_targets``) are unit
tested; ``run_session`` is exercised by a real run.
"""

from __future__ import annotations

from dataclasses import dataclass

from .compaction import select_turns_to_compact, should_compact
from .compactor import compact
from .documents import make_document
from .grader import grade
from .meter import TokenMeter
from .provider import OllamaProvider

# Each memo is a varied NIAH haystack (see ``documents``) rather than one
# repeated sentence. ``pad_repeat`` is the tokens/turn dial: it scales the
# haystack so a native (32K-128K) window fills in a reasonable number of turns.
_WORDS_PER_REPEAT = 16  # ~words in the old filler sentence; keeps tokens/turn comparable


@dataclass(frozen=True)
class Fact:
    index: int
    statement: str
    answer: str


@dataclass(frozen=True)
class TurnRow:
    turn: int
    context_tokens: int
    cumulative_tokens: int
    recall: float | None
    compaction_event: bool
    turn_tokens: int = 0       # tokens spent this turn (prompt + completion + any compaction)
    prompt_tokens: int = 0     # prompt tokens evaluated this turn (sum across calls)
    completion_tokens: int = 0  # completion tokens generated this turn


def make_fact(n: int, pad_repeat: int = 4) -> Fact:
    # Inject a varied haystack with the needle planted inside, but keep the
    # "Memo N:" label the probes ask about.
    doc = make_document(n, words=pad_repeat * _WORDS_PER_REPEAT)
    return Fact(index=n, statement=f"Memo {n}: {doc.statement}", answer=doc.answer)


def due_probe(turn: int, cadence: int) -> bool:
    return turn > 0 and turn % cadence == 0


def probe_targets(num_injected: int, k: int) -> list[int]:
    return list(range(min(k, num_injected)))


def is_fully_rotted(
    recalls: list[float | None], streak: int = 3, floor: float = 0.0
) -> bool:
    """True when the last ``streak`` *recorded* recalls are all at/below ``floor``.

    ``None`` entries (non-probe turns) are ignored — they neither count toward
    the streak nor reset it. Used to end a no-compaction stress run once recall
    accuracy has bottomed out and stayed there (the model is "fully rotted").
    """
    recorded = [r for r in recalls if r is not None]
    if len(recorded) < streak:
        return False
    return all(r <= floor for r in recorded[-streak:])


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def run_session(
    provider: OllamaProvider,
    *,
    turns: int,
    limit: int,
    cadence: int = 3,
    threshold: float = 0.85,
    probe_k: int = 3,
    keep_floor: float = 0.5,
    compaction_enabled: bool = True,
    pad_repeat: int = 4,
    stop_when_rotted: bool = False,
    rot_streak: int = 3,
) -> list[TurnRow]:
    """Run the arc and return one row per turn.

    When ``stop_when_rotted`` is set, ``turns`` is a safety cap: the run ends
    early once recall accuracy has stayed at the floor for ``rot_streak``
    consecutive probes (full context rot). Pair with ``compaction_enabled=False``
    and ``cadence=1`` for the pure rot stress test.
    """
    meter = TokenMeter()
    facts: list[Fact] = []
    # transcript[0] is the preamble; the rest are fact / summary messages.
    transcript: list[dict] = [
        {
            "role": "system",
            "content": "You are a memo keeper. Remember every memo's vault code. Answer tersely.",
        }
    ]
    fact_costs: list[int] = []  # estimated tokens per fact message (parallel to transcript[1:])
    rows: list[TurnRow] = []

    for turn in range(1, turns + 1):
        fact = make_fact(turn, pad_repeat)
        facts.append(fact)
        transcript.append({"role": "user", "content": fact.statement})
        fact_costs.append(_estimate_tokens(fact.statement))

        prev_total = meter.cumulative_total
        prev_prompt = meter.cumulative_prompt
        prev_completion = meter.cumulative_completion

        recall: float | None = None
        if due_probe(turn, cadence):
            targets = probe_targets(len(facts), probe_k)
            correct = 0
            for idx in targets:
                question = f"What is the vault code in Memo {facts[idx].index}? Reply with only the code."
                response = provider.complete(
                    transcript + [{"role": "user", "content": question}], max_tokens=16
                )
                meter.record(response.prompt_tokens, response.completion_tokens)
                if grade(facts[idx].answer, response.text):
                    correct += 1
            recall = correct / len(targets) if targets else None
        else:
            response = provider.complete(
                transcript + [{"role": "user", "content": "Acknowledge in one word."}],
                max_tokens=8,
            )
            meter.record(response.prompt_tokens, response.completion_tokens)

        compaction_event = False
        if compaction_enabled and should_compact(meter.current_context, limit, threshold):
            target_reclaim = meter.current_context - int(keep_floor * limit)
            fold_idx = select_turns_to_compact(fact_costs, target_reclaim)
            if fold_idx:
                folded = [transcript[1 + i] for i in fold_idx]
                summary, cost = compact(provider, folded)
                meter.cumulative_total += cost
                # rebuild transcript: preamble + summary + un-folded fact messages
                survivors = [transcript[1 + i] for i in range(len(fact_costs)) if i not in set(fold_idx)]
                survivor_costs = [fact_costs[i] for i in range(len(fact_costs)) if i not in set(fold_idx)]
                transcript = [transcript[0], summary] + survivors
                fact_costs = [_estimate_tokens(summary["content"])] + survivor_costs
                compaction_event = True

        rows.append(
            TurnRow(
                turn=turn,
                context_tokens=meter.current_context,
                cumulative_tokens=meter.cumulative_total,
                recall=recall,
                compaction_event=compaction_event,
                turn_tokens=meter.cumulative_total - prev_total,
                prompt_tokens=meter.cumulative_prompt - prev_prompt,
                completion_tokens=meter.cumulative_completion - prev_completion,
            )
        )

        if stop_when_rotted and is_fully_rotted([r.recall for r in rows], streak=rot_streak):
            break

    return rows


def run_memory_session(
    provider: OllamaProvider,
    *,
    turns: int,
    cadence: int = 3,
    probe_k: int = 3,
    pad_repeat: int = 4,
) -> list[TurnRow]:
    """Same workload, but retrieve only the relevant fact per probe.

    Facts are stored, not stacked into the prompt, so the live context stays
    flat: probes send just ``[preamble, retrieved fact, question]``. No window
    pressure → no compaction → no decay. This is the payoff contrast.
    """
    from .memory import RetrievalMemory

    memory = RetrievalMemory()
    meter = TokenMeter()
    facts: list[Fact] = []
    preamble = {"role": "system", "content": "You are a memo keeper. Answer tersely."}
    rows: list[TurnRow] = []

    for turn in range(1, turns + 1):
        fact = make_fact(turn, pad_repeat)
        facts.append(fact)
        memory.add(fact)  # ingest is a store, not an LLM call — ~free

        prev_total = meter.cumulative_total
        prev_prompt = meter.cumulative_prompt
        prev_completion = meter.cumulative_completion

        recall: float | None = None
        if due_probe(turn, cadence):
            targets = probe_targets(len(facts), probe_k)
            correct = 0
            for idx in targets:
                hit = memory.recall(facts[idx].index)
                question = f"What is the vault code in Memo {facts[idx].index}? Reply with only the code."
                context = [preamble, {"role": "system", "content": hit.statement}]
                response = provider.complete(context + [{"role": "user", "content": question}], max_tokens=16)
                meter.record(response.prompt_tokens, response.completion_tokens)
                if grade(facts[idx].answer, response.text):
                    correct += 1
            recall = correct / len(targets) if targets else None

        rows.append(
            TurnRow(
                turn=turn,
                context_tokens=meter.current_context,
                cumulative_tokens=meter.cumulative_total,
                recall=recall,
                compaction_event=False,
                turn_tokens=meter.cumulative_total - prev_total,
                prompt_tokens=meter.cumulative_prompt - prev_prompt,
                completion_tokens=meter.cumulative_completion - prev_completion,
            )
        )

    return rows
