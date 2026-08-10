"""ポートフォリオ診断のテスト。"""

from __future__ import annotations

import numpy as np
import pytest

from conftest import FakeSource, make_bars, trending
from paystock.analysis import portfolio as portfolio_mod
from paystock.config import AppConfig
from paystock.models import Action, AssetKind, Holding


def holding(name, symbol, sector, value, pnl=0.0, shares=10.0, price=None):
    """評価額と株数から Holding を作る。price を渡すとその単価に合わせる。"""
    if price is not None:
        shares = value / price
    return Holding(
        name=name,
        kind=AssetKind.JP_STOCK,
        symbol=symbol,
        sector=sector,
        shares=shares,
        market_value=value,
        pnl=pnl,
    )


def test_cash_and_private_holdings_do_not_break_the_review():
    cfg = AppConfig(
        holdings=[
            Holding(name="現金", kind=AssetKind.CASH, market_value=1531),
            Holding(
                name="SpaceX",
                kind=AssetKind.PRIVATE,
                sector="未上場",
                market_value=10019,
                pnl=-2982,
            ),
        ]
    )
    review = portfolio_mod.review(cfg, FakeSource({}))

    # 現金は判定行に出さず、未上場は「判定不可」として残す
    assert len(review.verdicts) == 1
    assert review.verdicts[0].action is Action.NO_SIGNAL
    assert review.cash == 1531


def test_price_fetch_failure_is_reported_not_fatal():
    cfg = AppConfig(holdings=[holding("取れない銘柄", "9999", "その他", 10000)])
    review = portfolio_mod.review(cfg, FakeSource({}))

    assert review.verdicts[0].action is Action.NO_SIGNAL
    assert len(review.errors) == 1
    assert "9999" in review.errors[0]


def test_sector_concentration_is_flagged():
    frames = {code: trending() for code in ("5401", "5411", "8035")}
    last = float(frames["5401"]["close"].iloc[-1])
    cfg = AppConfig(
        holdings=[
            holding("日本製鉄", "5401", "鉄鋼", 60000, price=last),
            holding("JFE", "5411", "鉄鋼", 30000, price=last),
            holding("東エレク", "8035", "半導体", 10000, price=last),
        ]
    )
    review = portfolio_mod.review(cfg, FakeSource(frames))

    assert review.sector_weights["鉄鋼"] == pytest.approx(90.0, abs=1.0)
    labels = [r.label for r in review.risks]
    assert "セクター集中: 鉄鋼" in labels


def test_single_name_concentration_is_flagged():
    frames = {"5401": trending(), "8035": trending()}
    last = float(frames["5401"]["close"].iloc[-1])
    cfg = AppConfig(
        holdings=[
            holding("日本製鉄", "5401", "鉄鋼", 90000, price=last),
            holding("東エレク", "8035", "半導体", 10000, price=last),
        ]
    )
    review = portfolio_mod.review(cfg, FakeSource(frames))

    assert any(r.label.startswith("銘柄集中") for r in review.risks)


def test_low_cash_ratio_is_flagged():
    frames = {"5401": trending()}
    cfg = AppConfig(
        holdings=[
            holding("日本製鉄", "5401", "鉄鋼", 100000,
                    price=float(frames["5401"]["close"].iloc[-1])),
            Holding(name="現金", kind=AssetKind.CASH, market_value=100),
        ]
    )
    review = portfolio_mod.review(cfg, FakeSource(frames))

    assert any(r.label == "現金比率が低い" for r in review.risks)


def test_correlated_holdings_produce_a_high_average_correlation():
    """同じ動きをする銘柄ばかりだと相関が 1 に近づく = 分散が効いていない。"""
    base = trending()
    frames = {"5401": base, "5411": base.copy()}
    cfg = AppConfig(
        holdings=[
            holding("日本製鉄", "5401", "鉄鋼", 50000),
            holding("JFE", "5411", "鉄鋼", 50000),
        ]
    )
    review = portfolio_mod.review(cfg, FakeSource(frames))

    assert review.avg_correlation == pytest.approx(1.0, abs=0.01)


def test_split_like_price_gap_raises_a_warning():
    """設定の単価と現在値が大きく違うときは、株式分割を疑う警告を出す。"""
    frames = {"5401": make_bars(np.full(400, 700.0))}
    cfg = AppConfig(
        holdings=[
            # 3,500 円 想定で書かれた設定 (分割前の単価が残っているケース)
            holding("日本製鉄", "5401", "鉄鋼", 35000, shares=10.0)
        ]
    )
    review = portfolio_mod.review(cfg, FakeSource(frames))

    assert any("乖離" in w for w in review.verdicts[0].warnings)


def test_matching_prices_produce_no_split_warning():
    frames = {"5401": make_bars(np.full(400, 700.0))}
    cfg = AppConfig(holdings=[holding("日本製鉄", "5401", "鉄鋼", 7000, shares=10.0)])
    review = portfolio_mod.review(cfg, FakeSource(frames))

    assert review.verdicts[0].warnings == []


def test_live_pnl_uses_current_price_not_the_snapshot():
    """設定に書いた評価額が古くても、損益率は現在値で計算し直す。"""
    frames = {"5401": make_bars(np.full(400, 1200.0))}
    cfg = AppConfig(
        holdings=[
            # 取得単価 1,000 円 × 10 株。設定上の評価額は 10,500 円 (古い)
            holding("日本製鉄", "5401", "鉄鋼", 10500, pnl=500, shares=10.0)
        ]
    )
    review = portfolio_mod.review(cfg, FakeSource(frames))

    assert review.verdicts[0].live_pnl_pct == pytest.approx(20.0)
    assert review.verdicts[0].live_market_value == pytest.approx(12000.0)


def test_fund_is_evaluated_through_its_proxy_index():
    frames = {"^NDX": trending()}
    cfg = AppConfig(
        holdings=[
            Holding(
                name="eMAXIS NASDAQ100",
                kind=AssetKind.FUND,
                proxy_symbol="^NDX",
                sector="海外株式",
                market_value=10181,
                pnl=180,
            )
        ]
    )
    review = portfolio_mod.review(cfg, FakeSource(frames))
    verdict = review.verdicts[0]

    assert verdict.indicators is not None
    assert any("代理指標" in w for w in verdict.warnings)
    # 基準価額ではなく指数を見ているので、評価額は設定値のまま扱う
    assert verdict.live_market_value == pytest.approx(10181)


def test_history_is_fetched_once_per_symbol():
    """現在値のために同じ銘柄をもう一度取りに行っていないこと。"""
    frames = {"5401": trending()}
    source = FakeSource(frames)
    cfg = AppConfig(holdings=[holding("日本製鉄", "5401", "鉄鋼", 10000)])

    portfolio_mod.review(cfg, source)

    assert [c[0] for c in source.calls] == ["5401"]
