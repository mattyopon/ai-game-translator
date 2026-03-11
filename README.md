# AI Game Translator

A CLI tool for Japanese-to-English game text translation powered by Claude AI.
Learns from human corrections to produce increasingly accurate translations with consistent terminology and tone.

## Features

- **Translation Memory**: Stores approved translations and uses them as few-shot examples for new text
- **Glossary Management**: Enforces consistent game-specific terminology (skills, items, UI elements)
- **Confidence Scoring**: Rates translation quality based on similarity to past approved translations
- **Excel I/O**: Reads source text from Excel, outputs translations with confidence scores
- **Continuous Learning**: Imports human-corrected translations to improve future output
- **Dual Backend**: Works with Anthropic API key or Claude Code subscription (no API key needed)
- **Interactive Mode**: Translate text one-by-one with approve/correct/skip workflow

## Quick Start

```bash
pip install -r requirements.txt

# Translate an Excel file (auto-detects Japanese text column)
python cli.py translate input.xlsx -o output.xlsx

# Import human-corrected translations to improve future quality
python cli.py learn corrected.xlsx --source-col A --translation-col B --corrected-col C

# Manage game glossary
python cli.py glossary add "必殺技" "Special Move"
python cli.py glossary list

# Interactive translation with approve/correct workflow
python cli.py interactive

# View translation memory statistics
python cli.py stats
```

## Backend Configuration

Edit `config.yaml`:

```yaml
# "auto" = use API key if available, otherwise use Claude Code CLI
# "api"  = Anthropic API (requires ANTHROPIC_API_KEY)
# "cli"  = Claude Code CLI (uses existing subscription, no API key needed)
backend: "auto"
```

## Project Structure

```
ai-translator/
├── cli.py           # CLI entry point (translate/learn/glossary/stats/interactive)
├── translator.py    # Core translation engine (GameTranslator class)
├── memory.py        # Translation memory with similarity search
├── glossary.py      # Glossary management (add/remove/import/export)
├── excel_io.py      # Excel read/write with confidence formatting
├── config.yaml      # Configuration (backend, model, thresholds)
├── requirements.txt # Python dependencies
└── data/
    ├── glossary.json            # Game terminology dictionary
    ├── style_guide.md           # Translation style rules
    └── translation_memory.json  # Learned translation pairs
```

---

# AI ゲーム翻訳ツール（日本語）

Claude AIを活用した日本語→英語のゲームテキスト翻訳CLIツール。
人間の修正から継続学習し、用語・トーンの一貫性を保ちながら翻訳精度を向上させます。

## 特徴

- **翻訳メモリ**: 承認済み翻訳を蓄積し、新規テキストの翻訳時にfew-shotとして活用
- **用語集管理**: ゲーム固有の用語（スキル・アイテム・UI等）の一貫性を保証
- **信頼度スコア**: 過去の承認済み翻訳との類似度に基づく品質評価
- **Excel入出力**: Excelから原文を読み取り、翻訳＋信頼度スコア付きで出力
- **継続学習**: 人間が修正した翻訳をインポートして将来の翻訳精度を向上
- **デュアルバックエンド**: Anthropic APIキーまたはClaude Codeサブスクリプション（APIキー不要）で動作
- **対話モード**: 1文ずつ翻訳→承認/修正/スキップのワークフロー

## クイックスタート

```bash
pip install -r requirements.txt

# Excelファイルを翻訳（日本語テキスト列を自動検出）
python cli.py translate input.xlsx -o output.xlsx

# 人間が修正した翻訳をインポート（将来の翻訳精度向上）
python cli.py learn corrected.xlsx --source-col A --translation-col B --corrected-col C

# ゲーム用語集の管理
python cli.py glossary add "必殺技" "Special Move"
python cli.py glossary list

# 対話モード（承認/修正ワークフロー）
python cli.py interactive

# 翻訳メモリの統計表示
python cli.py stats
```

## バックエンド設定

`config.yaml` を編集:

```yaml
# "auto" = APIキーがあればAPI、なければClaude Code CLI
# "api"  = Anthropic API（ANTHROPIC_API_KEYが必要）
# "cli"  = Claude Code CLI（サブスクリプション利用、APIキー不要）
backend: "auto"
```

## License / ライセンス

MIT
