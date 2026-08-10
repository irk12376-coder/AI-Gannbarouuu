"""portfolio.yaml の読み込みと取得単価の逆算のテスト。"""

from __future__ import annotations

import pytest

from paystock import config as config_mod
from paystock.models import AssetKind, Holding

MINIMAL = """
holdings:
  - name: 日本製鉄
    symbol: "5401"
    kind: jp_stock
    sector: 鉄鋼
    shares: 84.2091080572
    market_value: 57981
    pnl: 7960
  - name: 現金
    kind: cash
    market_value: 1531
"""


def write(tmp_path, text: str):
    path = tmp_path / "portfolio.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_holdings_and_cash(tmp_path):
    cfg = config_mod.load(write(tmp_path, MINIMAL))

    assert len(cfg.holdings) == 2
    assert cfg.cash == 1531
    assert cfg.total_value == 57981 + 1531


def test_average_cost_is_derived_from_value_and_pnl():
    """PayPay 証券の画面には取得単価が無いので、評価額と損益から復元する。"""
    h = Holding(
        name="日本製鉄",
        kind=AssetKind.JP_STOCK,
        symbol="5401",
        shares=84.2091080572,
        market_value=57981,
        pnl=7960,
    )

    assert h.cost == pytest.approx(50021.0)
    assert h.avg_cost == pytest.approx(50021.0 / 84.2091080572)
    assert h.pnl_pct == pytest.approx(15.91, abs=0.01)
    assert h.snapshot_price == pytest.approx(57981 / 84.2091080572)


def test_loss_making_holding_reports_negative_pnl_pct():
    h = Holding(
        name="三菱重工業",
        kind=AssetKind.JP_STOCK,
        symbol="7011",
        shares=10.4738403341,
        market_value=41456,
        pnl=-8547,
    )

    assert h.cost == pytest.approx(50003.0)
    assert h.pnl_pct == pytest.approx(-17.09, abs=0.01)


def test_cash_and_private_are_not_priceable():
    cash = Holding(name="現金", kind=AssetKind.CASH, market_value=1531)
    private = Holding(name="SpaceX", kind=AssetKind.PRIVATE, market_value=10019)

    assert not cash.priceable
    assert not private.priceable
    assert private.data_symbol is None


def test_fund_uses_proxy_symbol_for_pricing():
    fund = Holding(
        name="eMAXIS NASDAQ100",
        kind=AssetKind.FUND,
        proxy_symbol="^NDX",
        market_value=10181,
        pnl=180,
    )

    assert fund.priceable
    assert fund.data_symbol == "^NDX"


def test_comma_separated_numbers_are_accepted(tmp_path):
    """アプリの表示をそのままコピーしても読めるようにする。"""
    cfg = config_mod.load(
        write(
            tmp_path,
            """
holdings:
  - name: JX金属
    symbol: "5016"
    kind: jp_stock
    shares: 12.7078366688
    market_value: "48,629"
    pnl: "-1,377"
""",
        )
    )

    assert cfg.holdings[0].market_value == 48629.0
    assert cfg.holdings[0].pnl == -1377.0


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="見つかりません"):
        config_mod.load(tmp_path / "nope.yaml")


def test_unknown_kind_is_rejected(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="kind が不正"):
        config_mod.load(
            write(tmp_path, "holdings:\n  - name: X\n    kind: 暗号資産\n")
        )


def test_stock_without_symbol_is_rejected(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="symbol"):
        config_mod.load(
            write(tmp_path, "holdings:\n  - name: X\n    kind: jp_stock\n")
        )


def test_fund_without_proxy_symbol_is_rejected(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="proxy_symbol"):
        config_mod.load(write(tmp_path, "holdings:\n  - name: X\n    kind: fund\n"))


def test_unknown_rule_key_is_rejected(tmp_path):
    """設定ミスを黙って無視すると、意図しない閾値で動いてしまう。"""
    with pytest.raises(config_mod.ConfigError, match="未知の項目"):
        config_mod.load(write(tmp_path, MINIMAL + "\nrules:\n  stoploss_pct: -10\n"))


def test_rules_and_trade_sections_override_defaults(tmp_path):
    cfg = config_mod.load(
        write(
            tmp_path,
            MINIMAL + "\nrules:\n  stop_loss_pct: -8.0\ntrade:\n  max_order_amount: 5000\n",
        )
    )

    assert cfg.rules.stop_loss_pct == -8.0
    assert cfg.trade.max_order_amount == 5000
    assert cfg.trade.dry_run is True       # 指定しなかった項目は安全側の既定のまま


def test_real_portfolio_file_parses():
    """リポジトリに置いてある実データが常に読める状態を保つ。"""
    cfg = config_mod.load("portfolio.yaml")

    assert cfg.total_value == pytest.approx(184290.0)
    assert {h.name for h in cfg.holdings} >= {"日本製鉄", "JX金属", "三菱重工業"}
    assert cfg.trade.enabled is False      # 既定で自動発注が無効であること
