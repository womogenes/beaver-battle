"""Today's best Treasure Dash solo times, per board, kept in a small JSON file beside the game.

Only today's entries are kept: the board is wiped by the calendar, not by hand.
"""

import datetime
import json
from pathlib import Path


class Leaderboard:
    def __init__(self, path="leaderboard.json", today=None):
        self.path = Path(path)
        self.today = today or datetime.date.today().isoformat()
        self.times = {}
        try:
            saved = json.loads(self.path.read_text())
            if saved.get("date") == self.today and isinstance(saved.get("times"), dict):
                self.times = {board: [(str(name), float(seconds)) for name, seconds in rows] for board, rows in saved["times"].items()}
        except (OSError, ValueError, TypeError, AttributeError):
            self.times = {}

    def top(self, board, count=5):
        return sorted(self.times.get(board, []), key=lambda row: row[1])[:count]

    def record(self, board, name, seconds):
        """Add a finished run; returns its place today on that board, 1 being the best."""
        rows = self.times.setdefault(board, [])
        rows.append((name, round(float(seconds), 2)))
        rows.sort(key=lambda row: row[1])
        del rows[50:]
        try:
            self.path.write_text(json.dumps({"date": self.today, "times": self.times}, indent=1))
        except OSError:
            pass  # A read-only folder loses the board when the game closes, nothing worse.
        return 1 + sum(1 for other in rows if other[1] < round(float(seconds), 2))
