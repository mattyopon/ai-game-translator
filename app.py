#!/usr/bin/env python3
"""AI Game Translator — Web UI / ゲーム翻訳AIツール WebUI

Launch:
    python app.py          # Opens browser automatically
    python app.py --port 8080  # Custom port
"""

import argparse
import atexit
import json
import logging
import os
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

from flask import (
    Flask,
    after_this_request,
    jsonify,
    render_template,
    request,
    send_file,
)

# ---------------------------------------------------------------------------
# Resolve paths (works both as script and as frozen exe)
# ---------------------------------------------------------------------------

if getattr(sys, "frozen", False):
    # Running as PyInstaller bundle
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(BASE_DIR))

from glossary import Glossary
from translator import GameTranslator, _load_glossary

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB

CONFIG_PATH = str(BASE_DIR / "config.yaml")
GLOSSARY_PATH = str(BASE_DIR / "data" / "glossary.json")
UPLOAD_DIR = tempfile.mkdtemp(prefix="ai_translator_")

# Suppress verbose Flask/werkzeug logs
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Lazy singleton (defer init to first request so startup errors show in UI)
# ---------------------------------------------------------------------------

_translator: GameTranslator | None = None
_glossary: Glossary | None = None
_init_error: str | None = None


def _ensure_initialized():
    global _translator, _glossary, _init_error
    if _translator is not None:
        return
    if _init_error is not None:
        return
    try:
        _translator = GameTranslator(config_path=CONFIG_PATH)
        _glossary = Glossary(GLOSSARY_PATH)
    except Exception as e:
        _init_error = str(e)


def _get_translator() -> GameTranslator:
    _ensure_initialized()
    if _translator is None:
        raise RuntimeError(_init_error or "Translator not initialized")
    return _translator


def _get_glossary() -> Glossary:
    _ensure_initialized()
    if _glossary is None:
        raise RuntimeError(_init_error or "Glossary not initialized")
    return _glossary


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _cleanup():
    import shutil
    try:
        shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
    except Exception:
        pass

atexit.register(_cleanup)

# ---------------------------------------------------------------------------
# Routes — Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")

# ---------------------------------------------------------------------------
# Routes — API
# ---------------------------------------------------------------------------

@app.route("/api/config")
def api_config():
    try:
        t = _get_translator()
        return jsonify({
            "model": t._model,
            "backend": t._backend,
            "source_lang": t._source_lang,
            "target_lang": t._target_lang,
            "domain": t._domain,
            "auto_approve_threshold": t._auto_approve_threshold,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/translate", methods=["POST"])
def api_translate():
    try:
        t = _get_translator()
        data = request.get_json(force=True)
        source = data.get("source", "").strip()
        category = data.get("category", "")
        save_to_memory = data.get("save_to_memory", False)

        if not source:
            return jsonify({"error": "source text is required"}), 400

        result = t.translate(source=source, category=category)
        result.pop("similar_examples", None)

        # Optionally save to memory
        if save_to_memory and result["confidence"] < 1.0:
            t.memory.add(source=source, target=result["translation"], category=category)

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/translate/excel", methods=["POST"])
def api_translate_excel():
    try:
        from excel_io import read_source_excel, write_translation_excel

        t = _get_translator()

        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        if not file.filename or not file.filename.endswith(".xlsx"):
            return jsonify({"error": "Only .xlsx files are supported"}), 400

        category = request.form.get("category", "")
        save_to_memory = request.form.get("save_to_memory", "true") == "true"

        # Save uploaded file
        input_path = os.path.join(UPLOAD_DIR, f"input_{file.filename}")
        file.save(input_path)

        # Read source text
        items = read_source_excel(input_path, source_col=None)
        if not items:
            os.unlink(input_path)
            return jsonify({"error": "No translatable text found in file"}), 400

        # Add category if specified
        if category:
            for item in items:
                if not item.get("category"):
                    item["category"] = category

        # Translate batch
        results = t.translate_batch(items)

        # Save new translations to memory
        if save_to_memory:
            for r in results:
                if r.get("confidence", 0) < 1.0 and r.get("translation") and "error" not in r:
                    t.memory.add(
                        source=r["source"],
                        target=r["translation"],
                        category=r.get("category", ""),
                    )

        # Write output
        output_name = Path(file.filename).stem + "_translated.xlsx"
        output_path = os.path.join(UPLOAD_DIR, output_name)
        write_translation_excel(
            output_path,
            results,
            template_path=input_path,
            confidence_threshold=t._auto_approve_threshold,
        )

        # Clean up input file
        os.unlink(input_path)

        @after_this_request
        def cleanup(response):
            try:
                os.unlink(output_path)
            except Exception:
                pass
            return response

        return send_file(
            output_path,
            as_attachment=True,
            download_name=output_name,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/memory/approve", methods=["POST"])
def api_memory_approve():
    """Save an approved or corrected translation to memory."""
    try:
        t = _get_translator()
        data = request.get_json(force=True)
        source = data.get("source", "").strip()
        target = data.get("target", "").strip()
        category = data.get("category", "")

        if not source or not target:
            return jsonify({"error": "source and target are required"}), 400

        t.memory.add(source=source, target=target, category=category)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/glossary")
def api_glossary_list():
    try:
        g = _get_glossary()
        return jsonify({"entries": g.get_all()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/glossary", methods=["POST"])
def api_glossary_add():
    try:
        g = _get_glossary()
        t = _get_translator()
        data = request.get_json(force=True)
        source = data.get("source", "").strip()
        target = data.get("target", "").strip()
        notes = data.get("notes", "")

        if not source or not target:
            return jsonify({"error": "source and target are required"}), 400

        g.add(source, target, notes=notes)
        # Sync translator's glossary cache
        t._glossary = _load_glossary(GLOSSARY_PATH)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/glossary", methods=["DELETE"])
def api_glossary_remove():
    try:
        g = _get_glossary()
        t = _get_translator()
        data = request.get_json(force=True)
        source = data.get("source", "").strip()

        if not source:
            return jsonify({"error": "source is required"}), 400

        removed = g.remove(source)
        t._glossary = _load_glossary(GLOSSARY_PATH)
        return jsonify({"ok": True, "removed": removed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/glossary/export")
def api_glossary_export():
    try:
        g = _get_glossary()
        csv_path = os.path.join(UPLOAD_DIR, "glossary_export.csv")
        g.export_csv(csv_path)

        @after_this_request
        def cleanup(response):
            try:
                os.unlink(csv_path)
            except Exception:
                pass
            return response

        return send_file(csv_path, as_attachment=True, download_name="glossary.csv")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/glossary/import", methods=["POST"])
def api_glossary_import():
    try:
        g = _get_glossary()
        t = _get_translator()

        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        csv_path = os.path.join(UPLOAD_DIR, "glossary_import.csv")
        file.save(csv_path)

        count = g.import_csv(csv_path)
        os.unlink(csv_path)
        t._glossary = _load_glossary(GLOSSARY_PATH)
        return jsonify({"ok": True, "imported": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stats")
def api_stats():
    try:
        t = _get_translator()
        g = _get_glossary()
        stats = t.memory_stats()
        stats["glossary_count"] = len(g.get_all())
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AI Game Translator — Web UI")
    parser.add_argument("--port", type=int, default=5000, help="Server port (default: 5000)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    port = args.port
    url = f"http://localhost:{port}"

    print(f"AI Game Translator starting on {url}")
    print("Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
