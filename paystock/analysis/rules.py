"""売り時 / 買い時の判定ルール。

方針:
  - 判定は「スコア」に集約する。正なら売り方向、負なら買い方向。
  - どのルールが何点を出したかを必ず Reason として残す。ブラックボックスにしない。
  - 損切りだけはスコアを介さないハードルールにする。撤退判断を他の指標で
    薄めてしまうと、下落トレンドで持ち続ける典型的な負け方をするため。

ここは投資助言ではなく、あくまで機械的なルールの当てはめである点に注意。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Action, Indicators, Reason


@dataclass(frozen=True)
class RuleConfig:
    """判定に使う閾値。すべて settings.yaml から差し替えられる。"""

    rsi_overbought: float = 70.0
    rsi_very_overbought: float = 78.0
    rsi_oversold: float = 30.0
    stop_loss_pct: float = -12.0        # 取得単価からこの率まで下げたら損切り検討
    big_gain_pct: float = 50.0          # 「大きな含み益」とみなす水準
    atr_stop_multiple: float = 2.5      # トレーリングストップの ATR 倍率
    near_high_pct: float = 3.0          # 52週高値からこの範囲内なら「高値圏」
    trim_score: float = 25.0            # 一部利確の閾値
    sell_score: float = 45.0            # 売却検討の閾値
    buy_score: float = -25.0            # 買い増し検討の閾値
    reward_risk: float = 2.0            # 目標株価を決める際のリスクリワード比


def trailing_stop(ind: Indicators, cfg: RuleConfig) -> float | None:
    """シャンデリア・エグジット方式のトレーリングストップ。

    52週高値から ATR の一定倍を引いた値。ボラティリティが高い銘柄ほど
    ストップが遠くなるので、ノイズで振り落とされにくい。
    """
    if ind.high_52w is None or ind.atr14 is None:
        return None
    return ind.high_52w - cfg.atr_stop_multiple * ind.atr14


def _trend_reasons(ind: Indicators, cfg: RuleConfig) -> list[Reason]:
    reasons: list[Reason] = []
    price = ind.price

    if ind.sma25 is not None and ind.sma75 is not None:
        if ind.sma25 < ind.sma75:
            reasons.append(
                Reason(
                    "デッドクロス圏",
                    18.0,
                    f"25日線({ind.sma25:,.1f}) が 75日線({ind.sma75:,.1f}) を下回っている",
                )
            )
        else:
            reasons.append(
                Reason(
                    "上昇トレンド継続",
                    -12.0,
                    f"25日線({ind.sma25:,.1f}) が 75日線({ind.sma75:,.1f}) より上",
                )
            )

    if ind.sma25 is not None:
        if price < ind.sma25:
            reasons.append(
                Reason("25日線割れ", 10.0, f"終値 {price:,.1f} < 25日線 {ind.sma25:,.1f}")
            )
        else:
            reasons.append(
                Reason("25日線上", -6.0, f"終値 {price:,.1f} >= 25日線 {ind.sma25:,.1f}")
            )

    if ind.sma200 is not None and price < ind.sma200:
        reasons.append(
            Reason(
                "200日線割れ",
                12.0,
                f"長期トレンドが崩れている (200日線 {ind.sma200:,.1f})",
            )
        )

    return reasons


def _momentum_reasons(ind: Indicators, cfg: RuleConfig) -> list[Reason]:
    reasons: list[Reason] = []

    if ind.rsi14 is not None:
        if ind.rsi14 >= cfg.rsi_very_overbought:
            reasons.append(
                Reason("RSI 過熱", 20.0, f"RSI {ind.rsi14:.1f} — 短期的な買われすぎ")
            )
        elif ind.rsi14 >= cfg.rsi_overbought:
            reasons.append(Reason("RSI やや過熱", 12.0, f"RSI {ind.rsi14:.1f}"))
        elif ind.rsi14 <= cfg.rsi_oversold:
            reasons.append(
                Reason("RSI 売られすぎ", -15.0, f"RSI {ind.rsi14:.1f} — 反発余地")
            )

    if ind.macd_hist is not None and ind.macd_hist_prev is not None:
        if ind.macd_hist_prev > 0 >= ind.macd_hist:
            reasons.append(
                Reason("MACD 下向き転換", 15.0, "ヒストグラムがプラスからマイナスへ")
            )
        elif ind.macd_hist_prev <= 0 < ind.macd_hist:
            reasons.append(
                Reason("MACD 上向き転換", -12.0, "ヒストグラムがマイナスからプラスへ")
            )

    if ind.bb_upper is not None and ind.price > ind.bb_upper:
        reasons.append(
            Reason("ボリンジャー上限超え", 12.0, f"上限 {ind.bb_upper:,.1f} を超過")
        )
    if ind.bb_lower is not None and ind.price < ind.bb_lower:
        reasons.append(
            Reason("ボリンジャー下限割れ", -10.0, f"下限 {ind.bb_lower:,.1f} を下回る")
        )

    return reasons


def _position_reasons(
    ind: Indicators, cfg: RuleConfig, pnl_pct: float | None
) -> list[Reason]:
    reasons: list[Reason] = []

    dd = ind.drawdown_from_52w_high
    if dd is not None and dd >= -cfg.near_high_pct:
        overheated = ind.rsi14 is not None and ind.rsi14 >= 65
        reasons.append(
            Reason(
                "52週高値圏",
                8.0 if overheated else 2.0,
                f"52週高値 {ind.high_52w:,.1f} から {dd:+.1f}%",
            )
        )

    stop = trailing_stop(ind, cfg)
    if stop is not None and ind.price < stop:
        reasons.append(
            Reason(
                "トレーリングストップ抵触",
                25.0,
                f"高値から ATR×{cfg.atr_stop_multiple} ({stop:,.1f}) を割り込んだ",
            )
        )

    if pnl_pct is not None and pnl_pct >= cfg.big_gain_pct:
        fading = ind.macd_hist is not None and ind.macd_hist < 0
        reasons.append(
            Reason(
                "大きな含み益",
                10.0 if fading else 4.0,
                f"含み益 {pnl_pct:+.1f}% — 利益確定を検討する水準"
                + ("(モメンタムも減速)" if fading else ""),
            )
        )

    if ind.volume_ratio is not None and ind.volume_ratio >= 2.0:
        direction = "上放れ" if (ind.macd_hist or 0) > 0 else "下放れ"
        reasons.append(
            Reason(
                "出来高急増",
                -6.0 if direction == "上放れ" else 8.0,
                f"20日平均の {ind.volume_ratio:.1f} 倍 ({direction})",
            )
        )

    return reasons


def evaluate(
    ind: Indicators,
    cfg: RuleConfig | None = None,
    pnl_pct: float | None = None,
) -> tuple[Action, float, list[Reason], float | None, float | None]:
    """指標一式から (アクション, スコア, 根拠, 損切り価格, 目標株価) を返す。

    pnl_pct を渡すと保有銘柄向けの判定 (損切り・利確) が有効になる。
    None のままだと新規候補のスクリーニング向けの判定になる。
    """
    cfg = cfg or RuleConfig()

    reasons = (
        _trend_reasons(ind, cfg)
        + _momentum_reasons(ind, cfg)
        + _position_reasons(ind, cfg, pnl_pct)
    )
    score = sum(r.score for r in reasons)

    # 損切りはハードルール。スコアの合計に関係なく最優先で発火させる。
    hard_stop = pnl_pct is not None and pnl_pct <= cfg.stop_loss_pct
    if hard_stop:
        reasons.insert(
            0,
            Reason(
                "損切りライン到達",
                30.0,
                f"含み損 {pnl_pct:+.1f}% が設定値 {cfg.stop_loss_pct:.1f}% を超過",
            ),
        )
        score += 30.0
        action = Action.STOP_LOSS
    elif score >= cfg.sell_score:
        action = Action.SELL
    elif score >= cfg.trim_score:
        action = Action.TRIM
    elif score <= cfg.buy_score:
        action = Action.BUY_MORE
    else:
        action = Action.HOLD

    stop_price = trailing_stop(ind, cfg)
    target_price = None
    if stop_price is not None and stop_price < ind.price:
        risk = ind.price - stop_price
        target_price = ind.price + cfg.reward_risk * risk

    return action, score, reasons, stop_price, target_price
