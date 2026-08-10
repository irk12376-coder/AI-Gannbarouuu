"""「今日やること」の整形。

前提は毎日読むこと。だから **対応が要らない日は短く終わる** ことを最優先にする。
毎日長文が届くツールは読まれなくなり、肝心の日の通知も見落とされる。
"""

from __future__ import annotations

from ..models import Action
from .actions import ActionItem
from .text import DISCLAIMER

_MARK = {
    Action.STOP_LOSS: "【最優先】損切り検討",
    Action.SELL: "【要対応】売却検討",
    Action.TRIM: "【要対応】一部利確",
    Action.BUY_MORE: "【検討】買い増し",
    Action.HOLD: "保有継続",
    Action.NO_SIGNAL: "判定不可",
}

NOTHING_TO_DO = "本日は対応が必要な銘柄はありません。"


def render(items: list[ActionItem], include_hold: bool = False) -> str:
    """操作リストをテキストにする。

    include_hold=False (既定) では、手を動かす必要のある項目と
    注意事項のある項目だけを出す。
    """
    todo = [i for i in items if i.actionable]
    notes = [i for i in items if not i.actionable and i.cautions]

    blocks: list[str] = []

    if todo:
        blocks.append(f"■ 今日やること ({len(todo)}件)")
        for n, item in enumerate(todo, 1):
            lines = [f"{n}. {_MARK[item.action]}  {item.name} ({item.symbol or '－'})"]
            lines.append(f"   → {item.headline}")
            if item.price_note:
                lines.append(f"   価格の目安: {item.price_note}")
            for reason in item.reasons:
                lines.append(f"   ・{reason}")
            for caution in item.cautions:
                lines.append(f"   ⚠ {caution}")
            blocks.append("\n".join(lines))
    else:
        blocks.append(f"■ {NOTHING_TO_DO}")

    if notes:
        blocks.append("■ 注意しておくこと")
        for item in notes:
            for caution in item.cautions:
                blocks.append(f"   ⚠ {item.name}: {caution}")

    if include_hold:
        holds = [i for i in items if i.action is Action.HOLD]
        if holds:
            names = " / ".join(f"{i.name}" for i in holds)
            blocks.append(f"■ 保有継続 ({len(holds)}件): {names}")

    blocks.append(DISCLAIMER)
    return "\n\n".join(blocks)


def has_todo(items: list[ActionItem]) -> bool:
    """通知を送るべきか (対応事項か注意事項が 1 つでもあるか)。"""
    return any(i.actionable or i.cautions for i in items)
