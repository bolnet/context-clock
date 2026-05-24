"""Report writer — CSV is pure and tested; the PNG chart is integration."""

from context_clock.driver import TurnRow
from context_clock.report import write_csv, CSV_HEADER


def _rows():
    return [
        TurnRow(turn=1, context_tokens=164, cumulative_tokens=167, recall=None, compaction_event=False),
        TurnRow(turn=3, context_tokens=406, cumulative_tokens=1677, recall=1.0, compaction_event=False),
        TurnRow(turn=8, context_tokens=976, cumulative_tokens=7500, recall=None, compaction_event=True),
    ]


class TestWriteCsv:
    def test_header_matches(self, tmp_path):
        path = tmp_path / "out.csv"
        write_csv(_rows(), path)
        lines = path.read_text().strip().splitlines()
        assert lines[0] == ",".join(CSV_HEADER)

    def test_none_recall_is_blank_and_compaction_is_int(self, tmp_path):
        path = tmp_path / "out.csv"
        write_csv(_rows(), path)
        lines = path.read_text().strip().splitlines()
        assert lines[1] == "1,164,167,,0"          # recall None → blank, no compaction → 0
        assert lines[2] == "3,406,1677,1.0,0"       # recall present
        assert lines[3] == "8,976,7500,,1"          # compaction event → 1
