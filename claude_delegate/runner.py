"""Claude Code CLI を起動して実装を委託する本体。

守っていること:

  - subprocess はリスト引数で呼ぶ (shell=True は使わない)
  - cwd は resolve 済みのプロジェクト内に固定する
  - タスク本文はコマンド文字列に連結せず、引数として渡す
  - 実行時間に上限を設ける
  - stdout / stderr / 終了コードをマスキングしたうえで logs/ に保存する
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import safety
from .safety import SafetyError

#: 既定のテストコマンド (このリポジトリは pytest)
DEFAULT_TEST_COMMAND = "python -m pytest"

#: ログの保存先 (プロジェクト直下)
LOG_DIRNAME = "logs"

#: タスク本文を包む目印。指示と本文の境目をはっきりさせる。
TASK_OPEN = "<<<TASK>>>"
TASK_CLOSE = "<<<END_TASK>>>"

#: すべての委託タスクに自動で付ける共通指示。
COMMON_INSTRUCTIONS = """\
あなたは Codex から実装を委託されたエージェントです。以下を必ず守ってください。

## 進め方
1. 最初に既存コードを調査してから着手する (いきなり書き始めない)
2. 既存機能を壊さない。既存のテストが通る状態を保つ
3. タスクで指定された範囲の外は変更しない
4. 不明な仕様を勝手に補完しない。判断できない点は残課題として報告する
5. 実装後に必ずテストを実行する: `{test_command}`
6. テストやビルドでエラーが出たら原因を直し、直せない場合は理由を報告する

## 禁止事項
- 外部への投稿・送信 (curl / wget / SNS / YouTube / X / note など) は一切しない
- git commit と git push はしない。変更は作業ツリーに残すだけにする
- git reset --hard / git clean / git checkout による変更の破棄はしない
- ファイルの大量削除、プロジェクト外のファイル変更はしない
- OAuth・API キー・パスワードを要求したり、出力したり、ファイルへ保存したりしない
- 許可されていない操作が必要になったら、迂回せずに「失敗」として報告して終了する

## 完了時に必ず報告すること
- 変更したファイルの一覧
- 実行したテストとその結果 (件数・成否)
- 残課題、判断に迷った点

