#!/usr/bin/env python3
"""Offline end-to-end checks using random, nonfunctional synthetic credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gitleaks", type=Path, required=True)
    args = parser.parse_args()
    scanner = Path(__file__).with_name("scan_repository_secrets.py")
    with tempfile.TemporaryDirectory(prefix="secret-scan-selftest-") as directory:
        temporary = Path(directory)
        repo = temporary / "repo"
        repo.mkdir()
        reviewed = temporary / "reviewed.json"
        reviewed.write_text('{"entries": []}', encoding="utf-8")
        report = temporary / "sanitized.json"
        sentinels = []

        def git(*arguments):
            completed = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True)
            assert completed.returncode == 0, "Synthetic Git fixture setup failed"

        def scan(expected, expected_unreviewed):
            completed = subprocess.run([
                sys.executable, str(scanner), "--repo", str(repo), "--gitleaks", str(args.gitleaks),
                "--reviewed-fixtures", str(reviewed), "--report", str(report),
            ], capture_output=True)
            assert completed.returncode == expected, "Scanner returned unexpected status"
            payload = report.read_text(encoding="utf-8")
            combined = completed.stdout + completed.stderr + payload.encode("utf-8")
            assert all(value.encode() not in combined for value in sentinels), "Matched value disclosed"
            summary = json.loads(payload)
            assert summary["unreviewed_findings"] == expected_unreviewed
            return summary

        git("init", "--quiet")
        git("config", "user.name", "Synthetic scan fixture")
        git("config", "user.email", "fixture@example.invalid")
        (repo / "README.md").write_text("Synthetic clean repository\n", encoding="utf-8")
        git("add", "README.md")
        git("commit", "--quiet", "-m", "clean")
        scan(0, 0)

        source = repo / "candidate.py"
        sentinels.append(secrets.token_hex(32))
        original = f'api_key = "{sentinels[-1]}"'
        source.write_text(original + "\n", encoding="utf-8")
        git("add", "candidate.py")
        findings = scan(1, 1)
        assert findings["history_findings"] == 0
        assert findings["tracked_worktree_findings"] == 1

        reviewed.write_text(json.dumps({"entries": [{
            "file": "candidate.py", "rule": "generic-api-key",
            "line_sha256": hashlib.sha256(original.encode()).hexdigest(),
            "reason": "Only this exact synthetic self-test line",
        }]}), encoding="utf-8")
        exact = scan(0, 0)
        assert exact["reviewed_fixture_findings"] == 1
        sentinels.append(secrets.token_hex(32))
        source.write_text(f'api_key = "{sentinels[-1]}"\n', encoding="utf-8")
        scan(1, 1)  # Changing the value cannot hide behind an old reviewed fixture.

        git("add", "candidate.py")
        git("commit", "--quiet", "-m", "synthetic finding")
        source.unlink()
        git("add", "candidate.py")
        git("commit", "--quiet", "-m", "remove synthetic finding")
        history = scan(1, 1)
        assert history["history_findings"] == 1
        assert history["tracked_worktree_findings"] == 0
    print("PASS: clean scan; new finding; exact-fixture match; changed value; deleted historical finding; redaction")


if __name__ == "__main__":
    main()
