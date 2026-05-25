"""Client-side context window — mimics Ollama's num_ctx for API models that
don't truncate server-side. Keep the system preamble + the most-recent messages
that fit under the limit; drop the oldest. Lets a frontier API model run the
same constrained-window rot experiment as the local models.
"""

from context_clock.driver import cap_messages, _estimate_tokens


def _msg(role, content):
    return {"role": role, "content": content}


class TestCapMessages:
    def test_keeps_all_when_under_limit(self):
        msgs = [_msg("system", "preamble"), _msg("user", "a"), _msg("user", "b")]
        assert cap_messages(msgs, 10_000) == msgs

    def test_always_keeps_system_preamble(self):
        msgs = [_msg("system", "PREAMBLE"), _msg("user", "x" * 4000)]
        out = cap_messages(msgs, 100)
        assert out[0]["role"] == "system"
        assert out[0]["content"] == "PREAMBLE"

    def test_drops_oldest_when_over_limit(self):
        # each body is ~250 est tokens (1000 chars // 4); limit fits ~2 of them
        big = "x" * 1000
        msgs = [_msg("system", "p"), _msg("user", big + " one"),
                _msg("user", big + " two"), _msg("user", big + " three")]
        out = cap_messages(msgs, 600)
        bodies = [m["content"] for m in out if m["role"] == "user"]
        assert any("three" in b for b in bodies)   # most recent kept
        assert all("one" not in b for b in bodies)  # oldest dropped

    def test_keeps_at_least_the_most_recent_message(self):
        huge = "z" * 8000  # far exceeds the limit on its own
        msgs = [_msg("system", "p"), _msg("user", huge)]
        out = cap_messages(msgs, 50)
        assert out[-1]["content"] == huge  # never drop the only/most-recent turn

    def test_total_stays_within_budget_when_possible(self):
        msgs = [_msg("system", "p")] + [_msg("user", "w" * 400) for _ in range(20)]
        out = cap_messages(msgs, 500)
        total = sum(_estimate_tokens(m["content"]) for m in out)
        assert total <= 500 + _estimate_tokens("w" * 400)  # within one message of budget
