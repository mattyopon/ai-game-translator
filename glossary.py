"""Glossary management for AI Game Translation Tool.

Manages a JSON-based glossary of game-specific terminology (JP→EN).
Supports add, remove, lookup, bulk import/export, and in-text matching.
"""

import csv
import json
from pathlib import Path


class Glossary:
    """Manages game-specific terminology mappings (source → target).

    Storage format (JSON):
        {
            "entries": [
                {"source": "攻撃力", "target": "ATK", "notes": "ステータス表示用の略称"},
                ...
            ]
        }
    """

    def __init__(self, path: str = "data/glossary.json") -> None:
        self._path = Path(path)
        self._entries: list[dict] = []
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load glossary from disk. Creates an empty glossary if the file doesn't exist."""
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._entries = data.get("entries", [])
            except (json.JSONDecodeError, KeyError):
                self._entries = []
        else:
            self._entries = []

    def _save(self) -> None:
        """Persist the glossary to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"entries": self._entries}, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def add(self, source: str, target: str, notes: str = "") -> None:
        """Add or update a glossary entry.

        If *source* already exists, its target and notes are updated in place.
        """
        source = source.strip()
        target = target.strip()

        for entry in self._entries:
            if entry["source"] == source:
                entry["target"] = target
                entry["notes"] = notes
                self._save()
                return

        self._entries.append({
            "source": source,
            "target": target,
            "notes": notes,
        })
        self._save()

    def remove(self, source: str) -> bool:
        """Remove a glossary entry by source term.

        Returns True if the entry was found and removed, False otherwise.
        """
        source = source.strip()
        original_len = len(self._entries)
        self._entries = [e for e in self._entries if e["source"] != source]

        if len(self._entries) < original_len:
            self._save()
            return True
        return False

    def get(self, source: str) -> str | None:
        """Look up the target translation for *source*.

        Returns the target string or None if not found.
        """
        source = source.strip()
        for entry in self._entries:
            if entry["source"] == source:
                return entry["target"]
        return None

    def get_all(self) -> list[dict]:
        """Return a copy of all glossary entries."""
        return list(self._entries)

    # ------------------------------------------------------------------
    # Text matching
    # ------------------------------------------------------------------

    def find_matches(self, text: str) -> list[dict]:
        """Find all glossary terms that appear in *text*.

        Uses longest-match-first ordering to avoid partial-match issues.
        Each returned dict contains: source, target, notes, start, end.
        """
        if not self._entries:
            return []

        # Sort by source length descending (longest match first)
        sorted_entries = sorted(self._entries, key=lambda e: len(e["source"]), reverse=True)

        matches: list[dict] = []
        matched_ranges: list[tuple[int, int]] = []

        for entry in sorted_entries:
            source = entry["source"]
            start = 0
            while True:
                idx = text.find(source, start)
                if idx == -1:
                    break

                end = idx + len(source)

                # Check overlap with already-matched ranges
                overlaps = False
                for m_start, m_end in matched_ranges:
                    if idx < m_end and end > m_start:
                        overlaps = True
                        break

                if not overlaps:
                    matches.append({
                        "source": entry["source"],
                        "target": entry["target"],
                        "notes": entry.get("notes", ""),
                        "start": idx,
                        "end": end,
                    })
                    matched_ranges.append((idx, end))

                start = idx + 1

        # Sort matches by position in text
        matches.sort(key=lambda m: m["start"])
        return matches

    # ------------------------------------------------------------------
    # Bulk import / export
    # ------------------------------------------------------------------

    def import_csv(self, path: str) -> int:
        """Import glossary entries from a CSV file.

        Expected CSV columns: source, target[, notes]
        Returns the number of entries imported.
        """
        csv_path = Path(path)
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")

        count = 0
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, None)

            # Detect whether first row is a header or data
            is_header = False
            if header:
                lower = [h.lower().strip() for h in header]
                if any(kw in lower for kw in ("source", "target", "原文", "訳文", "用語")):
                    is_header = True

            rows = list(reader)
            if header and not is_header:
                rows.insert(0, header)

            for row in rows:
                if len(row) < 2:
                    continue
                source = row[0].strip()
                target = row[1].strip()
                notes = row[2].strip() if len(row) > 2 else ""
                if source and target:
                    self.add(source, target, notes)
                    count += 1

        return count

    def export_csv(self, path: str) -> None:
        """Export all glossary entries to a CSV file."""
        csv_path = Path(path)
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["source", "target", "notes"])
            for entry in self._entries:
                writer.writerow([
                    entry["source"],
                    entry["target"],
                    entry.get("notes", ""),
                ])
