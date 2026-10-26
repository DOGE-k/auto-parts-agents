#!/usr/bin/env python3
"""Scan all local Git refs and a tracked-file snapshot; never print secret values.

Requires Gitleaks 8.30.1. Ignored/untracked local configuration and private keys
are not read. A clean result is pattern-based, not proof that no secret exists;
image/OCR content, remote-only refs and untracked files are outside this scope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

VERSION = "8.30.1"


def run(command, *, cwd=None):
    return subprocess.run(command, cwd=cwd, capture_output=True, check=False)


def git(root, *args, strip=True):
    result = run(["git", "-C", str(root), *args])
    if result.returncode:
        raise RuntimeError("Git inspection failed (details withheld to avoid credential disclosure)")
    text = result.stdout.decode("utf-8", "surrogateescape")
    return text.strip() if strip else text


def scan(binary, root, mode, target, report, config, ignore_path):
    command = [str(binary), mode, str(target), "--no-banner", "--no-color", "--redact=100",
               "--ignore-gitleaks-allow", "--gitleaks-ignore-path", str(ignore_path),
               "--config", str(config), "--report-format", "json", "--report-path", str(report),
               "--max-archive-depth=2", "--max-decode-depth=2"]
    if mode == "git":
        command.extend(["--log-opts=--all --full-history --root --no-ext-diff"])
    result = run(command, cwd=root)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Gitleaks {mode} failed with exit {result.returncode}; raw output withheld")
    if not report.exists():
        raise RuntimeError(f"Gitleaks {mode} did not produce its expected report")
    records = json.loads(report.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise RuntimeError("Unexpected Gitleaks report shape")
    findings = []
    for record in records:
        file = str(record.get("File", ""))
        try:
            file = Path(file).relative_to(target).as_posix()
        except (ValueError, TypeError):
            file = file.replace("\\", "/")
        findings.append({
            "scope": "history" if mode == "git" else "tracked_worktree",
            "rule": record.get("RuleID"), "file": file,
            "line": record.get("StartLine"), "commit": record.get("Commit", ""),
            "fingerprint": f"{record.get('Commit', '')}:{file}:{record.get('RuleID')}:{record.get('StartLine')}",
        })
    return findings


def classify_findings(root, findings, reviewed_path):
    entries = json.loads(reviewed_path.read_text(encoding="utf-8"))["entries"]
    reviewed = {(item["file"], item["rule"], item["line_sha256"]): item["reason"] for item in entries}
    cache = {}
    for finding in findings:
        key = finding["commit"], finding["file"]
        if key not in cache:
            if finding["commit"]:
                # Never display a historic blob or any matched value.
                cache[key] = git(root, "show", f"{finding['commit']}:{finding['file']}", strip=False).splitlines()
            else:
                cache[key] = (root / finding["file"]).read_text(encoding="utf-8", errors="surrogateescape").splitlines()
        index = finding["line"] - 1
        if not 0 <= index < len(cache[key]):
            raise RuntimeError("Cannot verify reviewed finding against source line")
        digest = hashlib.sha256(cache[key][index].encode("utf-8", "surrogateescape")).hexdigest()
        reason = reviewed.get((finding["file"], finding["rule"], digest))
        finding["reviewed_fixture"] = reason is not None
        if reason:
            finding["review_reason"] = reason
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--gitleaks", type=Path, required=True)
    parser.add_argument("--report", type=Path, help="Optional sanitized JSON report, never raw findings")
    parser.add_argument("--reviewed-fixtures", type=Path, default=Path(__file__).with_name("secret_scan_reviewed_fixtures.json"))
    args = parser.parse_args()
    root = Path(git(args.repo.resolve(), "rev-parse", "--show-toplevel")).resolve()
    binary = args.gitleaks.resolve()
    version = run([str(binary), "version"])
    if version.returncode or version.stdout.decode().strip().lstrip("v") != VERSION:
        raise RuntimeError(f"Expected Gitleaks {VERSION}")
    if git(root, "rev-parse", "--is-shallow-repository") != "false":
        raise RuntimeError("Shallow checkout: fetch the full history before scanning")
    paths = git(root, "ls-files", "-z", strip=False).rstrip("\0").split("\0")
    paths = [path for path in paths if path]
    absent = []
    with tempfile.TemporaryDirectory(prefix="aic-secret-scan-") as temporary:
        private = Path(temporary)
        snapshot = private / "tracked"
        snapshot.mkdir()
        for name in paths:
            source = root / name
            if source.is_symlink():
                raise RuntimeError(f"Refusing to follow tracked symlink: {name}")
            if not source.is_file():
                absent.append(name)
                continue
            if not source.resolve().is_relative_to(root):
                raise RuntimeError("Tracked path escapes repository")
            destination = snapshot / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        config = private / "gitleaks.toml"
        config.write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
        ignore_path = private / "empty-ignore"
        ignore_path.write_text("", encoding="utf-8")
        history = scan(binary, root, "git", root, private / "history.json", config, ignore_path)
        current = scan(binary, root, "dir", snapshot, private / "current.json", config, ignore_path)
        findings = classify_findings(root, history + current, args.reviewed_fixtures)
        summary = {
            "scanner": f"gitleaks {VERSION}", "head": git(root, "rev-parse", "HEAD"),
            "commits_scanned": int(git(root, "rev-list", "--all", "--count")),
            "refs_scanned": len(git(root, "for-each-ref", "--format=%(refname)").splitlines()),
            "tracked_files": len(paths), "absent_tracked_files": absent,
            "history_findings": len(history), "tracked_worktree_findings": len(current),
            "reviewed_fixture_findings": sum(f["reviewed_fixture"] for f in findings),
            "unreviewed_findings": sum(not f["reviewed_fixture"] for f in findings),
            "findings": findings,
            "limitations": ["Pattern-based detection; not proof of absence",
                            "No OCR of images/screenshots", "Untracked/ignored files not read",
                            "Only locally available Git refs; fetch missing refs before running"],
        }
    encoded = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n", encoding="utf-8")
    display = {key: value for key, value in summary.items() if key != "findings"}
    display["findings"] = [item for item in summary["findings"] if not item["reviewed_fixture"]]
    print(json.dumps(display, ensure_ascii=False, indent=2))
    return 1 if summary["unreviewed_findings"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        # Error strings above contain only fixed text or file names, never raw scanner output.
        print(f"Secret scan could not complete: {error}", file=sys.stderr)
        raise SystemExit(2)
