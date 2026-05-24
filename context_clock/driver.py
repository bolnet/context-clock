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
from .grader import grade
from .meter import TokenMeter
from .provider import OllamaProvider

# Filler grows each memo so the context fills in a handful of turns.
_FILLER = (
    "Routine shift note: all subsystems nominal, logs rotated, no anomalies "
    "observed, telemetry within bounds, and the duty roster unchanged. "
) * 4


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


def make_fact(n: int) -> Fact:
    answer = f"k{n:03d}"
    statement = f"Memo {n}: the vault code is {answer}. {_FILLER}"
    return Fact(index=n, statement=statement, answer=answer)


def due_probe(turn: int, cadence: int) -> bool:
    return turn > 0 and turn % cadence == 0


def probe_targets(num_injected: int, k: int) -> list[int]:
    return list(range(min(k, num_injected)))


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
) -> list[TurnRow]:
    """Run the arc and return one row per turn."""
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
        fact = make_fact(turn)
        facts.append(fact)
        transcript.append({"role": "user", "content": fact.statement})
        fact_costs.append(_estimate_tokens(fact.statement))

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
            )
        )

    return rows


def run_memory_session(
    provider: OllamaProvider,
    *,
    turns: int,
    cadence: int = 3,
    probe_k: int = 3,
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
        fact = make_fact(turn)
        facts.append(fact)
        memory.add(fact)  # ingest is a store, not an LLM call — ~free

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
            )
        )

    return rows
