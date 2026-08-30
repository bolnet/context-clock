"""Cost-vs-context analysis — turning a run's rows into the scaling result.

C30 says per-request cost is linear in context, and therefore cumulative spend
is quadratic in turns. Both are testable from a single run, because every
request is a datapoint: a warm request bills its whole prefix at the cache-read
rate, so the slope of cost against context should recover that rate.

That prediction is sharp. On Sonnet a warm session must slope at $0.20/Mtok —
the read rate — not $2.00/Mtok. A slope near the full input rate means the
cache is not doing its job, which is exactly the failure a frozen breakpoint
produces. The regression is therefore both the scaling result and a
self-check on the harness.

Stdlib only — least squares on a handful of points needs no dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from .agent import AgentRun
from .anthropic_provider import RequestRecord
from .pricing import price_card

_PER_MTOK = 1_000_000


@dataclass(frozen=True)
class Fit:
    """A least-squares line, with the goodness of fit that qualifies it."""

    slope: float
    intercept: float
    r_squared: float
    n: int

    def predict(self, x: float) -> float:
        return self.slope * x + self.intercept


def least_squares(xs: list[float], ys: list[float]) -> Fit:
    """Ordinary least squares. Raises rather than returning a meaningless line."""
    n = len(xs)
    if n < 2:
        raise ValueError(f"need at least 2 points to fit a line, got {n}")
    if len(ys) != n:
        raise ValueError("xs and ys must be the same length")

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("all x values are identical; slope is undefined")

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx
    intercept = mean_y - slope * mean_x

    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot else 1.0
    return Fit(slope=slope, intercept=intercept, r_squared=r_squared, n=n)


def warm_requests(records: list[RequestRecord]) -> list[RequestRecord]:
    """Requests served from cache — the steady state the linearity claim is about.

    Cold starts and misses rewrite the prefix at a different rate and would
    otherwise drag the slope toward the write rate.
    """
    return [r for r in records if r.cache_read > 0 and r.cost is not None]


def cost_per_context(run: AgentRun, model: str) -> Fit:
    """Fit **input-side** billed cost against context size across warm requests.

    Output cost is subtracted first. It has to be: output ranges over two
    orders of magnitude in an agentic session (a 45-token confirmation next to
    an 8,000-token file), and at 5x the input rate that variance swamps the
    input signal entirely — fitting raw cost against context on a real run
    returned r-squared 0.07, measuring nothing.

    The slope that remains is a measured price per token of context carried.
    If C30 holds it lands on the cache-read rate.
    """
    warm = warm_requests(run.records)
    if len(warm) < 2:
        raise ValueError("not enough warm requests with a billed cost to fit")
    output_rate = price_card(model).output_per_mtok / _PER_MTOK
    return least_squares(
        [float(r.context_tokens) for r in warm],
        [(r.cost or 0.0) - r.output_tokens * output_rate for r in warm],
    )


def implied_read_rate(run: AgentRun, model: str) -> float:
    """The regression slope expressed as USD per million tokens."""
    return cost_per_context(run, model).slope * _PER_MTOK


def read_rate_agreement(run: AgentRun, model: str) -> float:
    """How closely the measured slope matches the published cache-read rate.

    1.0 is exact agreement. Well below 1.0 means the session was not actually
    being served from cache at the rate it should have been.
    """
    published = price_card(model).cache_read_per_mtok
    measured = implied_read_rate(run, model)
    if published == 0:
        return 0.0
    return 1.0 - abs(measured - published) / published


def breakpoint_advances(run: AgentRun) -> bool:
    """Whether the cache breakpoint moved forward request over request.

    The direct test for the frozen-breakpoint bug, and it does not need a
    regression: a healthy session's cache read grows every request, because the
    prefix it reads back is everything sent last time. A frozen breakpoint
    leaves the read pinned within a turn, so the same value repeats.

    Inferring this from a weak regression instead was wrong — a real session
    has genuine scatter from variable write sizes, which is not a bug.
    """
    reads = [r.cache_read for r in run.records if r.cache_read > 0]
    if len(reads) < 3:
        return True  # too short to judge; do not cry wolf
    repeats = sum(1 for a, b in zip(reads, reads[1:]) if a == b)
    return repeats <= len(reads) // 4


def read_rate_from_billing(run: AgentRun, model: str) -> Fit:
    """Recover the cache-read rate from billed cost alone.

    The exact relation is
    ``cost = read*r_read + write*r_write + uncached*r_in + output*r_out``,
    so subtracting the three known terms and fitting the remainder against
    ``cache_read`` isolates the read rate. Measured on a real session: slope
    $0.200/Mtok at r-squared 1.000 on Sonnet.

    This is the honest form of the C30 check. Fitting against *context* instead
    leaves the write term in the residual, and since writes vary by two orders
    of magnitude at 12.5x the read rate, that scatter is real — not evidence of
    a broken harness.
    """
    warm = warm_requests(run.records)
    if len(warm) < 2:
        raise ValueError("not enough warm requests with a billed cost to fit")
    card = price_card(model)
    residual = [
        (r.cost or 0.0)
        - (r.output_tokens * card.output_per_mtok
           + r.cache_creation * card.cache_write_5m_per_mtok
           + r.input_tokens * card.input_per_mtok) / _PER_MTOK
        for r in warm
    ]
    return least_squares([float(r.cache_read) for r in warm], residual)


def model_reproduces_billing(run: AgentRun, model: str) -> float:
    """Largest USD discrepancy between the price card and the provider's bill.

    Zero means the four-bucket model accounts for every cent actually charged.
    """
    card = price_card(model)
    worst = 0.0
    for r in run.records:
        if r.cost is None:
            continue
        predicted = (
            r.cache_read * card.cache_read_per_mtok
            + r.cache_creation * card.cache_write_5m_per_mtok
            + r.input_tokens * card.input_per_mtok
            + r.output_tokens * card.output_per_mtok
        ) / _PER_MTOK
        worst = max(worst, abs(predicted - r.cost))
    return worst


def cumulative_by_turn(run: AgentRun) -> list[tuple[int, float]]:
    """Running billed cost at the end of each turn — the curve C30 predicts."""
    out: dict[int, float] = {}
    for record in run.records:
        out[record.turn] = record.cumulative_cost
    return sorted(out.items())


def quadratic_beats_linear(run: AgentRun) -> tuple[float, float] | None:
    """Compare a linear and a quadratic fit of cumulative cost against turn.

    Returns ``(linear_r2, quadratic_r2)``, or ``None`` when there are too few
    turns to distinguish them. Reported as a comparison rather than a verdict:
    with a handful of turns a quadratic will almost always fit better, so the
    numbers are shown and the reader judges.
    """
    points = cumulative_by_turn(run)
    if len(points) < 4:
        return None
    turns = [float(t) for t, _ in points]
    costs = [c for _, c in points]
    linear = least_squares(turns, costs)
    squared = least_squares([t * t for t in turns], costs)
    return linear.r_squared, squared.r_squared


def summarize_scaling(run: AgentRun, model: str) -> str:
    """The scaling result for one run, with its own self-check."""
    published = price_card(model).cache_read_per_mtok
    lines = ["  COST vs CONTEXT (C30: per-request cost is linear in context)"]
    try:
        fit = cost_per_context(run, model)
    except ValueError as exc:
        return "\n".join(lines + [f"    not enough warm requests to fit: {exc}"])

    measured = fit.slope * _PER_MTOK
    lines += [
        f"    warm requests fitted  {fit.n:>9}",
        f"    slope                 ${measured:>8.3f}/Mtok of context carried",
        f"    published read rate   ${published:>8.3f}/Mtok",
        f"    agreement             {read_rate_agreement(run, model):>9.1%}",
        f"    r-squared             {fit.r_squared:>9.3f}",
    ]
    if fit.r_squared < 0.8:
        lines.append(
            "    (scatter is expected: writes vary by orders of magnitude at 12.5x the "
            "read rate, so context alone does not determine cost)"
        )

    # The exact check, controlling for the write term.
    exact = read_rate_from_billing(run, model)
    lines += [
        "",
        "  READ RATE RECOVERED FROM BILLING (controlling for writes and output)",
        f"    slope                 ${exact.slope * _PER_MTOK:>8.3f}/Mtok",
        f"    published             ${published:>8.3f}/Mtok",
        f"    r-squared             {exact.r_squared:>9.3f}",
        "",
        "  SELF-CHECKS",
        f"    breakpoint advancing  {str(breakpoint_advances(run)):>9}",
        f"    price card vs bill    ${model_reproduces_billing(run, model):>8.6f} worst-case error",
    ]
    if not breakpoint_advances(run):
        lines.append(
            "    WARNING: the cache read is not advancing — the breakpoint is frozen and "
            "the growing tail is billing at full price. Fix the harness before reading "
            "anything else in this run."
        )

    curve = quadratic_beats_linear(run)
    if curve:
        linear_r2, quad_r2 = curve
        lines += [
            "",
            "  CUMULATIVE COST vs TURN",
            f"    linear fit r-squared      {linear_r2:>7.3f}",
            f"    quadratic fit r-squared   {quad_r2:>7.3f}",
        ]
    else:
        lines += ["", "  CUMULATIVE COST vs TURN: too few turns to distinguish."]
    return "\n".join(lines)
