"""「これから上がりそうな銘柄」のスクリーニング。

考え方はシンプルなトレンドフォロー + 押し目拾い:

  1. 上昇トレンドにある銘柄だけを対象にする (200日線より上・25日線 > 75日線)。
     下降トレンドの銘柄を「安いから」と拾うのが一番損をしやすいため。
  2. その中で 12ヶ月モメンタム (直近1ヶ月を除く) が強いものを上位に置く。
     直近1ヶ月を除くのは、短期の急騰直後は反落しやすいという経験則から。
  3. ただし過熱している (RSI が高すぎる・25日線から離れすぎ) ものは減点する。
     良い銘柄でも、高値掴みになれば結果は同じなので。

将来の株価を当てるものではなく、条件に合う銘柄を機械的に絞り込むだけの道具。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..data import PriceSource, PriceSourceError
from ..models import Indicators, Reason, ScreenHit
from ..analysis import indicators as ind_mod
from .universe import UniverseEntry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScreenConfig:
    require_uptrend: bool = True     # 200日線・移動平均の並びを必須にするか
    max_rsi: float = 75.0            # これ以上は過熱として除外
    min_price: float = 100.0         # 低位株はノイズが大きいので除外
    max_extension_pct: float = 18.0  # 25日線からの上方乖離の許容上限 (%)
    top_n: int = 15
    workers: int = 8


def _momentum_12_1(ind: Indicators) -> float | None:
    """12ヶ月リターンから直近1ヶ月ぶんを差し引いたモメンタム (%)。"""
    if ind.return_12m is None or ind.return_1m is None:
        return None
    return ind.return_12m - ind.return_1m


def _passes_filters(ind: Indicators, cfg: ScreenConfig) -> str | None:
    """除外理由を返す。通過したら None。"""
    if ind.price < cfg.min_price:
        return f"株価 {ind.price:,.0f} 円 が下限 {cfg.min_price:,.0f} 円 未満"
    if ind.rsi14 is not None and ind.rsi14 > cfg.max_rsi:
        return f"RSI {ind.rsi14:.1f} が過熱"
    if cfg.require_uptrend:
        if ind.sma200 is not None and ind.price < ind.sma200:
            return "200日線を下回っている"
        if ind.sma25 is not None and ind.sma75 is not None and ind.sma25 < ind.sma75:
            return "25日線が75日線を下回っている"
    if ind.sma25:
        extension = (ind.price - ind.sma25) / ind.sma25 * 100.0
        if extension > cfg.max_extension_pct:
            return f"25日線から {extension:.1f}% 上方乖離しており高値掴みリスク"
    return None


def _quality_reasons(ind: Indicators, cfg: ScreenConfig) -> list[Reason]:
    """モメンタム以外の加点・減点。score は正が強気。"""
    reasons: list[Reason] = []

    if ind.rsi14 is not None:
        if 40.0 <= ind.rsi14 <= 62.0:
            reasons.append(
                Reason("押し目圏", 12.0, f"RSI {ind.rsi14:.1f} — 過熱せず上昇余地")
            )
        elif ind.rsi14 > 70.0:
            reasons.append(Reason("やや過熱", -8.0, f"RSI {ind.rsi14:.1f}"))

    if ind.macd_hist is not None and ind.macd_hist_prev is not None:
        if ind.macd_hist_prev <= 0 < ind.macd_hist:
            reasons.append(Reason("MACD 上向き転換", 15.0, "直近で買いシグナル点灯"))
        elif ind.macd_hist > 0:
            reasons.append(Reason("MACD プラス圏", 6.0, "上昇モメンタム継続中"))

    dd = ind.drawdown_from_52w_high
    if dd is not None:
        if dd >= -5.0:
            reasons.append(Reason("52週高値圏", 10.0, f"高値から {dd:+.1f}%"))
        elif dd <= -30.0:
            reasons.append(
                Reason("高値から大きく下落", -10.0, f"高値から {dd:+.1f}%")
            )

    if ind.volume_ratio is not None and ind.volume_ratio >= 1.5:
        reasons.append(
            Reason("出来高増加", 8.0, f"20日平均の {ind.volume_ratio:.1f} 倍")
        )

    if ind.volatility is not None and ind.volatility > 55.0:
        reasons.append(
            Reason("ボラティリティ過大", -10.0, f"年率 {ind.volatility:.0f}%")
        )

    return reasons


def _rank_points(rank: int, total: int) -> float:
    """モメンタム順位を 0〜40 点に変換する (1位が 40 点)。"""
    if total <= 1:
        return 40.0
    return 40.0 * (1.0 - (rank / (total - 1)))


def screen(
    entries: list[UniverseEntry],
    source: PriceSource,
    cfg: ScreenConfig | None = None,
    exclude_codes: set[str] | None = None,
    days: int = 400,
) -> tuple[list[ScreenHit], list[str]]:
    """ユニバースを評価して、上位候補と取得エラーの一覧を返す。"""
    cfg = cfg or ScreenConfig()
    exclude_codes = exclude_codes or set()
    targets = [e for e in entries if e.code not in exclude_codes]
    errors: list[str] = []

    def fetch(entry: UniverseEntry) -> tuple[UniverseEntry, Indicators | None, str | None]:
        try:
            df = source.history(entry.code, days=days)
            return entry, ind_mod.compute(df), None
        except (PriceSourceError, ValueError, KeyError) as exc:
            return entry, None, f"{entry.code} {entry.name}: {exc}"

    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        results = list(pool.map(fetch, targets))

    candidates: list[tuple[UniverseEntry, Indicators, float]] = []
    for entry, ind, err in results:
        if err:
            errors.append(err)
            continue
        assert ind is not None
        if _passes_filters(ind, cfg) is not None:
            continue
        momentum = _momentum_12_1(ind)
        if momentum is None:
            continue
        candidates.append((entry, ind, momentum))

    # モメンタムの強い順に並べ、順位を得点化する
    candidates.sort(key=lambda item: -item[2])
    total = len(candidates)

    hits: list[ScreenHit] = []
    for rank, (entry, ind, momentum) in enumerate(candidates):
        reasons = [
            Reason(
                "12ヶ月モメンタム",
                _rank_points(rank, total),
                f"直近1ヶ月を除く12ヶ月騰落率 {momentum:+.1f}% (対象 {total} 銘柄中 {rank + 1} 位)",
            )
        ]
        reasons += _quality_reasons(ind, cfg)
        hits.append(
            ScreenHit(
                symbol=entry.code,
                name=entry.name,
                sector=entry.sector,
                price=ind.price,
                score=sum(r.score for r in reasons),
                indicators=ind,
                reasons=sorted(reasons, key=lambda r: -r.score),
            )
        )

    hits.sort(key=lambda h: -h.score)
    return hits[: cfg.top_n], errors
