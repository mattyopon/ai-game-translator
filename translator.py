"""Main translation engine for game text localization.

Uses the Claude API with translation memory and glossary context to produce
high-quality, consistent game translations (Japanese -> English by default).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import yaml

try:
    from tqdm import tqdm

    _HAS_TQDM = True
except ImportError:
    _HAS_TQDM = False

import anthropic

from memory import TranslationMemory

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("game_translator")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _handler = logging.StreamHandler()  # stderr by default
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(_handler)


# ---------------------------------------------------------------------------
# Glossary loader
# ---------------------------------------------------------------------------


def _load_glossary(path: str) -> list[dict]:
    """Load glossary entries from a JSON file.

    Expected format – a JSON array of objects, each with at least:
        {"source": "...", "target": "...", "notes": "..."}
    """
    p = Path(path)
    if not p.exists():
        logger.warning("Glossary file not found: %s – starting with empty glossary", path)
        return []
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "entries" in data:
        data = data["entries"]
    if not isinstance(data, list):
        logger.warning("Glossary file has unexpected format – expected a list")
        return []
    return data


def _match_glossary(source: str, glossary: list[dict]) -> list[dict]:
    """Return glossary entries whose source term appears in *source* text."""
    matches: list[dict] = []
    for entry in glossary:
        term = entry.get("source", "")
        if term and term in source:
            matches.append(entry)
    return matches


# ---------------------------------------------------------------------------
# Style guide loader
# ---------------------------------------------------------------------------


def _load_style_guide(path: str) -> str:
    """Load style guide markdown content."""
    p = Path(path)
    if not p.exists():
        logger.warning("Style guide not found: %s – proceeding without style guide", path)
        return ""
    with open(p, "r", encoding="utf-8") as f:
        return f.read().strip()


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "api": {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "temperature": 0.3,
    },
    "translation": {
        "source_lang": "Japanese",
        "target_lang": "English",
        "domain": "game",
    },
    "memory": {
        "db_path": "data/translation_memory.json",
        "top_k": 5,
    },
    "glossary": {
        "path": "data/glossary.json",
    },
    "style_guide": {
        "path": "data/style_guide.md",
    },
    "review": {
        "auto_approve_threshold": 0.8,
    },
    "output": {
        "include_confidence": True,
        "include_similar_count": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def _compute_confidence(similar_examples: list[dict]) -> float:
    """Compute a confidence score based on the similar examples returned by
    translation memory.

    Rules:
        - 1.0  : exact match (similarity == 1.0)
        - 0.8–0.99 : very similar examples (top similarity > 0.7)
        - 0.5–0.79 : some similar examples (top similarity 0.3–0.7)
        - 0.0–0.49 : no/weak similar examples (cold start)
    """
    if not similar_examples:
        return 0.0

    top_sim = similar_examples[0].get("similarity", 0.0)

    # Exact match
    if top_sim >= 1.0:
        return 1.0

    # Very similar
    if top_sim > 0.7:
        # Scale linearly from 0.80 to 0.99 as top_sim goes from 0.7 to 1.0
        return round(0.80 + (top_sim - 0.7) / 0.3 * 0.19, 4)

    # Some similar
    if top_sim >= 0.3:
        # Scale linearly from 0.50 to 0.79 as top_sim goes from 0.3 to 0.7
        return round(0.50 + (top_sim - 0.3) / 0.4 * 0.29, 4)

    # Weak / no useful examples
    # Scale linearly from 0.0 to 0.49 as top_sim goes from 0.0 to 0.3
    return round(top_sim / 0.3 * 0.49, 4)


# ---------------------------------------------------------------------------
# Rate-limit aware API caller with exponential backoff
# ---------------------------------------------------------------------------

_MAX_RETRIES = 5
_BASE_DELAY = 1.0  # seconds
_MAX_DELAY = 60.0  # seconds


def _call_api_with_backoff(
    client: anthropic.Anthropic,
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    messages: list[dict],
    system: str,
) -> anthropic.types.Message:
    """Call the Anthropic messages API with exponential backoff on rate-limit
    and transient errors."""
    delay = _BASE_DELAY
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=messages,
            )
            return response
        except anthropic.RateLimitError as exc:
            last_exc = exc
            retry_after = getattr(exc, "retry_after", None)
            wait = float(retry_after) if retry_after else delay
            logger.warning(
                "Rate limited (attempt %d/%d). Retrying in %.1fs …",
                attempt,
                _MAX_RETRIES,
                wait,
            )
            time.sleep(wait)
            delay = min(delay * 2, _MAX_DELAY)
        except anthropic.APIStatusError as exc:
            # Retry on 5xx server errors
            if exc.status_code >= 500:
                last_exc = exc
                logger.warning(
                    "Server error %d (attempt %d/%d). Retrying in %.1fs …",
                    exc.status_code,
                    attempt,
                    _MAX_RETRIES,
                    delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, _MAX_DELAY)
            else:
                raise
        except anthropic.APIConnectionError as exc:
            last_exc = exc
            logger.warning(
                "Connection error (attempt %d/%d). Retrying in %.1fs …",
                attempt,
                _MAX_RETRIES,
                delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, _MAX_DELAY)

    # All retries exhausted
    raise RuntimeError(
        f"API call failed after {_MAX_RETRIES} retries"
    ) from last_exc


# ---------------------------------------------------------------------------
# GameTranslator
# ---------------------------------------------------------------------------


class GameTranslator:
    """Translates game text using the Claude API enriched with translation
    memory and glossary context."""

    def __init__(self, config_path: str = "config.yaml") -> None:
        self._config = self._load_config(config_path)

        api_cfg = self._config["api"]
        self._model: str = api_cfg["model"]
        self._max_tokens: int = api_cfg["max_tokens"]
        self._temperature: float = api_cfg["temperature"]

        trans_cfg = self._config["translation"]
        self._source_lang: str = trans_cfg["source_lang"]
        self._target_lang: str = trans_cfg["target_lang"]
        self._domain: str = trans_cfg["domain"]

        mem_cfg = self._config["memory"]
        self._top_k: int = mem_cfg["top_k"]
        self._memory = TranslationMemory(db_path=mem_cfg["db_path"])

        glos_cfg = self._config["glossary"]
        self._glossary: list[dict] = _load_glossary(glos_cfg["path"])

        style_cfg = self._config["style_guide"]
        self._style_guide: str = _load_style_guide(style_cfg["path"])

        self._auto_approve_threshold: float = self._config["review"]["auto_approve_threshold"]

        # Backend selection: "api" (anthropic SDK) or "cli" (claude CLI)
        self._backend: str = self._config.get("backend", "auto")
        api_key = os.environ.get("ANTHROPIC_API_KEY")

        if self._backend == "auto":
            if api_key:
                self._backend = "api"
            else:
                self._backend = "cli"

        if self._backend == "api":
            if not api_key:
                raise EnvironmentError(
                    "ANTHROPIC_API_KEY not set. Use backend: cli in config.yaml "
                    "to use Claude Code subscription instead."
                )
            self._client = anthropic.Anthropic(api_key=api_key)
        else:
            self._client = None
            # Verify claude CLI is available
            claude_bin = self._config.get("cli", {}).get("path", "claude")
            if not shutil.which(claude_bin):
                raise EnvironmentError(
                    f"Claude CLI not found at '{claude_bin}'. "
                    "Install Claude Code or set backend: api with ANTHROPIC_API_KEY."
                )
            self._claude_bin = claude_bin

        logger.info(
            "GameTranslator initialized — backend=%s, model=%s, memory=%d entries, glossary=%d terms",
            self._backend,
            self._model,
            self._memory.stats()["total_entries"],
            len(self._glossary),
        )

    # -- CLI backend --------------------------------------------------------

    def _call_cli(self, prompt: str) -> str:
        """Call Claude via the claude CLI (uses existing subscription)."""
        cmd = [self._claude_bin, "-p", prompt, "--model", "sonnet"]
        # Remove CLAUDECODE env var to allow nested execution
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
        env["HOME"] = os.environ.get("HOME", "")
        env["PATH"] = os.environ.get("PATH", "")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            if result.returncode != 0:
                logger.error("Claude CLI error: %s", result.stderr[:200])
                raise RuntimeError(f"Claude CLI failed: {result.stderr[:200]}")
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise RuntimeError("Claude CLI timed out after 120s")
        except FileNotFoundError:
            raise RuntimeError(
                f"Claude CLI not found at '{self._claude_bin}'. "
                "Install Claude Code or set backend: api."
            )

    # -- config -------------------------------------------------------------

    @staticmethod
    def _load_config(config_path: str) -> dict[str, Any]:
        """Load configuration from YAML, falling back to defaults."""
        cfg = dict(_DEFAULT_CONFIG)
        p = Path(config_path)
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                user_cfg = yaml.safe_load(f) or {}
            cfg = _deep_merge(cfg, user_cfg)
            logger.info("Loaded config from %s", config_path)
        else:
            logger.info(
                "Config file %s not found – using defaults", config_path
            )
        return cfg

    # -- prompt building ----------------------------------------------------

    def build_prompt(
        self,
        source: str,
        similar_examples: list[dict],
        glossary_matches: list[dict],
        category: str,
    ) -> str:
        """Build the full translation prompt with all available context.

        This is THE critical function for translation quality.  The prompt
        injects:
          1. Style guide (overall voice / tone rules)
          2. Glossary matches (mandatory term translations)
          3. Reference translations from translation memory (few-shot examples)
          4. Concrete instructions for the translation task
          5. The source text and its category
        """
        sections: list[str] = []

        # -- Header --
        sections.append(
            f"You are a professional game localization translator "
            f"({self._source_lang} → {self._target_lang})."
        )

        # -- Style guide --
        if self._style_guide:
            sections.append(f"## Style Guide\n{self._style_guide}")

        # -- Glossary --
        if glossary_matches:
            lines = ["## Glossary (MUST follow these exact translations)"]
            for entry in glossary_matches:
                src = entry.get("source", "")
                tgt = entry.get("target", "")
                notes = entry.get("notes", "")
                line = f"- {src} → {tgt}"
                if notes:
                    line += f"  ({notes})"
                lines.append(line)
            sections.append("\n".join(lines))

        # -- Reference translations --
        if similar_examples:
            lines = ["## Reference Translations (match this style and tone)"]
            for i, ex in enumerate(similar_examples, 1):
                sim = ex.get("similarity", 0.0)
                ex_cat = ex.get("category", "")
                lines.append(
                    f"{i}. [{ex_cat or 'general'}] (similarity: {sim:.2f})"
                )
                lines.append(f"   Source: {ex['source']}")
                lines.append(f"   Translation: {ex['target']}")
            sections.append("\n".join(lines))

        # -- Instructions --
        instructions = [
            "## Instructions",
            f"- Translate the {self._domain} text naturally for {self._target_lang}-speaking players",
            "- Match the tone and style of the reference translations above",
            "- Use glossary terms exactly as specified",
            "- For skill/effect descriptions, be concise and use game-standard phrasing",
            "- Preserve any numbers, percentages, and variable placeholders (like {0}, %s, {{name}})",
            "- Do not add information not present in the original",
            "- If unsure about nuance, prefer the style seen in reference translations",
            "- Output ONLY the translated text, with no explanations or notes",
        ]
        sections.append("\n".join(instructions))

        # -- Source text --
        translate_section = ["## Translate"]
        translate_section.append(f"Source: {source}")
        if category:
            translate_section.append(f"Category: {category}")
        translate_section.append("")
        translate_section.append("Translation:")
        sections.append("\n".join(translate_section))

        return "\n\n".join(sections)

    # -- single translation -------------------------------------------------

    def translate(
        self,
        source: str,
        category: str = "",
        context: str = "",
    ) -> dict[str, Any]:
        """Translate a single text with translation memory context.

        Parameters
        ----------
        source : str
            The source text to translate.
        category : str, optional
            Category of the text (e.g. ``"skill_effect"``, ``"item_desc"``,
            ``"ui"``, ``"dialogue"``).
        context : str, optional
            Additional context about where the text appears (currently
            reserved for future use).

        Returns
        -------
        dict
            Keys: ``source``, ``translation``, ``confidence``,
            ``similar_count``, ``similar_examples``, ``model``,
            ``tokens_used``.
        """
        logger.debug("Translating: %s (category=%s)", source[:60], category)

        # --- 1. Retrieve similar examples from translation memory ----------
        similar_examples = self._memory.search_similar(
            query=source,
            top_k=self._top_k,
            category=category,
        )

        # --- 2. Check for exact match (confidence == 1.0) ------------------
        if similar_examples and similar_examples[0].get("similarity", 0) >= 1.0:
            exact = similar_examples[0]
            logger.info("Exact TM match for: %s", source[:60])
            return {
                "source": source,
                "translation": exact["target"],
                "confidence": 1.0,
                "similar_count": len(similar_examples),
                "similar_examples": similar_examples,
                "model": self._model,
                "tokens_used": 0,
            }

        # --- 3. Match glossary terms ---------------------------------------
        glossary_matches = _match_glossary(source, self._glossary)

        # --- 4. Build prompt -----------------------------------------------
        prompt = self.build_prompt(
            source=source,
            similar_examples=similar_examples,
            glossary_matches=glossary_matches,
            category=category,
        )

        # --- 5. Call Claude (API or CLI) -----------------------------------
        system_msg = (
            "You are a professional game localization translator. "
            "Reply with ONLY the translated text. "
            "Do not include any explanations, notes, or extra text."
        )

        tokens_used = 0

        if self._backend == "api":
            response = _call_api_with_backoff(
                self._client,
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                system=system_msg,
                messages=[{"role": "user", "content": prompt}],
            )
            translation = ""
            for block in response.content:
                if block.type == "text":
                    translation = block.text.strip()
                    break
            if response.usage:
                tokens_used = response.usage.input_tokens + response.usage.output_tokens
        else:
            # CLI backend — use claude -p
            translation = self._call_cli(system_msg + "\n\n" + prompt)

        # --- 6. Compute confidence -----------------------------------------
        confidence = _compute_confidence(similar_examples)

        logger.info(
            "Translated (%s, conf=%.2f, tokens=%d): %s → %s",
            category or "general",
            confidence,
            tokens_used,
            source[:40],
            translation[:40],
        )

        return {
            "source": source,
            "translation": translation,
            "confidence": confidence,
            "similar_count": len(similar_examples),
            "similar_examples": similar_examples,
            "model": self._model,
            "tokens_used": tokens_used,
        }

    # -- batch translation --------------------------------------------------

    def translate_batch(
        self,
        items: list[dict],
        progress_callback: Callable[[int, int, dict], None] | None = None,
    ) -> list[dict]:
        """Translate multiple items.

        Parameters
        ----------
        items : list[dict]
            Each item must have ``"source"`` (str).  Optional keys:
            ``"category"`` (str), ``"context"`` (str).
        progress_callback : callable, optional
            Called after each item with ``(current_index, total, result)``.

        Returns
        -------
        list[dict]
            One result dict per input item (same structure as
            :meth:`translate`).
        """
        total = len(items)
        if total == 0:
            return []

        logger.info("Starting batch translation of %d items", total)
        results: list[dict] = []

        # Progress bar (tqdm if available, otherwise plain logging)
        iterator: Any
        if _HAS_TQDM and progress_callback is None:
            iterator = tqdm(enumerate(items), total=total, desc="Translating", unit="item")
        else:
            iterator = enumerate(items)

        for idx, item in iterator:
            source = item["source"]
            category = item.get("category", "")
            context = item.get("context", "")

            try:
                result = self.translate(
                    source=source, category=category, context=context
                )
            except Exception as exc:
                logger.error(
                    "Failed to translate item %d/%d (%s): %s",
                    idx + 1,
                    total,
                    source[:40],
                    exc,
                )
                result = {
                    "source": source,
                    "translation": "",
                    "confidence": 0.0,
                    "similar_count": 0,
                    "similar_examples": [],
                    "model": self._model,
                    "tokens_used": 0,
                    "error": str(exc),
                }

            results.append(result)

            if progress_callback is not None:
                progress_callback(idx + 1, total, result)
            elif not _HAS_TQDM:
                logger.info("Progress: %d/%d", idx + 1, total)

        # Summary logging
        total_tokens = sum(r.get("tokens_used", 0) for r in results)
        avg_confidence = (
            sum(r.get("confidence", 0.0) for r in results) / total
            if total
            else 0.0
        )
        errors = sum(1 for r in results if "error" in r)
        auto_approved = sum(
            1
            for r in results
            if r.get("confidence", 0.0) >= self._auto_approve_threshold
        )

        logger.info(
            "Batch complete — %d items, %d tokens, avg confidence %.2f, "
            "%d auto-approved, %d errors",
            total,
            total_tokens,
            avg_confidence,
            auto_approved,
            errors,
        )

        return results

    # -- utilities ----------------------------------------------------------

    @property
    def config(self) -> dict[str, Any]:
        """Return a copy of the current configuration."""
        return dict(self._config)

    @property
    def memory(self) -> TranslationMemory:
        """Access the underlying translation memory instance."""
        return self._memory

    @property
    def glossary(self) -> list[dict]:
        """Return the loaded glossary entries."""
        return list(self._glossary)

    def memory_stats(self) -> dict:
        """Return translation memory statistics."""
        return self._memory.stats()


# ---------------------------------------------------------------------------
# CLI entry point (for quick testing)
# ---------------------------------------------------------------------------

def main() -> None:
    """Minimal CLI for testing the translator."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Game text translator powered by Claude"
    )
    parser.add_argument("text", nargs="?", help="Source text to translate")
    parser.add_argument(
        "-c", "--category", default="", help="Text category (e.g. skill_effect, ui, dialogue)"
    )
    parser.add_argument(
        "--config", default="config.yaml", help="Path to config YAML"
    )
    parser.add_argument(
        "--batch", type=str, default=None,
        help="Path to JSON file with batch items (list of {source, category})"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    args = parser.parse_args()

    translator = GameTranslator(config_path=args.config)

    # --- Batch mode ---
    if args.batch:
        with open(args.batch, "r", encoding="utf-8") as f:
            items = json.load(f)
        results = translator.translate_batch(items)
        if args.json:
            # Strip similar_examples for compact output
            for r in results:
                r.pop("similar_examples", None)
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for r in results:
                conf = r["confidence"]
                status = "AUTO" if conf >= translator._auto_approve_threshold else "REVIEW"
                print(f"[{status} {conf:.2f}] {r['source']}")
                print(f"         → {r['translation']}")
                if "error" in r:
                    print(f"         !! ERROR: {r['error']}")
                print()
        sys.exit(0)

    # --- Single text mode ---
    if not args.text:
        parser.print_help()
        sys.exit(1)

    result = translator.translate(source=args.text, category=args.category)

    if args.json:
        result.pop("similar_examples", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        conf = result["confidence"]
        status = "AUTO" if conf >= translator._auto_approve_threshold else "REVIEW"
        print(f"[{status} conf={conf:.2f} similar={result['similar_count']} "
              f"tokens={result['tokens_used']}]")
        print(f"Source:      {result['source']}")
        print(f"Translation: {result['translation']}")


if __name__ == "__main__":
    main()
