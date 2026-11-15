"""Read explicit vocabulary lists and save compatible recovery CSVs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
import re
import tempfile


MAX_BYTES = 5 * 1024 * 1024
MAX_ISSUES = 12
MAX_TERM_LENGTH = 120
TERM = re.compile(r"[a-z]+(?:[-' ][a-z]+)*", re.ASCII)
LIST_MARKER = re.compile(r"^(?:[-*+•]\s+|\d+[.)、]\s*)")
WORD_COLUMNS = {"word", "lemma", "单词"}
PUNCTUATION = str.maketrans({"‘": "'", "’": "'", "‐": "-", "‑": "-"})


class InputError(ValueError):
    def __init__(self, code: str, *, issues: list[dict] | None = None, count: int = 1):
        super().__init__(code)
        self.code = code
        self.issues = issues or []
        self.count = count


@dataclass
class WordList:
    words: list[str]
    duplicates: int
    flagged: int

    @property
    def digest(self) -> str:
        return hashlib.sha256("\n".join(self.words).encode("ascii")).hexdigest()


def read_terms(path: Path) -> WordList:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InputError("input_too_large")
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    text = raw.decode(encoding)
    entries: dict[str, bool] = {}
    duplicates = 0
    issue_count = 0
    issues: list[dict] = []

    def issue(line: int, code: str) -> None:
        nonlocal issue_count
        issue_count += 1
        if len(issues) < MAX_ISSUES:
            issues.append({"line": line, "code": code})

    def add(raw_word: str, line: int, flagged: bool = False) -> None:
        nonlocal duplicates
        word = " ".join(raw_word.lower().translate(PUNCTUATION).split())
        if not TERM.fullmatch(word) or len(word) > MAX_TERM_LENGTH:
            issue(line, "invalid_term")
            return
        if word in entries:
            duplicates += 1
        entries[word] = entries.get(word, False) or flagged

    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        reader = csv.reader(io.StringIO(text, newline=""), delimiter="\t" if suffix == ".tsv" else ",", strict=True)
        try:
            header = [col.strip().lower() for col in next(reader, [])]
            indices = [i for i, name in enumerate(header) if name in WORD_COLUMNS]
            if len(indices) != 1 or header.count("flags") > 1:
                raise InputError("invalid_header", issues=[{"line": 1, "code": "invalid_header"}])
            word_col = indices[0]
            flags_col = header.index("flags") if "flags" in header and header[word_col] == "lemma" else None
            for row in reader:
                if not row or all(not field.strip() for field in row):
                    continue
                if len(row) != len(header):
                    issue(reader.line_num, "invalid_columns")
                    continue
                flagged = False
                if flags_col is not None:
                    flags = row[flags_col].strip()
                    if not re.fullmatch(r"[0-9]+", flags):
                        issue(reader.line_num, "invalid_flags")
                        continue
                    flagged = bool(flags.strip("0"))
                add(row[word_col], reader.line_num, flagged)
        except csv.Error:
            issue(max(1, reader.line_num), "invalid_csv")
    else:
        for line, value in enumerate(text.splitlines(), 1):
            value = value.strip()
            if value:
                add(LIST_MARKER.sub("", value, count=1), line)

    if issue_count:
        raise InputError("invalid_rows", issues=issues, count=issue_count)
    return WordList([word for word, flagged in entries.items() if not flagged], duplicates, sum(entries.values()))


def write_rows(stream, words: list[str]) -> None:
    writer = csv.writer(stream)
    writer.writerow(("lemma", "flags"))
    writer.writerows((word, 0) for word in words)


def write_csv(path: Path, words: list[str]) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        write_rows(stream, words)


def save_remaining(path: Path, words: list[str]) -> None:
    # Replace only a file already reserved by this invocation. On interruption
    # or I/O failure, the original full-batch recovery file remains usable.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", prefix=".bbdc-", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            write_rows(stream, words)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
