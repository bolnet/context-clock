"""Report writer — CSV is pure and tested; the PNG chart is integration."""

from context_clock.driver import TurnRow
from context_clock.report import write_csv, CSV_HEADER


def _rows():
    return [
        TurnRow(turn=1, context_tokens=164, cumulative_tokens=167, recall=None,
                compaction_event=False, turn_tokens=167, prompt_tokens=132, completion_tokens=35),
        TurnRow(turn=3, context_tokens=406, cumulative_tokens=1677, recall=1.0,
                compaction_event=False, turn_tokens=600, prompt_tokens=560, completion_tokens=40),
        TurnRow(turn=8, context_tokens=976, cumulative_tokens=7500, recall=None,
                compaction_event=True, turn_tokens=2200, prompt_tokens=900, completion_tokens=20),
    ]


class TestWriteCsv:
    def test_header_matches(self, tmp_path):
        path = tmp_path / "out.csv"
        write_csv(_rows(), path)
        lines = path.read_text().strip().splitlines()
        assert lines[0] == ",".join(CSV_HEADER)

    def test_header_includes_per_turn_token_columns(self):
        assert "turn_tokens" in CSV_HEADER
        assert "prompt_tokens" in CSV_HEADER
        assert "completion_tokens" in CSV_HEADER

    def test_rows_serialize_token_columns(self, tmp_path):
        path = tmp_path / "out.csv"
        write_csv(_rows(), path)
        lines = path.read_text().strip().splitlines()
        # turn,context,cumulative,turn_tokens,prompt,completion,recall,compaction
        assert lines[1] == "1,164,167,167,132,35,,0"       # recall None → blank
        assert lines[2] == "3,406,1677,600,560,40,1.0,0"   # recall present
        assert lines[3] == "8,976,7500,2200,900,20,,1"     # compaction → 1
