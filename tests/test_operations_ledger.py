import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from operations_ledger import export_ledgers


class OperationsLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.con = sqlite3.connect(":memory:")
        self.con.executescript("""
            CREATE TABLE execution_logs(
                id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL,
                operation TEXT NOT NULL, source_id TEXT, status TEXT NOT NULL
            );
            CREATE TABLE published_content(
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, occurred_at TEXT NOT NULL
            );
        """)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_exports_verified_publication_and_ist_day(self):
        self.con.execute(
            "INSERT INTO execution_logs VALUES(1,?,?,?,?)",
            ("2026-11-19T18:30:00+00:00", "directory_attempt", "launchpad:cero-1", "verified"),
        )
        self.con.execute(
            "INSERT INTO published_content VALUES(?,?,?)",
            ("directory-launchpad-cero-1",
             '{"connector":"launchpad","url":"https://launchpad.example/listing/1","product_id":"cero-1","status":"submitted"}',
             "2026-11-19T18:30:00+00:00"),
        )
        snapshot = export_ledgers(self.con, self.root, target=666)
        self.assertEqual(snapshot["verified_publications"], 1)
        self.assertEqual(snapshot["unique_verified_domains"], 1)
        with (self.root / "operations" / "PUBLICATION_LEDGER.csv").open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["ist_date"], "2026-11-20")
        self.assertEqual(rows[0]["verified"], "true")

    def test_repeated_export_does_not_duplicate_events(self):
        self.con.execute(
            "INSERT INTO execution_logs VALUES(1,?,?,?,?)",
            ("2026-11-19T00:00:00+00:00", "run", "", "completed"),
        )
        export_ledgers(self.con, self.root)
        export_ledgers(self.con, self.root)
        with (self.root / "operations" / "ACTIVITY_LEDGER.csv").open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
