"""スクリーニングのテスト。"""

from __future__ import annotations

import numpy as np

from conftest import FakeSource, choppy, falling, make_bars, trending
from paystock.screener import universe as universe_mod
from paystock.screener.screen import ScreenConfig, screen

ENTRIES = [
    universe_mod.UniverseEntry("1111", "上昇株", "機械"),
    universe_mod.UniverseEntry("2222", "下降株", "鉄鋼"),
    universe_mod.UniverseEntry("3333", "横ばい株", "食料品"),
]
FRAMES = {"1111": trending(), "2222": falling(), "3333": choppy()}


def test_downtrending_names_are_filtered_out():
    hits, _errors = screen(ENTRIES, FakeSource(FRAMES), ScreenConfig())

    assert "2222" not in {h.symbol for h in hits}


def test_uptrending_name_is_selected():
    hits, _errors = screen(ENTRIES, FakeSource(FRAMES), ScreenConfig())

    assert hits
    assert hits[0].symbol == "1111"


def test_relaxed_mode_keeps_more_names():
    strict, _e1 = screen(ENTRIES, FakeSource(FRAMES), ScreenConfig())
    relaxed, _e2 = screen(ENTRIES, FakeSource(FRAMES), ScreenConfig(require_uptrend=False))

    assert len(relaxed) >= len(strict)


def test_excluded_codes_are_skipped():
    hits, _errors = screen(
        ENTRIES, FakeSource(FRAMES), ScreenConfig(), exclude_codes={"1111"}
    )

    assert "1111" not in {h.symbol for h in hits}


def test_top_n_limits_the_result_size():
    hits, _errors = screen(ENTRIES, FakeSource(FRAMES), ScreenConfig(top_n=1))

    assert len(hits) <= 1


def test_missing_data_is_collected_as_an_error_not_raised():
    entries = ENTRIES + [universe_mod.UniverseEntry("9999", "データ無し", "その他")]
    hits, errors = screen(entries, FakeSource(FRAMES), ScreenConfig())

    assert any("9999" in e for e in errors)
    assert hits  # 他の銘柄の評価は継続する


def test_overheated_names_are_excluded():
    """RSI が上限を超える急騰銘柄は、条件に合っても候補から外す。"""
    frames = {"1111": trending(daily_pct=1.0)}
    entries = [universe_mod.UniverseEntry("1111", "急騰株", "機械")]

    hits, _errors = screen(entries, FakeSource(frames), ScreenConfig(max_rsi=50.0))

    assert hits == []


def test_penny_stocks_are_excluded():
    frames = {"1111": make_bars(np.linspace(10, 50, 400))}
    entries = [universe_mod.UniverseEntry("1111", "低位株", "機械")]

    hits, _errors = screen(entries, FakeSource(frames), ScreenConfig(min_price=100.0))

    assert hits == []


def test_scores_are_fully_explained_by_their_reasons():
    hits, _errors = screen(ENTRIES, FakeSource(FRAMES), ScreenConfig())

    for hit in hits:
        assert hit.score == sum(r.score for r in hit.reasons)
        assert hit.reasons


def test_universe_csv_loads(tmp_path):
    path = tmp_path / "u.csv"
    path.write_text("code,name,sector\n5401,日本製鉄,鉄鋼\n", encoding="utf-8")

    entries = universe_mod.load(path)

    assert entries == [universe_mod.UniverseEntry("5401", "日本製鉄", "鉄鋼")]


def test_universe_csv_missing_column_is_rejected(tmp_path):
    path = tmp_path / "u.csv"
    path.write_text("code,name\n5401,日本製鉄\n", encoding="utf-8")

    try:
        universe_mod.load(path)
    except ValueError as exc:
        assert "sector" in str(exc)
    else:
        raise AssertionError("列不足が検出されませんでした")


def test_bundled_universe_is_valid():
    """同梱の銘柄リストが壊れていないこと (コードは 4 桁、重複なし)。"""
    entries = universe_mod.load("data/universe_jp.csv")

    assert len(entries) > 100
    codes = [e.code for e in entries]
    assert len(codes) == len(set(codes))
    assert all(len(c) == 4 and c.isdigit() for c in codes)
