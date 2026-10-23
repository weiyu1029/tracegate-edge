#!/usr/bin/env python3
"""Fail a release when repository files appear to contain private or secret data.

The scanner is intentionally dependency-free. Custom deny and allow rules are
regular expressions, so a private organization can keep its own terms outside
the public repository. Findings are redacted before they are written to CI logs.

Content inspection is intentionally text-only: files larger than 2 MB, binary
payloads, and image/PDF/archive bodies are skipped. Sensitive filenames (for
example SQLite databases or private keys) are still rejected before content is
read. Treat this tool as one release gate, not as a replacement for Git history
review, secret scanning, OCR, metadata inspection, or manual review.
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Pattern


SKIP_DIRECTORIES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "bin",
    "dist",
    "htmlcov",
    "node_modules",
    "obj",
    "venv",
}

SKIP_SUFFIXES = {
    ".bmp",
    ".gif",
    ".heic",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".pyc",
    ".svgz",
    ".webp",
    ".zip",
}

SENSITIVE_SUFFIXES = {".db", ".key", ".p12", ".pfx", ".pem", ".sqlite", ".sqlite3"}
SQLITE_SIDECAR_SUFFIXES = tuple(
    f"{database_suffix}-{sidecar}"
    for database_suffix in (".db", ".sqlite", ".sqlite3")
    for sidecar in ("journal", "shm", "wal")
)
SAFE_ENV_NAMES = {".env.example", ".env.sample", ".env.template"}
MAX_TEXT_BYTES = 2_000_000

# Organization-specific terms belong in local CI configuration, not in a public
# repository. Supply base64-encoded UTF-8 terms at runtime with
# TRACEGATE_RELEASE_DENY_TERMS_B64 or --deny-terms-b64. Keeping the tuple empty
# also lets this scanner inspect its own source without embedding a private term.
BUILTIN_DENY_TERMS_B64: tuple[str, ...] = ()

SAFE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "0.0.0.0/32",
        "127.0.0.0/8",
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "255.255.255.255/32",
    )
)


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    pattern: Pattern[str]


@dataclass(frozen=True, slots=True)
class Finding:
    path: Path
    line: int
    rule: str
    matched: str
    source_line: str

    @property
    def subject(self) -> str:
        return f"{self.path}:{self.line}:{self.rule}:{self.matched}:{self.source_line}"


def decode_term(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, validate=True).decode("utf-8")


def built_in_rules() -> tuple[Rule, ...]:
    private_key_prefix = decode_term("LS0tLS1CRUdJTiA=")
    private_key_suffix = decode_term("UFJJVkFURSBLRVktLS0tLQ==")
    private_key_pattern = re.compile(
        re.escape(private_key_prefix)
        + r"(?:(?:RSA|EC|DSA|ENCRYPTED|OPENSSH) )?"
        + re.escape(private_key_suffix),
        re.IGNORECASE,
    )
    return (
        Rule("cloud-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
        Rule("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
        Rule("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
        Rule("slack-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b")),
        Rule("private-key", private_key_pattern),
        Rule(
            "credential-assignment",
            re.compile(
                r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|"
                r"client[_-]?secret)\b\s*[:=]\s*[\"']?([^\s\"'#;,]{6,})"
            ),
        ),
        Rule(
            "personal-email",
            re.compile(r"\b[A-Z0-9._%+-]+@(?!example\.(?:com|org|net)\b)[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
        ),
        Rule("absolute-user-home", re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/")),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", type=Path)
    parser.add_argument(
        "--deny-regex",
        action="append",
        default=[],
        help="Additional forbidden regular expression; may be repeated",
    )
    parser.add_argument(
        "--deny-regex-file",
        type=Path,
        help="File containing one forbidden regular expression per line",
    )
    parser.add_argument(
        "--deny-terms-b64",
        default=os.environ.get("TRACEGATE_RELEASE_DENY_TERMS_B64", ""),
        help="Comma-separated base64 UTF-8 literal terms",
    )
    parser.add_argument(
        "--allow-regex",
        action="append",
        default=[],
        help="Allow finding when this regex matches path/rule/content; may repeat",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        help="File containing one allow regular expression per line",
    )
    return parser.parse_args()


def load_regex_lines(path: Path | None) -> list[str]:
    if path is None:
        return []
    values: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            values.append(line)
    return values


def compile_regexes(values: Iterable[str], label: str) -> list[Pattern[str]]:
    compiled: list[Pattern[str]] = []
    for index, value in enumerate(values, start=1):
        try:
            compiled.append(re.compile(value, re.IGNORECASE))
        except re.error as exc:
            raise ValueError(f"invalid {label} regex #{index}: {exc}") from exc
    return compiled


def iter_files(root: Path) -> Iterator[Path]:
    for current, directory_names, file_names in os.walk(root):
        directory_names[:] = sorted(
            name for name in directory_names if name not in SKIP_DIRECTORIES
        )
        current_path = Path(current)
        for name in sorted(file_names):
            yield current_path / name


def is_sensitive_filename(path: Path) -> bool:
    if path.name.startswith(".env") and path.name not in SAFE_ENV_NAMES:
        return True
    lower_name = path.name.lower()
    return path.suffix.lower() in SENSITIVE_SUFFIXES or lower_name.endswith(
        SQLITE_SIDECAR_SUFFIXES
    )


def read_text(path: Path) -> str | None:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return None
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return None
        payload = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in payload:
        return None
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return None


def is_safe_network_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return True
    return any(address in network for network in SAFE_NETWORKS)


def scan_text(
    relative_path: Path,
    text: str,
    rules: Iterable[Rule],
    denied_terms: Iterable[str],
) -> Iterator[Finding]:
    ipv4_pattern = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
    terms = tuple((term, term.casefold()) for term in denied_terms if term)

    for line_number, source_line in enumerate(text.splitlines(), start=1):
        for rule in rules:
            for match in rule.pattern.finditer(source_line):
                yield Finding(relative_path, line_number, rule.name, match.group(0), source_line)

        folded = source_line.casefold()
        for original, normalized in terms:
            if normalized in folded:
                yield Finding(relative_path, line_number, "private-organization", original, source_line)

        for match in ipv4_pattern.finditer(source_line):
            value = match.group(0)
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                continue
            if not is_safe_network_address(value):
                yield Finding(relative_path, line_number, "network-address", str(address), source_line)


def placeholder_assignment(finding: Finding) -> bool:
    if finding.rule != "credential-assignment":
        return False
    normalized = finding.matched.casefold()
    placeholders = ("change-me", "changeme", "example", "not-set", "placeholder", "synthetic")
    return any(value in normalized for value in placeholders) or "${" in finding.matched


def allowed(finding: Finding, allow_rules: Iterable[Pattern[str]]) -> bool:
    if placeholder_assignment(finding):
        return True
    return any(rule.search(finding.subject) for rule in allow_rules)


def redact(value: str) -> str:
    if not value:
        return "[redacted]"
    if len(value) <= 6:
        return "[redacted]"
    return f"{value[:3]}…{value[-2:]}"


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    try:
        deny_regex_values = list(args.deny_regex) + load_regex_lines(args.deny_regex_file)
        allow_regex_values = list(args.allow_regex) + load_regex_lines(args.allowlist)
        custom_rules = [
            Rule(f"custom-regex-{index}", regex)
            for index, regex in enumerate(
                compile_regexes(deny_regex_values, "deny"), start=1
            )
        ]
        allow_rules = compile_regexes(allow_regex_values, "allow")
        encoded_terms = list(BUILTIN_DENY_TERMS_B64)
        encoded_terms.extend(
            value.strip() for value in args.deny_terms_b64.split(",") if value.strip()
        )
        denied_terms = [decode_term(value) for value in encoded_terms]
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    scanned_files = 0
    for path in iter_files(root):
        relative_path = path.relative_to(root)
        if is_sensitive_filename(path):
            finding = Finding(relative_path, 0, "sensitive-file", path.name, path.name)
            if not allowed(finding, allow_rules):
                findings.append(finding)
            continue

        text = read_text(path)
        if text is None:
            continue
        scanned_files += 1
        for finding in scan_text(
            relative_path,
            text,
            (*built_in_rules(), *custom_rules),
            denied_terms,
        ):
            if not allowed(finding, allow_rules):
                findings.append(finding)

    if findings:
        print(f"Public-release check failed with {len(findings)} finding(s):", file=sys.stderr)
        for finding in findings:
            location = str(finding.path)
            if finding.line:
                location += f":{finding.line}"
            print(
                f"- {location}: {finding.rule} ({redact(finding.matched)})",
                file=sys.stderr,
            )
        print("Use --allow-regex/--allowlist only after manually verifying a finding.", file=sys.stderr)
        return 1

    print(f"Public-release check passed ({scanned_files} text files scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
