"""レポート出力のテスト。

数字の正しさは他のテストで見ているので、ここでは
「崩れないこと」「誤解を招く表示をしないこと」を確認する。
"""

from __future__ import annotations

import numpy as np

from conftest import FakeSource, make_bars, trending
from paystock.analysis import portfolio as portfolio_mod
from paystock.config import AppConfig
from paystock.models import AssetKind, Holding
from paystock.report import html as html_report
from paystock.report import text as text_report


def build_review(**kwargs):
    frames = {"5401": trending(), "7011": make_bars(np.linspace(3000, 1500, 400))}
    cfg = AppConfig(
        holdings=[
            Holding(
                name="日本製鉄",
                kind=AssetKind.JP_STOCK,
                symbol="5401",
                sector="鉄鋼",
                shares=10.0,
                market_value=float(trending()["close"].iloc[-1]) * 10,
                pnl=5000,
            ),
            Holding(
                name="三菱重工業",
                kind=AssetKind.JP_STOCK,
                symbol="7011",
                sector="機械",
                shares=10.0,
                market_value=15000,
                pnl=-8000,
            ),
            Holding(name="現金", kind=AssetKind.CASH, market_value=1531),
        ]
    )
    return portfolio_mod.review(cfg, FakeSource(frames), **kwargs)


def test_text_report_contains_every_holding():
    output = text_report.render_review(build_review())

    assert "日本製鉄" in output
    assert "三菱重工業" in output


def test_text_report_always_carries_the_disclaimer():
    assert text_report.DISCLAIMER in text_report.render_review(build_review())


def test_offline_mode_is_loudly_marked():
    """ダミー価格を本物と取り違えないよう、必ず警告を出す。"""
    output = text_report.render_review(build_review(offline=True))

    assert "ダミーデータ" in output


def test_table_columns_line_up_with_japanese_text():
    """全角文字を含んでも表の桁が揃うこと。"""
    rows = [["日本製鉄", "5401"], ["A", "8035"]]
    lines = text_report.table(["銘柄", "コード"], rows).splitlines()
    widths = {text_report.width(line) for line in lines}

    assert len(widths) == 1


def test_width_counts_fullwidth_as_two_columns():
    assert text_report.width("日本") == 4
    assert text_report.width("ab") == 2


def test_html_report_is_self_contained():
    """外部リソースを読み込まない (オフラインでもスマホでそのまま開ける)。"""
    output = html_report.render_review(build_review())

    assert output.startswith("<!doctype html>")
    assert "<script" not in output
    assert "http://" not in output.replace("http://www.w3.org", "")


def test_html_report_escapes_holding_names():
    cfg = AppConfig(
        holdings=[
            Holding(
                name="<script>alert(1)</script>",
                kind=AssetKind.PRIVATE,
                market_value=100,
            )
        ]
    )
    output = html_report.render_review(portfolio_mod.review(cfg, FakeSource({})))

    assert "<script>alert(1)</script>" not in output
    assert "&lt;script&gt;" in output


def test_html_report_supports_both_color_schemes():
    output = html_report.render_review(build_review())

    assert "prefers-color-scheme: dark" in output


def test_stop_line_is_labelled_differently_once_breached():
    """現在値がストップを下回っているのに「損切りライン」とだけ書くと誤解を招く。"""
    review = build_review()
    breached = [
        v
        for v in review.verdicts
        if v.quote and v.stop_price and v.quote.price < v.stop_price
    ]
    assert breached, "テスト前提: ストップを割った銘柄が 1 つはあること"

    output = text_report.render_verdict_detail(breached[0])
    assert "既に下回っており" in output


def test_screen_report_handles_no_hits():
    output = text_report.render_screen([], [])

    assert "条件に合致する銘柄はありません" in output
    assert text_report.DISCLAIMER in output
