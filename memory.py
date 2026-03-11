"""Translation memory module for game translation.

Manages a database of approved source→target translation pairs used as
few-shot examples when translating new text.  Similarity search combines
exact matching, Jaccard token overlap, and character n-gram overlap so that
relevant examples surface quickly — no external dependencies required.
"""

from __future__ import annotations

import copy
import csv
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Rough check: a character is CJK (covers CJK Unified Ideographs,
# Hiragana, Katakana, CJK Symbols, half-width Katakana, etc.)
_CJK_RANGES = re.compile(
    r"[\u3000-\u303F"   # CJK Symbols and Punctuation
    r"\u3040-\u309F"    # Hiragana
    r"\u30A0-\u30FF"    # Katakana
    r"\u4E00-\u9FFF"    # CJK Unified Ideographs
    r"\uFF00-\uFFEF]"   # Half/Full-width Forms
)

_NON_ALNUM = re.compile(r"[^\w]", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Split *text* into tokens suitable for Jaccard comparison.

    For Japanese text each character becomes its own token; ASCII/Latin
    words are kept whole.  Punctuation and whitespace are discarded.
    """
    tokens: list[str] = []
    buf: list[str] = []

    for ch in text:
        if _CJK_RANGES.match(ch):
            # flush any accumulated Latin buffer as one token
            if buf:
                word = "".join(buf).lower()
                if word:
                    tokens.append(word)
                buf = []
            tokens.append(ch)
        elif ch.isspace() or _NON_ALNUM.match(ch):
            if buf:
                word = "".join(buf).lower()
                if word:
                    tokens.append(word)
                buf = []
        else:
            buf.append(ch)

    if buf:
        word = "".join(buf).lower()
        if word:
            tokens.append(word)

    return tokens


def _char_ngrams(text: str, n: int) -> set[str]:
    """Return a set of character n-grams for *text*."""
    # Strip whitespace so spacing differences don't dominate.
    t = text.replace(" ", "").replace("\u3000", "")
    if len(t) < n:
        return {t} if t else set()
    return {t[i : i + n] for i in range(len(t) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _similarity(source_a: str, source_b: str) -> float:
    """Compute a similarity score in [0, 1] between two source texts.

    The score blends Jaccard token overlap with bigram/trigram overlap.
    An exact match always returns 1.0.
    """
    if source_a == source_b:
        return 1.0

    tokens_a = set(_tokenize(source_a))
    tokens_b = set(_tokenize(source_b))
    jaccard_tok = _jaccard(tokens_a, tokens_b)

    bigrams_a = _char_ngrams(source_a, 2)
    bigrams_b = _char_ngrams(source_b, 2)
    jaccard_bi = _jaccard(bigrams_a, bigrams_b)

    trigrams_a = _char_ngrams(source_a, 3)
    trigrams_b = _char_ngrams(source_b, 3)
    jaccard_tri = _jaccard(trigrams_a, trigrams_b)

    # Weighted blend — token overlap is most meaningful, then bigrams,
    # then trigrams.
    return 0.45 * jaccard_tok + 0.35 * jaccard_bi + 0.20 * jaccard_tri


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class TranslationMemory:
    """Persistent store of approved source→target translation pairs."""

    _VERSION = 1

    def __init__(self, db_path: str = "data/translation_memory.json") -> None:
        self._path = Path(db_path)
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {"version": self._VERSION, "entries": []}
        self._load()

    # -- persistence --------------------------------------------------------

    def _ensure_dir(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> None:
        if self._path.exists():
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._data = {"version": self._VERSION, "entries": []}

    def _save(self) -> None:
        """Atomic write: write to a temp file then rename."""
        self._ensure_dir()
        tmp_path = self._path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        # os.replace is atomic on POSIX; best-effort on Windows.
        os.replace(tmp_path, self._path)

    def _backup(self, tag: str = "backup") -> None:
        """Create a timestamped backup of the current database file."""
        if not self._path.exists():
            return
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup_name = f"{self._path.stem}_{tag}_{ts}{self._path.suffix}"
        backup_path = self._path.parent / backup_name
        shutil.copy2(self._path, backup_path)

    # -- public API ---------------------------------------------------------

    def add(
        self,
        source: str,
        target: str,
        category: str = "",
        notes: str = "",
    ) -> None:
        """Add a single translation pair.

        If an entry with the same *source* already exists it is updated
        in-place (target, category, notes are overwritten and
        ``updated_at`` is refreshed).
        """
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            # Check for duplicate source
            for entry in self._data["entries"]:
                if entry["source"] == source:
                    entry["target"] = target
                    entry["category"] = category
                    entry["notes"] = notes
                    entry["updated_at"] = now
                    self._save()
                    return

            entry = {
                "id": str(uuid.uuid4()),
                "source": source,
                "target": target,
                "category": category,
                "notes": notes,
                "created_at": now,
                "updated_at": now,
                "use_count": 0,
            }
            self._data["entries"].append(entry)
            self._save()

    def add_batch(self, pairs: list[dict]) -> None:
        """Add multiple pairs at once.

        Each element of *pairs* must be a dict with at least ``source``
        and ``target`` keys; ``category`` and ``notes`` are optional.

        A backup of the database is created before the operation.
        """
        with self._lock:
            self._backup("pre_batch")
            now = datetime.now().isoformat(timespec="seconds")

            # Build a lookup of existing sources for fast duplicate check.
            existing: dict[str, int] = {
                e["source"]: idx
                for idx, e in enumerate(self._data["entries"])
            }

            for pair in pairs:
                source = pair["source"]
                target = pair["target"]
                category = pair.get("category", "")
                notes = pair.get("notes", "")

                if source in existing:
                    idx = existing[source]
                    entry = self._data["entries"][idx]
                    entry["target"] = target
                    entry["category"] = category
                    entry["notes"] = notes
                    entry["updated_at"] = now
                else:
                    new_entry = {
                        "id": str(uuid.uuid4()),
                        "source": source,
                        "target": target,
                        "category": category,
                        "notes": notes,
                        "created_at": now,
                        "updated_at": now,
                        "use_count": 0,
                    }
                    self._data["entries"].append(new_entry)
                    existing[source] = len(self._data["entries"]) - 1

            self._save()

    def search_similar(
        self,
        query: str,
        top_k: int = 5,
        category: str = "",
    ) -> list[dict]:
        """Return up to *top_k* entries most similar to *query*.

        Each returned dict is a copy of the stored entry with an extra
        ``similarity`` key (float in [0, 1]).

        If *category* is given, entries in that category receive a +0.1
        similarity boost (clamped to 1.0).

        Entries are sorted by descending similarity.  A side-effect is
        that ``use_count`` is incremented for every returned entry.
        """
        with self._lock:
            scored: list[tuple[float, int]] = []
            for idx, entry in enumerate(self._data["entries"]):
                sim = _similarity(query, entry["source"])
                if category and entry.get("category") == category:
                    sim = min(sim + 0.1, 1.0)
                scored.append((sim, idx))

            scored.sort(key=lambda t: t[0], reverse=True)
            top = scored[:top_k]

            results: list[dict] = []
            for sim, idx in top:
                entry = self._data["entries"][idx]
                entry["use_count"] += 1
                result = copy.deepcopy(entry)
                result["similarity"] = round(sim, 4)
                results.append(result)

            self._save()
            return results

    def get_all(self) -> list[dict]:
        """Return a deep copy of every entry."""
        with self._lock:
            return copy.deepcopy(self._data["entries"])

    def get_by_category(self, category: str) -> list[dict]:
        """Return entries whose category matches *category*."""
        with self._lock:
            return copy.deepcopy(
                [e for e in self._data["entries"] if e.get("category") == category]
            )

    def stats(self) -> dict:
        """Return summary statistics about the memory."""
        with self._lock:
            entries = self._data["entries"]
            categories: dict[str, int] = {}
            total_use = 0
            for e in entries:
                cat = e.get("category") or "(none)"
                categories[cat] = categories.get(cat, 0) + 1
                total_use += e.get("use_count", 0)
            return {
                "total_entries": len(entries),
                "categories": categories,
                "total_use_count": total_use,
                "version": self._data.get("version", self._VERSION),
            }

    def export_csv(self, path: str) -> None:
        """Export all entries to a CSV file at *path*."""
        with self._lock:
            fieldnames = [
                "id",
                "source",
                "target",
                "category",
                "notes",
                "created_at",
                "updated_at",
                "use_count",
            ]
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for entry in self._data["entries"]:
                    writer.writerow(entry)

    def delete(self, source: str) -> bool:
        """Delete the entry whose source text equals *source*.

        Returns ``True`` if an entry was removed, ``False`` otherwise.
        """
        with self._lock:
            for idx, entry in enumerate(self._data["entries"]):
                if entry["source"] == source:
                    self._data["entries"].pop(idx)
                    self._save()
                    return True
            return False
