#!/usr/bin/env python3
"""Scan tracked text files for high-confidence committed secrets.

Findings report only a label, path, and line number. Secret values are never
printed. The scanner intentionally favors low false-positive patterns so it can
block every pull request.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable

MAX_TEXT_BYTES = 2_000_000
TEXT_SUFFIXES = {
    ".cfg", ".conf", ".css", ".env", ".example", ".html", ".ini", ".js",
    ".json", ".jsx", ".md", ".mjs", ".py", ".sh", ".toml", ".ts", ".tsx",
    ".txt", ".yaml", ".yml",
}
TEXT_NAMES = {"Dockerfile", "Procfile"}

MONGO_PLACEHOLDER_CREDENTIALS = (
    "user:password@",
    "staging_user:staging_password@",
    "user:pass@",
    "u:p@",
    "isolated-user:isolated-pass@",
)

PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github-token", re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]{36,255}|github_pat_[A-Za-z0-9_]{50,255})\b"
    )),
    ("stripe-live-secret", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    ("sendgrid-key", re.compile(
        r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"
    )),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("credentialed-mongo-uri", re.compile(
        r"mongodb(?:\+srv)?://[^/\s:@]+:[^@\s/]+@",
        re.IGNORECASE,
    )),
)


@dataclass(frozen=True)
class Finding:
    label: str
    path: str
    line: int


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [
        root / item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    ]


def is_text_candidate(path: Path) -> bool:
    return path.name in TEXT_NAMES or path.suffix.lower() in TEXT_SUFFIXES


def is_explicit_placeholder(label: str, matched: str) -> bool:
    if label != "credentialed-mongo-uri":
        return False
    lowered = matched.lower()
    return any(marker in lowered for marker in MONGO_PLACEHOLDER_CREDENTIALS)


def scan_files(root: Path, paths: Iterable[Path]) -> list[Finding]:
    findings: list[Finding] = []
    resolved_root = root.resolve()
    for path in paths:
        candidate = path if path.is_absolute() else root / path
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        try:
            resolved.relative_to(resolved_root)
        except ValueError:
            continue
        if not resolved.is_file() or not is_text_candidate(resolved):
            continue
        try:
            if resolved.stat().st_size > MAX_TEXT_BYTES:
                continue
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = resolved.relative_to(resolved_root).as_posix()
        for number, line in enumerate(content.splitlines(), 1):
            for label, pattern in PATTERNS:
                match = pattern.search(line)
                if match and not is_explicit_placeholder(label, match.group(0)):
                    findings.append(Finding(label, relative, number))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        findings = scan_files(root, tracked_files(root))
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"repository secret guard: FAIL ({type(exc).__name__})", file=sys.stderr)
        return 2
    if findings:
        for finding in findings:
            print(
                f"repository secret guard: FAIL "
                f"[{finding.label}] {finding.path}:{finding.line}",
                file=sys.stderr,
            )
        return 1
    print("repository secret guard: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
