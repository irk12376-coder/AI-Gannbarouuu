"""市場別テーマ履歴の管理(重複防止台帳)。

data/history/<market>.json に、これまで生成した回のテーマ・切り口・結論の
要約と採番を積み上げる。新規生成前に必ずこれを読み、content_brain の
プロンプトへ「既出テーマ一覧」として渡すことで直訳/使い回しを防ぐ。
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR

HISTORY_DIR = DATA_DIR / "history"


def _history_path(market: str) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return HISTORY_DIR / f"{market}.json"


def load_history(market: str) -> list[dict[str, Any]]:
    path = _history_path(market)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^\w一-龯ぁ-んァ-ヶ가-힣]+", "", text)


def _token_overlap(a: str, b: str) -> float:
    """簡易的な文字3-gram Jaccard類似度(依存ライブラリなしの重複検知用)。"""
    def grams(s: str) -> set[str]:
        s = _normalize(s)
        return {s[i:i + 3] for i in range(max(len(s) - 2, 1))}

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    union = len(ga | gb)
    return inter / union if union else 0.0


def next_seq_number(market: str) -> int:
    history = load_history(market)
    return len(history) + 1


def is_duplicate_theme(market: str, theme: str, conclusion: str, threshold: float = 0.55) -> dict[str, Any] | None:
    """既出テーマ・結論と高い類似度のものがあれば、その履歴エントリを返す。"""
    for entry in load_history(market):
        theme_sim = _token_overlap(theme, entry.get("theme", ""))
        conclusion_sim = _token_overlap(conclusion, entry.get("conclusion", ""))
        if theme_sim >= threshold and conclusion_sim >= threshold:
            return entry
    return None

def recent_theme_summaries(market: str, limit: int = 40) -> list[str]:
    history = load_history(market)
    out = []
    for entry in history[-limit:]:
        out.append(
            f"#{entry['seq']:04d} [{entry['slot_id']}] {entry['theme']} / 結論:{entry.get('conclusion', '')}"
        )
    return out


def record_run(
    market: str,
    slot_id: str,
    theme: str,
    conclusion: str,
    title: str,
    output_dir: str,
    theme_category: str,
) -> dict[str, Any]:
    history = load_history(market)
    entry = {
        "seq": next_seq_number(market),
        "market": market,
        "slot_id": slot_id,
        "theme_category": theme_category,
        "theme": theme,
        "conclusion": conclusion,
        "title": title,
        "output_dir": output_dir,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    history.append(entry)
    with open(_history_path(market), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    return entry
