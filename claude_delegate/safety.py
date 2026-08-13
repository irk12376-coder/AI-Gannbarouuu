"""委託実行の安全弁。

ここに置くのは「Claude Code を起動する前に止めるための判定」だけ。

  - 危険な指示の検出          scan_task()
  - プロジェクト外パスの拒否  resolve_project_dir() / ensure_inside()
  - 認証情報のマスキング      mask_secrets()
  - 同時実行の防止            ProjectLock

方針は fail-closed。判断に迷う入力は通さずに失敗させる。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

#: タスクファイルの上限 (これを超えたら読み込まない)
MAX_TASK_BYTES = 64 * 1024

#: Claude Code の実行時間上限の既定値 (秒)
DEFAULT_TIMEOUT_SEC = 900

#: `claude --version` などの補助コマンドの上限 (秒)
PROBE_TIMEOUT_SEC = 60

#: 同時実行を防ぐロックファイル名 (プロジェクト直下に作る)
LOCK_FILENAME = ".claude_delegate.lock"

#: Claude Code に使わせる組み込みツール。ここに無いツールは渡さない。
ALLOWED_TOOLS = ("Read", "Glob", "Grep", "Edit", "Write", "Bash")

#: Bash で許可するコマンド接頭辞 (テストコマンドは実行時に追加する)
ALLOWED_BASH_PREFIXES = ("git status", "git diff")

#: 明示的に拒否する操作。--tools で絞ったうえで、さらに二重に塞ぐ。
DISALLOWED_TOOLS = (
    "Bash(git push:*)",
    "Bash(git commit:*)",
    "Bash(git reset:*)",
    "Bash(git clean:*)",
    "Bash(git checkout:*)",
    "Bash(git restore:*)",
    "Bash(git remote:*)",
    "Bash(curl:*)",
    "Bash(wget:*)",
    "Bash(ssh:*)",
    "Bash(scp:*)",
    "Bash(rm:*)",
    "Bash(sudo:*)",
    "WebFetch",
    "WebSearch",
)


class SafetyError(Exception):
    """安全側の判定で実行を止めるときに投げる。"""


# ---------------------------------------------------------------------------
# 危険な指示の検出
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    """タスクファイルから見つかった気になる記述 1 件。"""

    line_no: int
    line: str
    rule: str
    blocking: bool

    def format(self) -> str:
        mark = "拒否" if self.blocking else "参考"
        return f"[{mark}] {self.line_no} 行目: {self.rule}\n        {self.line.strip()}"


# (正規表現, 説明) の並び。説明はそのまま利用者に見せる。
_DANGER_RULES: tuple[tuple[str, str], ...] = (
    (r"git\s+push", "Git push は禁止されている"),
    (r"git\s+reset\s+--hard", "git reset --hard は変更を破棄する"),
    (r"git\s+clean", "git clean は未追跡ファイルを消す"),
    (r"git\s+checkout\s+(--|\.|\S+\s+--)", "git checkout による変更の破棄は禁止"),
    (r"git\s+restore", "git restore による変更の破棄は禁止"),
    (r"rm\s+-[a-z]*r[a-z]*f?\s", "再帰削除はファイルの大量削除にあたる"),
    (r"find\s+.*-delete", "find -delete はファイルの大量削除にあたる"),
    (r"\bsudo\b", "sudo は使わせない"),
    (r"\bchmod\s+777", "権限の全開放は禁止"),
    (r"\b(curl|wget|nc|netcat)\b", "外部送信・外部取得コマンドは禁止"),
    (r"--dangerously-skip-permissions", "権限スキップは禁止"),
    (r"bypassPermissions", "権限バイパスは禁止"),
    (r"shell\s*=\s*True", "shell=True は禁止"),
    (r"\b(api[\s_-]?key|apikey|access[\s_-]?token|client[\s_-]?secret)\b",
     "API キー・トークンの要求/記載は禁止"),
    (r"(APIキー|API\s*キー|アクセストークン|シークレットキー)",
     "API キー・トークンの要求/記載は禁止"),
    (r"\bsk-(ant-)?[A-Za-z0-9_\-]{8,}", "API キーらしき文字列の記載は禁止"),
    (r"(パスワード|password|passwd)", "パスワードの要求/記載は禁止"),
    (r"\bOAuth\b", "OAuth 認証情報の取り扱いは禁止"),
    (r"(youtube|ようつべ|YouTube)", "YouTube への投稿・連携は禁止"),
    (r"(twitter\.com|x\.com|ツイート|tweet|ポスト投稿)", "X への投稿は禁止"),
    (r"(note\.com|note に投稿|note へ投稿)", "note への投稿は禁止"),
    (r"(投稿し|アップロードし|公開し|送信し)て", "外部への投稿・送信は禁止"),
    (r"(?<![\w/.])(/etc/|/usr/|/var/|~/|C:\\\\)", "プロジェクト外のパスは変更できない"),
)

# 「〜しない」「禁止」のような打ち消し文脈。ヒットしても止めない。
_NEGATION = re.compile(
    r"(しない|しないで|するな|禁止|不可|避け|控え|ません|ないこと|なし|"
    r"\bnever\b|\bdon't\b|\bdo not\b|\bmust not\b|\bno\b)",
    re.IGNORECASE,
)


def scan_task(text: str) -> list[Finding]:
    """タスク本文を 1 行ずつ見て、危険な指示を洗い出す。

    「git push はしないこと」のような打ち消し文脈は blocking=False にする
    (禁止事項をタスクに書けなくなると使い物にならないため)。
    """
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        negated = bool(_NEGATION.search(line))
        for pattern, rule in _DANGER_RULES:
            if re.search(pattern, line, re.IGNORECASE):
                findings.append(
                    Finding(line_no=line_no, line=line, rule=rule, blocking=not negated)
                )
    return findings


def blocking(findings: list[Finding]) -> list[Finding]:
    """実行を止めるべき指摘だけを返す。"""
    return [f for f in findings if f.blocking]


# ---------------------------------------------------------------------------
# 認証情報のマスキング
# ---------------------------------------------------------------------------

MASK = "***MASKED***"

# キー名つきの代入形式。キー名は残し、値だけ潰す。
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|apikey|token|secret|password|passwd|pwd|credential|"
    r"authorization|auth[_-]?token|client[_-]?secret|session[_-]?key)\b"
    r"(\s*[:=]\s*)(\"|')?([^\s\"',]{4,})(\"|')?"
)

# 値そのものが機密と分かる形式。
_SECRET_VALUES: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\bey[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


def mask_secrets(text: str | None) -> str:
    """API キーらしい文字列を潰す。ログに書く前に必ず通す。"""
    if not text:
        return "" if text is None else text
    masked = text
    for pattern in _SECRET_VALUES:
        masked = pattern.sub(MASK, masked)
    masked = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", masked)
    return masked


# ---------------------------------------------------------------------------
# パスの検証
# ---------------------------------------------------------------------------

def resolve_project_dir(raw: str | os.PathLike[str]) -> Path:
    """作業対象を絶対パスに解決する。存在しなければ失敗。"""
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise SafetyError(f"プロジェクトディレクトリが存在しません: {path}")
    if not path.is_dir():
        raise SafetyError(f"プロジェクトディレクトリがディレクトリではありません: {path}")
    return path


def ensure_inside(raw: str | os.PathLike[str], project_dir: Path, label: str) -> Path:
    """`raw` を解決し、プロジェクト内に収まっていることを確認する。

    シンボリックリンク経由の脱出も resolve() 後に判定するので弾ける。
    """
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = project_dir / path
    path = path.resolve()
    if path != project_dir and project_dir not in path.parents:
        raise SafetyError(
            f"{label}がプロジェクトの外を指しています: {path}\n"
            f"  プロジェクト: {project_dir}"
        )
    return path


def read_task_file(path: Path) -> str:
    """タスクファイルを上限つきで読む。"""
    if not path.is_file():
        raise SafetyError(f"タスクファイルが見つかりません: {path}")
    size = path.stat().st_size
    if size > MAX_TASK_BYTES:
        raise SafetyError(
            f"タスクファイルが大きすぎます: {size} bytes "
            f"(上限 {MAX_TASK_BYTES} bytes)"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SafetyError(f"タスクファイルを UTF-8 として読めません: {path}") from exc
    if not text.strip():
        raise SafetyError(f"タスクファイルが空です: {path}")
    return text


# ---------------------------------------------------------------------------
# 同時実行の防止
# ---------------------------------------------------------------------------

class ProjectLock:
    """プロジェクト単位のロック。二重起動を防ぐ。

    with 文で使う。既にロックがある場合は SafetyError。
    プロセスが死んでいると分かる場合だけ、古いロックを引き取る。
    """

    def __init__(self, project_dir: Path) -> None:
        self.path = project_dir / LOCK_FILENAME
        self._acquired = False

    def __enter__(self) -> "ProjectLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    def acquire(self) -> None:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if self._is_stale():
                self.path.unlink(missing_ok=True)
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            else:
                raise SafetyError(
                    "他の委託実行が動いています (ロックあり): "
                    f"{self.path}\n"
                    "  終わっているはずなら、このファイルを削除してから再実行してください。"
                ) from None
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")
        self._acquired = True

    def release(self) -> None:
        if self._acquired:
            self.path.unlink(missing_ok=True)
            self._acquired = False

    def _is_stale(self) -> bool:
        """ロックの持ち主が既に居ないなら True。"""
        try:
            pid = int(self.path.read_text(encoding="utf-8").strip().splitlines()[0])
        except (OSError, ValueError, IndexError):
            return False
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return False
