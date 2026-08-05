"""台本・メタデータ生成(content brain)。

Anthropic API (Claude) を使い、市場・時間枠ごとに:
  - 重複しないテーマの選定(履歴を踏まえた重み付き抽選 + 生成後の類似度チェック)
  - 7ページ構成の台本(見出し/本文/ナレーション)
  - 動画タイトル・概要欄・タグ・ハッシュタグ
  - 出典メモ(sources_notes用)
  - 金融免責文言の要否
を1本のJSONとして生成する。

ANTHROPIC_API_KEY が無い環境(ローカルのドライラン/CI設定前など)では、
テンプレートベースのプレースホルダ台本を返す。プレースホルダは
`is_placeholder: true` を持ち、そのまま実運用には使わない前提。
"""
from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass, field
from typing import Any

from . import dedupe
from .config import SlotConfig, load_style

MODEL = os.environ.get("CONTENT_BRAIN_MODEL", "claude-sonnet-5")
ENABLE_WEB_SEARCH = os.environ.get("CONTENT_BRAIN_WEB_SEARCH", "true").lower() != "false"
MAX_DEDUPE_RETRIES = 3

TONE_RULES = """
- 少し不穏で意味深なトーン。冒頭は短く強く。
- 専門用語を使いすぎない。不自然なAI文体を避ける。
- 同じ語尾・定型句(「実は」「知らないと危険」等)を毎回使い回さない。
- 断定しすぎず、根拠・条件・例外・研究の限界を示す。
- 心理学は「全員に当てはまる」ように断定しない。
- 金融・投資は利益を保証しない。特定の金融商品を無条件に推奨しない。
- 数字を使う場合は前提条件と計算根拠を明確にする。
- 都市伝説は「確認済みの事実」と「噂・仮説」を明確に分けて書く。
- ニュースは、記事の公開日と出来事の発生日を区別する。
""".strip()

STRUCTURE_RULES = """
7ページ構成(この順番で、読むと1つの話が完成するように):
1. 強いタイトルとフック
2. 身近な状況または問題提起
3. 理由・仕組み
4. 意外な事実または具体例
5. 一段深い説明
6. 誤解・限界・注意点・対処法
7. 結論と自然な保存・コメント・フォロー誘導(単なる煽りで終えず「知ってよかった」と思える情報を入れる)
""".strip()

JSON_SCHEMA_HINT = """
以下のJSONオブジェクトのみを出力してください(前後に説明文やコードブロック記法を付けないこと):

{
  "theme_category": "テーマカテゴリのスラッグ(与えられた候補から1つ)",
  "theme": "テーマの短い要約(重複判定に使うので具体的に)",
  "conclusion": "7枚目で伝える結論の短い要約(重複判定に使う)",
  "video_title": "YouTube Shorts用タイトル(100文字以内、煽りすぎない)",
  "description": "動画概要欄。導入/発生理由/カルーセル外の補足/誤解への注意/読者への問い/保存・フォロー誘導を含み、350〜600文字目安(日本語の場合)。本文の単純な繰り返しにしない。",
  "tags": ["YouTubeタグ", "..."],
  "hashtags": ["#設定済みハッシュタグプールから3〜5個、必要なら1つだけ独自語を追加可"],
  "finance_disclaimer_needed": true,
  "pages": [
    {"page": 1, "heading": "画面に出す短い見出し", "body": "画面に出す本文(改行位置が分かるように\\nで区切る)", "narration": "ナレーション読み上げ用テキスト(画面表示と完全一致でなくてよい)", "image_motif": "背景ビジュアルの短い指示(人物はシルエットのみ、文字/ロゴ/数字禁止、流血・死体・露骨な恐怖表現は禁止)"},
    ... 7件 ...
  ],
  "sources": [
    {"title": "資料名", "publisher": "発行元", "url": "URL", "published_date": "YYYY-MM-DD or 不明", "accessed_date": "YYYY-MM-DD", "note": "一次情報か二次情報か、事実か仮説かなどの短いメモ"}
  ]
}
""".strip()


@dataclass
class GeneratedScript:
    raw: dict[str, Any]
    is_placeholder: bool = False
    warnings: list[str] = field(default_factory=list)


def _pick_theme_category(slot: SlotConfig, avoid: set[str] | None = None) -> str:
    weights = dict(slot.theme_weights)
    if avoid:
        for k in avoid:
            weights.pop(k, None)
        if not weights:
            weights = dict(slot.theme_weights)
    categories = list(weights.keys())
    probs = list(weights.values())
    return random.choices(categories, weights=probs, k=1)[0]


def _build_prompt(slot: SlotConfig, theme_category: str, history_summaries: list[str], retry_note: str) -> str:
    style = load_style()
    motif_pool = "\n".join(f"- {m}" for m in style["background_style"]["motif_pool"])
    history_block = "\n".join(history_summaries) if history_summaries else "(まだ実績なし)"
    return f"""
あなたはショート動画(YouTube Shorts)向けの台本・企画担当です。
チャンネル: {slot.channel_label}
言語: {slot.language} (locale: {slot.locale})
投稿枠: {slot.label} ({slot.slot_id})
今回のテーマカテゴリ: {theme_category}

# トーン・文章ルール
{TONE_RULES}

# 構成ルール
{STRUCTURE_RULES}

# 調査・安全性ルール
- 書く前に必ず調べること。一次情報・公的機関・原著論文を優先する。
- Web検索が使える場合は積極的に使い、出典のURL・発行元・公開日を記録する。
- 確認できない噂や仮説は、確認済みの事実と明確に区別して書く。
- 金融・投資テーマでは利益を保証しない。「一般的な情報であり、特定の商品を推奨するものではない」旨を概要欄で示す。

# 既出テーマ(切り口・事例・結論を重複させないこと)
{history_block}
{retry_note}

# 背景ビジュアルのモチーフ候補(各ページのimage_motifはこの雰囲気に沿うこと。人物はシルエットのみ、文字・ロゴ・数字・血・死体は禁止)
{motif_pool}

# 出力形式
{JSON_SCHEMA_HINT}
""".strip()


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found in model output")
    return json.loads(match.group(0))