## 委託タスク ({task_name})
{task_open}
{task_text}
{task_close}
"""


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Plan:
    """これから実行する内容。dry-run はこれを表示するだけで終わる。"""

    project_dir: Path
    task_path: Path
    task_text: str
    prompt: str
    argv: list[str]
    allowed_tools: list[str]
    disallowed_tools: list[str]
    timeout_sec: int
    test_command: str
    findings: list[safety.Finding] = field(default_factory=list)

    @property
    def executable(self) -> str:
        return self.argv[0]

    def safe_argv(self) -> list[str]:
        """表示・ログ用の argv。長いプロンプトは要約に置き換える。"""
        out: list[str] = []
        for arg in self.argv:
            if arg == self.prompt:
                out.append(f"<プロンプト {len(self.prompt)} 文字>")
            else:
                out.append(arg)
        return out


@dataclass
class RunResult:
    """Claude Code の実行結果。"""

    status: str  # "ok" | "failed" | "timeout"
    returncode: int | None
    stdout: str
    stderr: str
    duration_sec: float
    result_text: str = ""
    payload: dict | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# ---------------------------------------------------------------------------
# CLI の探索と機能検出
# ---------------------------------------------------------------------------

def find_claude() -> str:
    """claude コマンドの実体を返す。無ければ SafetyError。"""
    path = shutil.which("claude")
    if not path:
        raise SafetyError(
            "claude コマンドが見つかりません。Claude Code CLI を入れて "
            "`claude auth login` でログインしてください。"
        )
    return path


def probe(argv: list[str], cwd: Path | None = None,
          timeout: int = safety.PROBE_TIMEOUT_SEC) -> tuple[int | None, str]:
    """補助コマンドを 1 つ実行して (終了コード, 出力) を返す。

    失敗しても例外にしない。check の表示に使うだけなので、
    出力は必ずマスキングしてから返す。
    """
    try:
        completed = subprocess.run(  # noqa: S603 - 固定 argv、shell は使わない
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return None, f"コマンドが見つかりません: {argv[0]}"
    except subprocess.TimeoutExpired:
        return None, f"タイムアウトしました ({timeout} 秒): {' '.join(argv)}"
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, safety.mask_secrets(output).strip()


def detect_flags(executable: str) -> set[str]:
    """インストール済みの claude が受け付けるオプションを調べる。

    `--tools` などが無い版でも動くよう、実際の --help から判定する。
    """
    _code, help_text = probe([executable, "--help"])
    flags = set(re.findall(r"--[a-zA-Z][a-zA-Z0-9-]*", help_text or ""))
    return flags


# ---------------------------------------------------------------------------
# 計画の組み立て
# ---------------------------------------------------------------------------

def build_prompt(task_text: str, task_name: str, test_command: str) -> str:
    """共通指示 + タスク本文のプロンプトを作る。"""
    return COMMON_INSTRUCTIONS.format(
        test_command=test_command,
        task_name=task_name,
        task_open=TASK_OPEN,
        task_text=task_text.strip(),
        task_close=TASK_CLOSE,
    )


def allowed_tool_rules(test_command: str) -> list[str]:
    """Claude Code に許可するツールの一覧を作る。"""
    rules = [t for t in safety.ALLOWED_TOOLS if t != "Bash"]
    bash_prefixes = [test_command.strip(), *safety.ALLOWED_BASH_PREFIXES]
    rules.extend(f"Bash({prefix}:*)" for prefix in bash_prefixes if prefix)
    return rules


def build_plan(
    project_dir: Path,
    task_path: Path,
    *,
    test_command: str = DEFAULT_TEST_COMMAND,
    timeout_sec: int = safety.DEFAULT_TIMEOUT_SEC,
    model: str | None = None,
    executable: str | None = None,
    flags: set[str] | None = None,
) -> Plan:
    """タスクを読み、危険判定をしたうえで実行計画を組み立てる。

    ここでは Claude Code を起動しない。dry-run と run が同じ計画を共有する。
    """
    task_text = safety.read_task_file(task_path)
    findings = safety.scan_task(task_text)
    blockers = safety.blocking(findings)
    if blockers:
        detail = "\n".join(f.format() for f in blockers)
        raise SafetyError("タスクに危険な指示が含まれています。実行を中止します。\n" + detail)

    if timeout_sec <= 0:
        raise SafetyError("タイムアウトは 1 秒以上にしてください。")

    executable = executable or find_claude()
    flags = detect_flags(executable) if flags is None else flags

    task_name = task_path.name
    prompt = build_prompt(task_text, task_name, test_command)
    allowed = allowed_tool_rules(test_command)
    disallowed = list(safety.DISALLOWED_TOOLS)

    argv: list[str] = [executable, "-p", prompt]
    if "--output-format" in flags:
        argv += ["--output-format", "json"]
    if "--permission-mode" in flags:
        argv += ["--permission-mode", "dontAsk"]
    if "--tools" in flags:
        argv += ["--tools", ",".join(safety.ALLOWED_TOOLS)]
    if "--allowedTools" in flags or "--allowed-tools" in flags:
        argv += ["--allowedTools", ",".join(allowed)]
    if "--disallowedTools" in flags or "--disallowed-tools" in flags:
        argv += ["--disallowedTools", ",".join(disallowed)]
    if model and "--model" in flags:
        argv += ["--model", model]

    return Plan(
        project_dir=project_dir,
        task_path=task_path,
        task_text=task_text,
        prompt=prompt,
        argv=argv,
        allowed_tools=allowed,
        disallowed_tools=disallowed,
        timeout_sec=timeout_sec,
        test_command=test_command,
        findings=findings,
    )


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------

def execute(plan: Plan) -> RunResult:
    """Claude Code を起動する。呼び出し側は必ず --approve を確認済みであること。"""
    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - リスト引数、shell=True は使わない
            plan.argv,
            cwd=str(plan.project_dir),
            capture_output=True,
            text=True,
            timeout=plan.timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            status="timeout",
            returncode=None,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr),
            duration_sec=time.monotonic() - started,
            result_text=f"タイムアウト ({plan.timeout_sec} 秒) で打ち切りました。",
        )
    except FileNotFoundError as exc:
        raise SafetyError(f"claude コマンドを実行できません: {exc}") from exc

    duration = time.monotonic() - started
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    payload = parse_output(stdout)
    result_text = ""
    if payload:
        result_text = str(payload.get("result") or "")
    if not result_text:
        result_text = stdout.strip()

    failed = completed.returncode != 0 or bool(payload and payload.get("is_error"))
    return RunResult(
        status="failed" if failed else "ok",
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_sec=duration,
        result_text=result_text,
        payload=payload,
    )


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def parse_output(stdout: str) -> dict | None:
    """`--output-format json` の出力を読む。

    単一オブジェクトでも、stream-json のような行区切りでも拾えるようにする。
    読めなければ None を返し、呼び出し側は生テキストを使う。
    """
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = None
    if isinstance(loaded, dict):
        return loaded
    if isinstance(loaded, list):
        for item in reversed(loaded):
            if isinstance(item, dict) and item.get("type") == "result":
                return item
        return next((i for i in reversed(loaded) if isinstance(i, dict)), None)

    # 行区切り JSON (stream-json) を後ろから探す
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("type") == "result":
            return item
    return None


# ---------------------------------------------------------------------------
# 実行後の確認
# ---------------------------------------------------------------------------

def is_git_repo(project_dir: Path) -> bool:
    code, _out = probe(["git", "rev-parse", "--is-inside-work-tree"], cwd=project_dir)
    return code == 0


def changed_files(project_dir: Path) -> list[str]:
    """`git status --porcelain` の行をそのまま返す。"""
    code, out = probe(["git", "status", "--porcelain"], cwd=project_dir)
    if code != 0 or not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def diff_stat(project_dir: Path) -> str:
    _code, out = probe(["git", "diff", "--stat"], cwd=project_dir)
    return out or ""


_TEST_LINE = re.compile(
    r"(\d+\s+(passed|failed|error|errors|skipped)|"
    r"=+\s.*(passed|failed|error).*\s=+|"
    r"\b(FAILED|ERROR|OK|PASS|FAIL)\b|"
    r"(テスト|test).*(成功|失敗|通過|パス))",
    re.IGNORECASE,
)


def summarize_tests(text: str) -> list[str]:
    """Claude Code の報告からテスト結果らしい行を抜き出す。"""
    lines = [line.strip() for line in (text or "").splitlines()]
    hits = [line for line in lines if line and _TEST_LINE.search(line)]
    # 同じ行が繰り返されることがあるので順序を保って重複を除く
    seen: set[str] = set()
    unique: list[str] = []
    for line in hits:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return unique[:20]


# ---------------------------------------------------------------------------
# ログ
# ---------------------------------------------------------------------------

def log_dir(project_dir: Path) -> Path:
    path = project_dir / LOG_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_log(
    plan: Plan,
    result: RunResult,
    *,
    changed: list[str],
    tests: list[str],
    now: datetime | None = None,
) -> Path:
    """実行結果を logs/ に日時付きで保存する。

    環境変数は一切記録しない。stdout / stderr / プロンプトは
    mask_secrets() を通してから書く。
    """
    now = now or datetime.now()
    stem = plan.task_path.stem or "task"
    path = log_dir(plan.project_dir) / f"{now:%Y%m%d-%H%M%S}_{stem}.json"
    record = {
        "timestamp": now.isoformat(timespec="seconds"),
        "project_dir": str(plan.project_dir),
        "task_file": str(plan.task_path),
        "test_command": plan.test_command,
        "timeout_sec": plan.timeout_sec,
        "argv": [safety.mask_secrets(a) for a in plan.safe_argv()],
        "allowed_tools": plan.allowed_tools,
        "disallowed_tools": plan.disallowed_tools,
        "prompt": safety.mask_secrets(plan.prompt),
        "status": result.status,
        "returncode": result.returncode,
        "duration_sec": round(result.duration_sec, 2),
        "result_text": safety.mask_secrets(result.result_text),
        "stdout": safety.mask_secrets(result.stdout),
        "stderr": safety.mask_secrets(result.stderr),
        "changed_files": changed,
        "test_summary": [safety.mask_secrets(t) for t in tests],
    }
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path
