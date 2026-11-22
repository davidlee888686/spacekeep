#!/usr/bin/env python3
"""Self-check for SpaceKeep dry-run and safety guards."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import spacekeep  # noqa: E402


class SpaceKeepDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="spacekeep_test_"))
        self.live = self.tmp / "live"
        self.backup = self.tmp / "backup_copies"
        self.protected = self.tmp / "MyProjects" / "important.txt"
        self.quarantine = self.tmp / "quarantine"
        self.live.mkdir()
        self.backup.mkdir()
        self.protected.parent.mkdir(parents=True)
        (self.live / "note.txt").write_text("same-content", encoding="utf-8")
        (self.backup / "note.txt").write_text("same-content", encoding="utf-8")
        (self.backup / "unique.txt").write_text("only-in-backup", encoding="utf-8")
        self.protected.write_text("same-content", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_finds_backup_duplicate_and_moves_nothing(self) -> None:
        plan_path = self.tmp / "plan.json"
        code = spacekeep.main(
            [
                "--system-root",
                str(self.tmp),
                "--min-free-gb",
                "0",
                "--keepers",
                str(self.live),
                "--roots",
                str(self.backup),
                "--json-out",
                str(plan_path),
            ]
        )
        self.assertEqual(code, 0)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["extra_count"], 1)
        self.assertTrue(str(self.backup / "note.txt") in plan["items"][0]["extras"])
        self.assertTrue((self.backup / "note.txt").exists())
        self.assertTrue((self.live / "note.txt").exists())

    def test_protected_folder_is_not_moved_even_on_apply(self) -> None:
        code = spacekeep.main(
            [
                "--system-root",
                str(self.tmp),
                "--min-free-gb",
                "0",
                "--keepers",
                str(self.live),
                "--roots",
                str(self.protected.parent),
                "--protected",
                str(self.protected.parent),
                "--apply",
                "--quarantine",
                str(self.quarantine),
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(self.protected.exists())
        self.assertFalse(any(self.quarantine.glob("important.txt")) if self.quarantine.exists() else True)

    def test_forbidden_drive_is_refused(self) -> None:
        code = spacekeep.main(
            [
                "--system-root",
                str(self.tmp),
                "--min-free-gb",
                "0",
                "--forbid-drive",
                "G",
                "--roots",
                r"G:\anything",
            ]
        )
        self.assertEqual(code, 2)

    def test_low_free_space_aborts(self) -> None:
        code = spacekeep.main(
            [
                "--system-root",
                str(self.tmp),
                "--min-free-gb",
                "1000000",
                "--roots",
                str(self.backup),
            ]
        )
        self.assertEqual(code, 2)

    def test_apply_moves_only_verified_backup_extra(self) -> None:
        code = spacekeep.main(
            [
                "--system-root",
                str(self.tmp),
                "--min-free-gb",
                "0",
                "--keepers",
                str(self.live),
                "--roots",
                str(self.backup),
                "--apply",
                "--quarantine",
                str(self.quarantine),
            ]
        )
        self.assertEqual(code, 0)
        self.assertFalse((self.backup / "note.txt").exists())
        self.assertTrue((self.live / "note.txt").exists())
        self.assertTrue((self.backup / "unique.txt").exists())
        self.assertTrue((self.quarantine / "note.txt").exists())
        manifest = (self.quarantine / "spacekeep_manifest.jsonl").read_text(encoding="utf-8")
        self.assertIn("digest", manifest)

    def test_protected_name_is_never_moved(self) -> None:
        dup = self.tmp / "backup_copies" / "MyProjects" / "keep.txt"
        dup.parent.mkdir(parents=True)
        dup.write_text("same-content", encoding="utf-8")
        code = spacekeep.main(
            [
                "--system-root", str(self.tmp), "--min-free-gb", "0",
                "--keepers", str(self.live), "--roots", str(self.backup),
                "--protect-name", "MyProjects",
                "--apply", "--quarantine", str(self.quarantine),
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(dup.exists())

    def test_restore_puts_files_back(self) -> None:
        code = spacekeep.main(
            [
                "--system-root", str(self.tmp), "--min-free-gb", "0",
                "--keepers", str(self.live), "--roots", str(self.backup),
                "--apply", "--quarantine", str(self.quarantine),
            ]
        )
        self.assertEqual(code, 0)
        self.assertFalse((self.backup / "note.txt").exists())
        code = spacekeep.main(
            ["--restore", str(self.quarantine / "spacekeep_manifest.jsonl"),
             "--system-root", str(self.tmp), "--min-free-gb", "0"]
        )
        self.assertEqual(code, 0)
        self.assertTrue((self.backup / "note.txt").exists())
        self.assertEqual((self.backup / "note.txt").read_text(encoding="utf-8"), "same-content")
        self.assertFalse((self.quarantine / "note.txt").exists())


if __name__ == "__main__":
    unittest.main()