def _call_claude(prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    kwargs: dict[str, Any] = dict(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    if ENABLE_WEB_SEARCH:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]

    response = client.messages.create(**kwargs)
    text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return "\n".join(text_parts)


_PLACEHOLDER_TEXT = {
    "ja": {
        "heading": "プレースホルダ見出し",
        "body": "これはAPIキー未設定時のプレースホルダ本文です。",
        "narration": "プレースホルダナレーションです。",
        "title": "[PLACEHOLDER]",
        "description": "ANTHROPIC_API_KEY未設定のため生成されたプレースホルダです。本番投稿には使用しないでください。",
    },
    "en": {
        "heading": "Placeholder heading",
        "body": "This is placeholder body text (no ANTHROPIC_API_KEY set).",
        "narration": "This is placeholder narration.",
        "title": "[PLACEHOLDER]",
        "description": "Generated placeholder because ANTHROPIC_API_KEY is not set. Do not publish as-is.",
    },
    "ko": {
        "heading": "플레이스홀더 제목",
        "body": "API 키가 설정되지 않았을 때의 플레이스홀더 본문입니다.",
        "narration": "플레이스홀더 내레이션입니다.",
        "title": "[PLACEHOLDER]",
        "description": "ANTHROPIC_API_KEY가 없어 생성된 플레이스홀더입니다. 실제 게시에 사용하지 마세요.",
    },
    "zh": {
        "heading": "占位标题",
        "body": "这是未设置 ANTHROPIC_API_KEY 时的占位正文。",
        "narration": "这是占位旁白。",
        "title": "[PLACEHOLDER]",
        "description": "因未设置 ANTHROPIC_API_KEY 而生成的占位内容，请勿直接发布。",
    },
}


def _placeholder_script(slot: SlotConfig, theme_category: str, seq: int) -> dict[str, Any]:
    """API未接続時のフォールバック(パイプライン動作確認用のダミー台本)。

    デザイン仕様(ページ番号/エピソード番号を画面に出さない)を守るため、
    見出しにページ番号やseq番号は含めない。言語ごとに読める文字列を使う。
    """
    t = _PLACEHOLDER_TEXT.get(slot.language, _PLACEHOLDER_TEXT["en"])
    pages = []
    for i in range(1, 8):
        pages.append({
            "page": i,
            "heading": f"{t['heading']} {i}",
            "body": f"{t['body']} ({theme_category})",
            "narration": t["narration"],
            "image_motif": "old paper texture, deep shadow vignette, no people, no text",
        })
    return {
        "theme_category": theme_category,
        "theme": f"placeholder-theme-{seq:04d}",
        "conclusion": "placeholder-conclusion",
        "video_title": f"{t['title']} {slot.channel_label} #{seq:04d}",
        "description": t["description"],
        "tags": ["placeholder"],
        "hashtags": slot.hashtag_pool[:3] if slot.hashtag_pool else ["#shorts"],
        "finance_disclaimer_needed": slot.finance_disclaimer_required,
        "pages": pages,
        "sources": [],
    }


def generate(slot: SlotConfig) -> GeneratedScript:
    seq = dedupe.next_seq_number(slot.market)
    history_summaries = dedupe.recent_theme_summaries(slot.market)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        theme_category = _pick_theme_category(slot)
        data = _placeholder_script(slot, theme_category, seq)
        return GeneratedScript(raw=data, is_placeholder=True, warnings=["ANTHROPIC_API_KEY not set; using placeholder script"])

    avoid: set[str] = set()
    retry_note = ""
    last_data: dict[str, Any] | None = None
    for attempt in range(1, MAX_DEDUPE_RETRIES + 1):
        theme_category = _pick_theme_category(slot, avoid=avoid)
        prompt = _build_prompt(slot, theme_category, history_summaries, retry_note)
        text = _call_claude(prompt)
        data = _extract_json(text)
        last_data = data
        dup = dedupe.is_duplicate_theme(slot.market, data.get("theme", ""), data.get("conclusion", ""))
        if dup is None:
            return GeneratedScript(raw=data, is_placeholder=False)
        avoid.add(theme_category)
        retry_note = (
            f"\n# 注意: 前回生成したテーマ「{data.get('theme')}」は既出のテーマ"
            f"「{dup['theme']}」(#{dup['seq']:04d})と類似していると判定されました。"
            "切り口・具体例・結論を明確に変えて、別角度で書き直してください。"
        )

    assert last_data is not None
    return GeneratedScript(
        raw=last_data,
        is_placeholder=False,
        warnings=[f"could not avoid theme duplication after {MAX_DEDUPE_RETRIES} attempts; used last generated version as-is"],
    )
