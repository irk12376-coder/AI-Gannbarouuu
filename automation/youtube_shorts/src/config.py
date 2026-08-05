"""設定ファイル(channels.yaml / style.yaml)の読み込みユーティリティ."""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
FONTS_FALLBACK_DIR = ROOT / "fonts"


@functools.lru_cache(maxsize=None)
def load_channels() -> dict[str, Any]:
    with open(CONFIG_DIR / "channels.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


@functools.lru_cache(maxsize=None)
def load_style() -> dict[str, Any]:
    with open(CONFIG_DIR / "style.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class SlotConfig:
    market: str
    slot_id: str
    label: str
    publish_time_local: str
    theme_pool: str
    theme_weights: dict[str, float]
    finance_disclaimer_required: bool
    channel_label: str
    language: str
    locale: str
    timezone: str
    tts_voice: str
    tts_voice_alt: str
    default_category_id: str
    hashtag_pool: list[str]


def get_market(market: str) -> dict[str, Any]:
    markets = load_channels()["markets"]
    if market not in markets:
        raise KeyError(f"unknown market: {market}")
    return markets[market]


def get_slot(market: str, slot_id: str) -> SlotConfig:
    m = get_market(market)
    for slot in m["slots"]:
        if slot["id"] == slot_id:
            return SlotConfig(
                market=market,
                slot_id=slot["id"],
                label=slot["label"],
                publish_time_local=slot["publish_time_local"],
                theme_pool=slot["theme_pool"],
                theme_weights=slot["theme_weights"],
                finance_disclaimer_required=slot.get("finance_disclaimer_required", False),
                channel_label=m["channel_label"],
                language=m["language"],
                locale=m["locale"],
                timezone=m["timezone"],
                tts_voice=m["tts_voice"],
                tts_voice_alt=m.get("tts_voice_alt", m["tts_voice"]),
                default_category_id=m.get("default_category_id", "27"),
                hashtag_pool=m.get("hashtag_pool", []),
            )
    raise KeyError(f"unknown slot '{slot_id}' for market '{market}'")


def iter_all_slots():
    for market, cfg in load_channels()["markets"].items():
        if not cfg.get("enabled", True):
            continue
        for slot in cfg["slots"]:
            yield get_slot(market, slot["id"])
