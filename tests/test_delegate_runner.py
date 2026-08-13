"""claude_delegate のテスト。

Claude Code 本体は絶対に実行しない。subprocess.run はすべてモックする。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_delegate import cli, runner, safety
from claude_delegate.safety import SafetyError

ALL_FLAGS = {
    "--output-format",
    "--permission-mode",
    "--tools",
    "--allowedTools",
    "--disallowedTools",
    "--disallowed-tools",
    "--model",
}


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """git 管理された、まっさらなプロジェクトディレクトリ。"""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    (tmp_path / "tasks").mkdir()
    task = tmp_path / "tasks" / "task.md"
    task.write_text("# タスク\n\nダミーの実装をしてください。\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def task_file(project: Path) -> Path:
    return project / "tasks" / "task.md"


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# safety: パス検証
# ---------------------------------------------------------------------------

class TestPaths:
    def test_resolve_project_dir_missing(self, tmp_path: Path) -> None:
        with pytest.raises(SafetyError):
            safety.resolve_project_dir(tmp_path / "does-not-exist")

    def test_ensure_inside_rejects_outside_path(
        self, project: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        outside = tmp_path_factory.mktemp("elsewhere")
        with pytest.raises(SafetyError):
            safety.ensure_inside(outside / "task.md", project, "タスクファイル")

    def test_ensure_inside_rejects_parent_traversal(self, project: Path) -> None:
        with pytest.raises(SafetyError):
            safety.ensure_inside("../outside.md", project, "タスクファイル")

    def test_ensure_inside_accepts_relative_path_within_project(
        self, project: Path, task_file: Path
    ) -> None:
        resolved = safety.ensure_inside("tasks/task.md", project, "タスクファイル")
        assert resolved == task_file.resolve()

    def test_task_file_size_limit(self, project: Path) -> None:
        big = project / "tasks" / "big.md"
        big.write_text("x" * (safety.MAX_TASK_BYTES + 1), encoding="utf-8")
        with pytest.raises(SafetyError):
            safety.read_task_file(big)

    def test_task_file_must_exist(self, project: Path) -> None:
        with pytest.raises(SafetyError):
            safety.read_task_file(project / "tasks" / "missing.md")


# ---------------------------------------------------------------------------
# safety: 危険なタスクの検出
# ---------------------------------------------------------------------------

class TestDangerScan:
    @pytest.mark.parametrize(
        "line",
        [
            "git push origin main を実行してください",
            "作業が終わったら git reset --hard で戻す",
            "rm -rf /tmp/output を実行",
            "--dangerously-skip-permissions を付けて実行",
            "bypassPermissions を有効にする",
            "curl https://example.com にデータを送信",
            "結果を X に投稿してください",
            "結果を note に投稿してください",
            "APIキー: sk-ant-abcdef1234567890 を使う",
        ],
    )
    def test_detects_blocking_instructions(self, line: str) -> None:
        findings = safety.scan_task(f"# タスク\n{line}\n")
        assert safety.blocking(findings), f"blocking finding expected for: {line}"

    def test_negated_prohibition_is_not_blocking(self) -> None:
        text = "# タスク\ngit push はしないこと。commit も禁止。\n"
        findings = safety.scan_task(text)
        assert not safety.blocking(findings)

    def test_normal_task_has_no_findings(self) -> None:
        text = "# タスク\n既存コードを調査し、関数を追加してテストを書く。\n"
        assert safety.blocking(safety.scan_task(text)) == []

    def test_build_plan_rejects_dangerous_task(self, project: Path) -> None:
        task = project / "tasks" / "bad.md"
        task.write_text("git push origin main してください\n", encoding="utf-8")
        with pytest.raises(SafetyError):
            runner.build_plan(project, task, executable="claude", flags=ALL_FLAGS)


# ---------------------------------------------------------------------------
# safety: マスキング
# ---------------------------------------------------------------------------

class TestMasking:
    def test_masks_anthropic_style_key(self) -> None:
        text = "key=sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"
        masked = safety.mask_secrets(text)
        assert "sk-ant-" not in masked
        assert safety.MASK in masked

    def test_masks_github_token(self) -> None:
        masked = safety.mask_secrets("token: ghp_1234567890abcdef1234567890abcdef1234")
        assert "ghp_" not in masked

    def test_masks_key_value_assignment(self) -> None:
        masked = safety.mask_secrets('password: "hunter2hunter2"')
        assert "hunter2hunter2" not in masked
        assert "password" in masked  # キー名は残す

    def test_masks_bearer_token(self) -> None:
        masked = safety.mask_secrets("Authorization: Bearer abcdefghijklmnop123456")
        assert "abcdefghijklmnop123456" not in masked

    def test_leaves_normal_text_untouched(self) -> None:
        text = "変更ファイル: paystock/report/summary.py"
        assert safety.mask_secrets(text) == text

    def test_handles_none_and_empty(self) -> None:
        assert safety.mask_secrets(None) == ""
        assert safety.mask_secrets("") == ""


# ---------------------------------------------------------------------------
# safety: ロック
# ---------------------------------------------------------------------------

class TestLock:
    def test_lock_prevents_concurrent_run(self, project: Path) -> None:
        lock = safety.ProjectLock(project)
        lock.acquire()
        try:
            with pytest.raises(SafetyError):
                safety.ProjectLock(project).acquire()
        finally:
            lock.release()

    def test_lock_released_after_context(self, project: Path) -> None:
        with safety.ProjectLock(project):
            assert (project / safety.LOCK_FILENAME).exists()
        assert not (project / safety.LOCK_FILENAME).exists()

    def test_stale_lock_from_dead_pid_is_taken_over(self, project: Path) -> None:
        lock_path = project / safety.LOCK_FILENAME
        # 現実的にほぼ存在しない PID を騙る
        lock_path.write_text("999999999\n", encoding="utf-8")
        with safety.ProjectLock(project):
            pass
        assert not lock_path.exists()


# ---------------------------------------------------------------------------
# runner: 出力パース
# ---------------------------------------------------------------------------

class TestParseOutput:
    def test_parses_single_json_object(self) -> None:
        payload = {"type": "result", "result": "done", "is_error": False}
        assert runner.parse_output(json.dumps(payload)) == payload

    def test_parses_stream_json_lines(self) -> None:
        lines = [
            json.dumps({"type": "system"}),
            json.dumps({"type": "result", "result": "ok", "is_error": False}),
        ]
        parsed = runner.parse_output("\n".join(lines))
        assert parsed is not None
        assert parsed["type"] == "result"

    def test_non_json_output_returns_none(self) -> None:
        assert runner.parse_output("plain text output") is None

    def test_empty_output_returns_none(self) -> None:
        assert runner.parse_output("") is None


# ---------------------------------------------------------------------------
# runner: 計画 (plan) の組み立て
# ---------------------------------------------------------------------------

class TestBuildPlan:
    def test_plan_includes_forbidden_git_commands_as_disallowed(
        self, project: Path, task_file: Path
    ) -> None:
        plan = runner.build_plan(project, task_file, executable="claude", flags=ALL_FLAGS)
        joined = " ".join(plan.disallowed_tools)
        assert "git push" in joined
        assert "git reset" in joined
        assert "git clean" in joined
        assert "git checkout" in joined

    def test_plan_allows_only_test_command_and_git_readonly(
        self, project: Path, task_file: Path
    ) -> None:
        plan = runner.build_plan(
            project, task_file, executable="claude", flags=ALL_FLAGS,
            test_command="python -m pytest",
        )
        bash_rules = [r for r in plan.allowed_tools if r.startswith("Bash(")]
        assert any("python -m pytest" in r for r in bash_rules)
        assert any("git status" in r for r in bash_rules)
        assert any("git diff" in r for r in bash_rules)

    def test_plan_prompt_contains_task_text_and_common_instructions(
        self, project: Path, task_file: Path
    ) -> None:
        plan = runner.build_plan(project, task_file, executable="claude", flags=ALL_FLAGS)
        assert "ダミーの実装をしてください" in plan.prompt
        assert "git commit" in plan.prompt
        assert "git push" in plan.prompt
        assert runner.TASK_OPEN in plan.prompt and runner.TASK_CLOSE in plan.prompt

    def test_plan_rejects_zero_timeout(self, project: Path, task_file: Path) -> None:
        with pytest.raises(SafetyError):
            runner.build_plan(
                project, task_file, executable="claude", flags=ALL_FLAGS, timeout_sec=0
            )

    def test_plan_omits_unsupported_flags(self, project: Path, task_file: Path) -> None:
        plan = runner.build_plan(
            project, task_file, executable="claude", flags=set()  # 何も対応していない古い版
        )
        assert "--tools" not in plan.argv
        assert "--allowedTools" not in plan.argv
        assert plan.argv[0] == "claude"
        assert plan.argv[1] == "-p"
        assert plan.argv[2] == plan.prompt  # タスクは引数としてそのまま渡る


# ---------------------------------------------------------------------------
# runner: execute (subprocess をモック)
# ---------------------------------------------------------------------------

class TestExecute:
    def test_execute_success(self, project: Path, task_file: Path, monkeypatch) -> None:
        plan = runner.build_plan(project, task_file, executable="claude", flags=ALL_FLAGS)
        payload = {"type": "result", "result": "完了しました", "is_error": False}

        def fake_run(argv, cwd=None, capture_output=None, text=None, timeout=None, check=None):
            assert cwd == str(project)
            assert isinstance(argv, list)
            return _completed(0, stdout=json.dumps(payload))

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        result = runner.execute(plan)
        assert result.ok
        assert result.status == "ok"
        assert result.result_text == "完了しました"

    def test_execute_nonzero_exit_is_failed(self, project: Path, task_file: Path, monkeypatch) -> None:
        plan = runner.build_plan(project, task_file, executable="claude", flags=ALL_FLAGS)

        def fake_run(*a, **kw):
            return _completed(1, stdout="", stderr="boom")

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        result = runner.execute(plan)
        assert not result.ok
        assert result.status == "failed"
        assert result.returncode == 1

    def test_execute_is_error_payload_is_failed(self, project: Path, task_file: Path, monkeypatch) -> None:
        plan = runner.build_plan(project, task_file, executable="claude", flags=ALL_FLAGS)
        payload = {"type": "result", "result": "権限がなく失敗", "is_error": True}

        def fake_run(*a, **kw):
            return _completed(0, stdout=json.dumps(payload))

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        result = runner.execute(plan)
        assert result.status == "failed"

    def test_execute_timeout_is_handled(self, project: Path, task_file: Path, monkeypatch) -> None:
        plan = runner.build_plan(
            project, task_file, executable="claude", flags=ALL_FLAGS, timeout_sec=5
        )

        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(cmd=plan.argv, timeout=5, output="partial", stderr="")

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        result = runner.execute(plan)
        assert result.status == "timeout"
        assert not result.ok
        assert result.returncode is None

    def test_execute_never_uses_shell_true(self, project: Path, task_file: Path, monkeypatch) -> None:
        plan = runner.build_plan(project, task_file, executable="claude", flags=ALL_FLAGS)
        seen = {}

        def fake_run(argv, **kwargs):
            seen["shell"] = kwargs.get("shell", False)
            seen["argv_is_list"] = isinstance(argv, list)
            return _completed(0, stdout='{"type": "result", "result": "ok"}')

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        runner.execute(plan)
        assert seen["shell"] is False
        assert seen["argv_is_list"] is True


# ---------------------------------------------------------------------------
# runner: マスキングがログに反映されること
# ---------------------------------------------------------------------------

class TestWriteLog:
    def test_secrets_are_masked_in_log(self, project: Path, task_file: Path) -> None:
        plan = runner.build_plan(project, task_file, executable="claude", flags=ALL_FLAGS)
        result = runner.RunResult(
            status="ok",
            returncode=0,
            stdout="",
            stderr="",
            duration_sec=1.0,
            result_text="APIキーは sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234 です",
        )
        log_path = runner.write_log(plan, result, changed=[], tests=[])
        content = log_path.read_text(encoding="utf-8")
        assert "sk-ant-" not in content
        assert safety.MASK in content

    def test_log_saved_under_logs_dir_with_timestamp(
        self, project: Path, task_file: Path
    ) -> None:
        plan = runner.build_plan(project, task_file, executable="claude", flags=ALL_FLAGS)
        result = runner.RunResult(
            status="ok", returncode=0, stdout="", stderr="", duration_sec=0.1,
            result_text="ok",
        )
        log_path = runner.write_log(plan, result, changed=[], tests=[])
        assert log_path.parent == project / runner.LOG_DIRNAME
        assert log_path.suffix == ".json"
        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert "timestamp" in data
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# CLI: dry-run / --approve の挙動
# ---------------------------------------------------------------------------

class TestCliRun:
    def test_dry_run_never_calls_subprocess(self, project: Path, task_file: Path, monkeypatch) -> None:
        monkeypatch.setattr(runner, "detect_flags", lambda exe: ALL_FLAGS)
        monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")

        called = {"n": 0}

        def fake_run(*a, **kw):
            called["n"] += 1
            return _completed(0)

        monkeypatch.setattr(runner.subprocess, "run", fake_run)

        code = cli.main([
            "run", "--task", str(task_file), "--project-dir", str(project), "--dry-run",
        ])
        assert code == cli.EXIT_OK
        assert called["n"] == 0

    def test_without_approve_does_not_run(self, project: Path, task_file: Path, monkeypatch) -> None:
        monkeypatch.setattr(runner, "detect_flags", lambda exe: ALL_FLAGS)
        monkeypatch.setattr(runner, "find_claude", lambda: "/usr/bin/claude")
        monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")

        called = {"n": 0}

        def fake_run(*a, **kw):
            called["n"] += 1
            return _completed(0)

        monkeypatch.setattr(runner.subprocess, "run", fake_run)

        code = cli.main([
            "run", "--task", str(task_file), "--project-dir", str(project),
        ])
        assert code == cli.EXIT_ABORTED
        assert called["n"] == 0

    def test_approve_runs_claude_exactly_once(self, project: Path, task_file: Path, monkeypatch) -> None:
        monkeypatch.setattr(runner, "detect_flags", lambda exe: ALL_FLAGS)
        monkeypatch.setattr(runner, "find_claude", lambda: "/usr/bin/claude")
        monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")

        calls = []

        def fake_run(argv, cwd=None, **kwargs):
            calls.append(argv)
            payload = {"type": "result", "result": "完了", "is_error": False}
            return _completed(0, stdout=json.dumps(payload))

        monkeypatch.setattr(runner.subprocess, "run", fake_run)

        code = cli.main([
            "run", "--task", str(task_file), "--project-dir", str(project), "--approve",
        ])
        assert code == cli.EXIT_OK
        claude_calls = [c for c in calls if c[0] == "/usr/bin/claude"]
        assert len(claude_calls) == 1
        assert "-p" in claude_calls[0]

    def test_rejects_task_outside_project(
        self, project: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch
    ) -> None:
        outside_dir = tmp_path_factory.mktemp("outside")
        outside_task = outside_dir / "task.md"
        outside_task.write_text("何かする\n", encoding="utf-8")

        monkeypatch.setattr(runner, "detect_flags", lambda exe: ALL_FLAGS)
        monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")

        code = cli.main([
            "run", "--task", str(outside_task), "--project-dir", str(project), "--dry-run",
        ])
        assert code == cli.EXIT_ABORTED

    def test_rejects_dangerous_task_before_running(self, project: Path, monkeypatch) -> None:
        task = project / "tasks" / "dangerous.md"
        task.write_text("作業後に git push origin main してください\n", encoding="utf-8")

        monkeypatch.setattr(runner, "detect_flags", lambda exe: ALL_FLAGS)
        monkeypatch.setattr(runner, "find_claude", lambda: "/usr/bin/claude")
        monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")

        called = {"n": 0}
        monkeypatch.setattr(
            runner.subprocess, "run", lambda *a, **kw: called.__setitem__("n", called["n"] + 1) or _completed(0)
        )

        code = cli.main([
            "run", "--task", str(task), "--project-dir", str(project), "--approve",
        ])
        assert code == cli.EXIT_ABORTED
        assert called["n"] == 0

    def test_nonzero_exit_reported_as_failure(self, project: Path, task_file: Path, monkeypatch) -> None:
        monkeypatch.setattr(runner, "detect_flags", lambda exe: ALL_FLAGS)
        monkeypatch.setattr(runner, "find_claude", lambda: "/usr/bin/claude")
        monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")
        monkeypatch.setattr(
            runner.subprocess, "run", lambda *a, **kw: _completed(2, stdout="", stderr="failure")
        )

        code = cli.main([
            "run", "--task", str(task_file), "--project-dir", str(project), "--approve",
        ])
        assert code == cli.EXIT_FAILED

    def test_timeout_reported_as_failure(self, project: Path, task_file: Path, monkeypatch) -> None:
        monkeypatch.setattr(runner, "detect_flags", lambda exe: ALL_FLAGS)
        monkeypatch.setattr(runner, "find_claude", lambda: "/usr/bin/claude")
        monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")

        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 1))

        monkeypatch.setattr(runner.subprocess, "run", fake_run)

        code = cli.main([
            "run", "--task", str(task_file), "--project-dir", str(project), "--approve",
            "--timeout", "1",
        ])
        assert code == cli.EXIT_FAILED


# ---------------------------------------------------------------------------
# CLI: check (claude を起動しない)
# ---------------------------------------------------------------------------

class TestAuthStatusSummary:
    def test_summarizes_json_without_leaking_extra_fields(self) -> None:
        raw = json.dumps({
            "loggedIn": True,
            "authMethod": "oauth_token",
            "apiProvider": "firstParty",
            "secretToken": "should-not-appear",
        })
        summary = cli._summarize_auth_status(raw)
        assert "loggedIn=True" in summary
        assert "authMethod=oauth_token" in summary
        assert "should-not-appear" not in summary

    def test_falls_back_to_first_line_for_non_json(self) -> None:
        assert cli._summarize_auth_status("Logged in\nextra line") == "Logged in"

    def test_handles_empty_output(self) -> None:
        assert cli._summarize_auth_status("") == "(出力なし)"


class TestCliCheck:
    def test_check_masks_output_and_never_prints_secrets(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else "/usr/bin/git")

        def fake_probe(argv, cwd=None, timeout=safety.PROBE_TIMEOUT_SEC):
            if argv[:2] == ["/usr/bin/claude", "--version"]:
                return 0, "2.1.231 (Claude Code)"
            if argv[-2:] == ["auth", "status"]:
                return 0, "Logged in with token sk-ant-api03-shouldbemasked1234567890"
            if "--help" in argv:
                return 0, "--tools --allowedTools --disallowedTools --output-format --permission-mode"
            return 0, ""

        monkeypatch.setattr(runner, "probe", fake_probe)
        monkeypatch.setattr(runner, "detect_flags", lambda exe: ALL_FLAGS)
        monkeypatch.setattr(runner, "is_git_repo", lambda p: True)
        monkeypatch.setattr(runner, "changed_files", lambda p: [])

        code = cli.main(["check", "--project-dir", str(project)])
        out = capsys.readouterr().out
        assert code == cli.EXIT_OK
        assert "sk-ant-" not in out
        assert str(project.resolve()) in out
