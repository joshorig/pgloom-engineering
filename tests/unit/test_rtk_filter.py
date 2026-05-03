from __future__ import annotations

from pgloom.harness.subprocess import SubprocessResult

from pgloom_engineering.rtk import FilterPolicy, filter_subprocess_result, should_filter


def test_rtk_passthrough_when_disabled() -> None:
    result = _result(stdout="BUILD FAILED\nerror: nope\n")

    filtered = filter_subprocess_result(result, policy=FilterPolicy(enabled=False))

    assert filtered.filter_method == "passthrough"
    assert filtered.filtered_stdout == result.stdout
    assert filtered.tokens_saved == 0


def test_rtk_policy_passthrough_command() -> None:
    result = _result(argv=["git", "status"], stdout="large status")

    assert not should_filter(result, FilterPolicy(passthrough_commands=["git"]))


def test_rtk_falls_back_when_binary_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("pgloom_engineering.rtk.filter.shutil.which", lambda name: None)
    result = _result(stdout=":compileJava\n" * 200)

    filtered = filter_subprocess_result(result, policy=FilterPolicy())

    assert filtered.filter_method == "rtk_unavailable"
    assert filtered.filtered_stdout == result.stdout
    assert filtered.tokens_saved == 0


def test_rtk_truncates_to_token_budget_when_configured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("pgloom_engineering.rtk.filter.shutil.which", lambda name: "/usr/bin/rtk")

    def fake_run_bounded(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return _result(stdout=("line\n" * 200), argv=["rtk"], exit_code=0)

    monkeypatch.setattr("pgloom_engineering.rtk.filter.run_bounded", fake_run_bounded)

    filtered = filter_subprocess_result(
        _result(stdout="line\n" * 200),
        policy=FilterPolicy(max_tokens_after=20),
    )

    assert filtered.filter_method == "rtk"
    assert filtered.tokens_after <= 20


def _result(
    *,
    argv: list[str] | None = None,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 1,
) -> SubprocessResult:
    return SubprocessResult(
        argv=argv or ["./gradlew", "test"],
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.1,
        timed_out=False,
        killed=False,
    )
