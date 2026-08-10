"""ポートフォリオ全体の診断。

個別銘柄の売り時判定 (rules.py) に加えて、ポートフォリオとしての
偏り — セクター集中・銘柄集中・相関の高さ・現金比率 — を見る。
個別に良い銘柄でも、同じ方向に動くものばかり持っていればリスクは足し算になる。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import AppConfig
from ..data import DEFAULT_HISTORY_DAYS, PriceSource, PriceSourceError, quote_from_history
from ..models import Action, AssetKind, Holding, Verdict
from . import indicators as ind_mod
from .rules import evaluate

log = logging.getLogger(__name__)

# ライブ株価とスクリーンショット時点の単価がこれ以上ずれていたら分割等を疑う
SPLIT_SUSPECT_RATIO = 0.35


@dataclass
class ConcentrationRisk:
    label: str
    weight_pct: float
    severity: str  # "高" | "中" | "低"
    detail: str


@dataclass
class PortfolioReview:
    """診断結果ひとまとめ。レポート出力はこれだけを見れば作れる。"""

    verdicts: list[Verdict] = field(default_factory=list)
    total_value: float = 0.0
    cash: float = 0.0
    sector_weights: dict[str, float] = field(default_factory=dict)
    risks: list[ConcentrationRisk] = field(default_factory=list)
    avg_correlation: float | None = None
    correlation_matrix: pd.DataFrame | None = None
    errors: list[str] = field(default_factory=list)
    offline: bool = False

    @property
    def cash_pct(self) -> float:
        return self.cash / self.total_value * 100.0 if self.total_value else 0.0

    def by_action(self, action: Action) -> list[Verdict]:
        return [v for v in self.verdicts if v.action is action]


def _weights(holdings: list[Holding], values: dict[str, float], total: float) -> dict[str, float]:
    """セクター別の構成比 (%)。"""
    out: dict[str, float] = {}
    for h in holdings:
        key = "現金" if h.kind is AssetKind.CASH else (h.sector or "その他")
        out[key] = out.get(key, 0.0) + values.get(h.name, h.market_value)
    if not total:
        return {}
    return {k: v / total * 100.0 for k, v in sorted(out.items(), key=lambda kv: -kv[1])}


def _concentration_risks(
    sector_weights: dict[str, float],
    verdicts: list[Verdict],
    total: float,
    cash_pct: float,
) -> list[ConcentrationRisk]:
    risks: list[ConcentrationRisk] = []

    for sector, pct in sector_weights.items():
        if sector == "現金":
            continue
        if pct >= 50.0:
            severity = "高"
        elif pct >= 30.0:
            severity = "中"
        else:
            continue
        risks.append(
            ConcentrationRisk(
                label=f"セクター集中: {sector}",
                weight_pct=pct,
                severity=severity,
                detail=f"{sector} だけで全体の {pct:.1f}% を占めている",
            )
        )

    for v in verdicts:
        value = v.live_market_value or v.holding.market_value
        pct = value / total * 100.0 if total else 0.0
        if pct >= 30.0:
            risks.append(
                ConcentrationRisk(
                    label=f"銘柄集中: {v.holding.name}",
                    weight_pct=pct,
                    severity="高" if pct >= 40.0 else "中",
                    detail=f"1 銘柄で全体の {pct:.1f}%。この銘柄の下落がそのまま全体に効く",
                )
            )

    if cash_pct < 5.0:
        risks.append(
            ConcentrationRisk(
                label="現金比率が低い",
                weight_pct=cash_pct,
                severity="中",
                detail=f"現金は {cash_pct:.1f}%。押し目で買い増す余力がほぼ無い",
            )
        )

    return risks


def _correlation(returns: dict[str, pd.Series]) -> tuple[pd.DataFrame | None, float | None]:
    """日次リターンの相関行列と、平均ペア相関を返す。"""
    if len(returns) < 2:
        return None, None
    frame = pd.DataFrame(returns).dropna()
    if len(frame) < 30:  # サンプルが少なすぎる相関は信用しない
        return None, None
    corr = frame.corr()
    # 上三角の非対角成分だけ平均する
    mask = np.triu(np.ones(corr.shape, dtype=bool), k=1)
    values = corr.to_numpy()[mask]
    return corr, float(np.nanmean(values)) if values.size else None


def review(
    config: AppConfig,
    source: PriceSource,
    days: int = DEFAULT_HISTORY_DAYS,
    offline: bool = False,
) -> PortfolioReview:
    """保有銘柄を 1 つずつ評価して、全体診断まで組み立てる。"""
    verdicts: list[Verdict] = []
    errors: list[str] = []
    returns: dict[str, pd.Series] = {}
    live_values: dict[str, float] = {}

    for holding in config.holdings:
        if holding.kind is AssetKind.CASH:
            continue

        if not holding.priceable or not holding.data_symbol:
            # 未上場など、市場価格が存在しない資産。評価はできないが表には出す。
            verdicts.append(
                Verdict(
                    holding=holding,
                    quote=None,
                    indicators=None,
                    action=Action.NO_SIGNAL,
                    warnings=[
                        "市場価格を取得できない資産です"
                        "(未上場・非流動性)。売買タイミングは機械的には判定できません。"
                    ],
                )
            )
            continue

        symbol = holding.data_symbol
        try:
            df = source.history(symbol, days=days)
            ind = ind_mod.compute(df)
        except (PriceSourceError, ValueError, KeyError) as exc:
            log.warning("%s (%s) の評価をスキップ: %s", holding.name, symbol, exc)
            errors.append(f"{holding.name} ({symbol}): {exc}")
            verdicts.append(
                Verdict(
                    holding=holding,
                    quote=None,
                    indicators=None,
                    action=Action.NO_SIGNAL,
                    warnings=[f"価格データを取得できませんでした: {exc}"],
                )
            )
            continue

        # 取得済みの df から現在値を作る。source.quote() を呼ぶと同じ銘柄を
        # もう一度取りに行くことになり、指標と現在値がずれる可能性もある。
        quote = quote_from_history(symbol, df)
        warnings: list[str] = []

        # 投資信託は連動指数で代用しているので、単価の突き合わせはしない
        if holding.kind is AssetKind.FUND:
            warnings.append(
                f"連動指数 {symbol} を代理指標として評価しています"
                "(基準価額そのものではありません)。"
            )
            pnl_pct = holding.pnl_pct
            live_values[holding.name] = holding.market_value
        else:
            snapshot = holding.snapshot_price
            if snapshot and abs(quote.price - snapshot) / snapshot > SPLIT_SUSPECT_RATIO:
                warnings.append(
                    f"設定上の単価 {snapshot:,.1f} 円 と現在値 {quote.price:,.1f} 円 が"
                    "大きく乖離しています。株式分割・併合や銘柄コードの誤りを確認してください。"
                )
            verdict_pnl = None
            avg = holding.avg_cost
            if avg:
                verdict_pnl = (quote.price - avg) / avg * 100.0
            pnl_pct = verdict_pnl if verdict_pnl is not None else holding.pnl_pct
            live_values[holding.name] = quote.price * holding.shares

        action, score, reasons, stop, target = evaluate(ind, config.rules, pnl_pct)
        verdicts.append(
            Verdict(
                holding=holding,
                quote=quote,
                indicators=ind,
                action=action,
                sell_score=score,
                reasons=sorted(reasons, key=lambda r: -abs(r.score)),
                stop_price=stop,
                target_price=target,
                warnings=warnings,
            )
        )
        returns[holding.name] = df["close"].pct_change().dropna()

    total = sum(
        live_values.get(h.name, h.market_value) for h in config.holdings
    )
    sector_weights = _weights(config.holdings, live_values, total)
    cash_pct = config.cash / total * 100.0 if total else 0.0
    corr, avg_corr = _correlation(returns)

    return PortfolioReview(
        verdicts=verdicts,
        total_value=total,
        cash=config.cash,
        sector_weights=sector_weights,
        risks=_concentration_risks(sector_weights, verdicts, total, cash_pct),
        avg_correlation=avg_corr,
        correlation_matrix=corr,
        errors=errors,
        offline=offline,
    )
