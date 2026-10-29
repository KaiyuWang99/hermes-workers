"""Provider failures are read from the layer that saw the response.

Never from free text: the output tail also carries the Worker's own content — a
timestamp's milliseconds, a uuid, a hash, a pasted line number, a lint code,
documentation quoting ``401/402/403/429``.  Scanning it classified most real
worker logs as provider failures, which rotated the credential pool and reran
Cards on another model.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from workers_core import fallbacks
from workers_core.backends import claude_runner, opencode_runner

BUN = shutil.which("bun")
OPENCODE_HOOK = Path(fallbacks.__file__).resolve().parent.parent / "opencode" / "index.js"
BRIDGE_ENV = json.dumps(["bridge"])


def _result(**fields):
    return json.dumps({"type": "result", **fields})


NOISE = [
    '{"timestamp":"2026-09-11T11:21:10.402Z"}',           # 毫秒 .402Z ← 改前的真实触发
    '{"uuid":"570cbd4c-ebb8-4b3e-8556-bf68e0bdd429"}',     # uuid 尾 429
    '{"uuid":"5c74a0b6-af4b-4bd6-bd19-3dcc401afbba"}',     # uuid 里的 401
    "import x  # noqa: F401",                               # ruff 代码
    "402\t    (graph_dir / DESCRIPTIONS).unlink()",         # 粘贴的行号
    "@@ -403,6 +408,6 @@",                                  # diff hunk 行号
    "8400094fe401c2f140d7715075d04f0b850f1",                # sha256 片段
    '"estimated_tokens":403',
    "with a terminal 401/402/403/429 error",                # 文档正文
    "connection timeout ECONNREFUSED ENOTFOUND capacity",   # 传输关键词
]


# --- Claude Code: status from its own result event ---------------------------


def test_claude_reports_the_api_status_it_saw():
    tail = "\n".join(
        [
            '{"type":"assistant","message":{"id":"x"}}',
            _result(is_error=True, api_error_status=402),
        ]
    )
    assert claude_runner._reported_status(tail) == 402
    assert claude_runner._failure_status(tail) == 402


def test_successful_result_reports_no_status():
    tail = _result(is_error=False, subtype="success", api_error_status=None)
    assert claude_runner._reported_status(tail) is None
    assert claude_runner._failure_status(tail) is None


def test_error_without_an_api_status_is_not_a_provider_failure():
    """``max_turns`` 之类由 CLI 自己判错，不是 provider 失败。"""
    tail = _result(is_error=True, subtype="error_max_turns", api_error_status=None)
    assert claude_runner._reported_status(tail) is None
    assert claude_runner._failure_status(tail) is None


def test_killed_worker_without_a_result_event_reports_no_status():
    """被我们自己的 timeout 杀掉时没有 result 事件 —— 不 rotate、不 fallback。"""
    tail = json.dumps(
        {"type": "assistant", "message": {"id": "x", "usage": {"input_tokens": 206507}}}
    )
    assert claude_runner._reported_status(tail) is None
    assert claude_runner._failure_status(tail) is None


def test_only_credential_reasons_rotate():
    assert claude_runner._failure_status(_result(api_error_status=503)) is None
    assert claude_runner._failure_status(_result(api_error_status=402)) == 402


def test_region_result_never_rotates():
    region = {
        "type": "result",
        "is_error": True,
        "api_error_status": 403,
        "api_error_body": {
            "error": {
                "type": "RegionError",
                "message": "This model is not available in your country",
            }
        },
    }
    assert claude_runner._failure_status(json.dumps(region)) is None


@pytest.mark.parametrize("noise", NOISE)
def test_worker_content_never_invents_a_status(noise):
    tail = "\n".join(['{"type":"assistant","message":{"id":"x"}}', noise])
    assert claude_runner._reported_status(tail) is None, noise
    assert claude_runner._failure_status(tail) is None, noise


# --- 回归：文本猜测路径必须全部消失 -----------------------------------------


def test_no_free_text_classification_remains():
    source = open(fallbacks.__file__, encoding="utf-8").read()
    assert r"(?<!\d)(\d{3})(?!\d)" not in source
    assert "api error" not in source.lower()
    runner = open(claude_runner.__file__, encoding="utf-8").read()
    assert r"(?<!\d)(401|402|403|429)(?!\d)" not in runner
    assert "\nimport re\n" not in runner


# --- result 事件解析（唯一实现） ---------------------------------------------


def test_result_event_returns_the_last_one():
    tail = "\n".join([_result(session_id="first"), _result(session_id="second")])
    assert fallbacks.result_event(tail)["session_id"] == "second"


def test_result_event_ignores_other_events_and_garbage():
    assert fallbacks.result_event("no json here") == {}
    assert fallbacks.result_event(json.dumps({"type": "assistant"})) == {}
    assert claude_runner.result_event(_result(session_id="s"))["session_id"] == "s"


# --- OpenCode：状态与决策由 hook 写入 ----------------------------------------


def test_opencode_runner_prefers_the_attempt_status_file(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode_runner, "hermes_root", lambda: tmp_path / "unused")
    status = tmp_path / "run" / "status.json"
    status.parent.mkdir(parents=True)
    status.write_text(
        json.dumps(
            {
                "status_code": 403,
                "should_fallback": True,
                "reason": "upstream_blocked",
            }
        ),
        encoding="utf-8",
    )
    env = {"WORKERS_OPENCODE_STATUS": str(status)}

    assert opencode_runner._status_path("t_probe", env) == status
    assert opencode_runner._reported_status("t_probe", env) == 403
    assert opencode_runner._reported("t_probe", env)["should_fallback"] is True

    opencode_runner._clear_reported_status("t_probe", env)
    assert opencode_runner._reported_status("t_probe", env) is None


def test_opencode_runner_reads_the_status_env_of_its_own_process(monkeypatch, tmp_path):
    status = tmp_path / "status.json"
    status.write_text("402", encoding="utf-8")
    monkeypatch.setenv("WORKERS_OPENCODE_STATUS", str(status))

    assert opencode_runner._status_path("t_probe") == status
    assert opencode_runner._reported_status("t_probe") == 402


def test_opencode_runner_reads_and_clears_the_legacy_json_decision(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKERS_OPENCODE_STATUS", raising=False)
    monkeypatch.setattr(opencode_runner, "hermes_root", lambda: tmp_path)
    path = tmp_path / "state" / "workers-opencode-t_probe.status"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "status_code": 403,
                "should_fallback": True,
                "reason": "upstream_blocked",
            }
        ),
        encoding="utf-8",
    )

    assert opencode_runner._reported_status("t_probe") == 403
    assert opencode_runner._reported("t_probe")["should_fallback"] is True

    opencode_runner._clear_reported_status("t_probe")
    assert opencode_runner._reported_status("t_probe") is None


def test_opencode_runner_reads_a_legacy_integer_status(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKERS_OPENCODE_STATUS", raising=False)
    monkeypatch.setattr(opencode_runner, "hermes_root", lambda: tmp_path)
    path = tmp_path / "state" / "workers-opencode-t_probe.status"
    path.parent.mkdir(parents=True)
    path.write_text("402", encoding="utf-8")
    assert opencode_runner._reported_status("t_probe") == 402
    assert opencode_runner._reported("t_probe") == {"status_code": 402}


def test_opencode_runner_rejects_missing_or_garbage_status(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKERS_OPENCODE_STATUS", raising=False)
    monkeypatch.setattr(opencode_runner, "hermes_root", lambda: tmp_path)
    path = tmp_path / "state" / "workers-opencode-t_probe.status"
    path.parent.mkdir(parents=True)

    assert opencode_runner._reported_status("t_probe") is None       # 文件不存在
    path.write_text("not a status", encoding="utf-8")
    assert opencode_runner._reported_status("t_probe") is None
    assert opencode_runner._reported_status("") is None


def test_opencode_runner_does_not_walk_fallback_on_a_transient_5xx(monkeypatch):
    """5xx 交给 core 先在同一条路由上重试，runner 不再自行换模型。"""
    commands: list[list[str]] = []

    def fake_run_once(command, _env):
        commands.append(command)
        return 1, "503 upstream overloaded"

    monkeypatch.setattr(opencode_runner, "_run_once", fake_run_once)
    monkeypatch.setattr(
        opencode_runner,
        "_reported",
        lambda *_args, **_kwargs: {"status_code": 503},
    )
    payload = {
        "attempts": [
            {"command": ["opencode", "--model", "primary"]},
            {"command": ["opencode", "--model", "fallback"]},
        ]
    }

    assert opencode_runner.run_payload(payload) == 1
    assert commands == [["opencode", "--model", "primary"]]


@pytest.mark.skipif(BUN is None, reason="bun is required to run the OpenCode hook")
def test_opencode_hook_records_the_decision_the_runner_reads(monkeypatch, tmp_path):
    """JS 写、Python 读 —— 两端对同一份约定（跨语言契约）实测对齐。"""
    status = tmp_path / "run" / "status.json"
    script = tmp_path / "probe.mjs"
    script.write_text(
        "import {{ reportUpstreamStatus }} from {hook};\n"
        "reportUpstreamStatus(403, {{ should_fallback: true, reason: 'upstream_blocked' }});\n".format(
            hook=json.dumps(str(OPENCODE_HOOK))
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "HERMES_KANBAN_TASK": "t_probe"}
    env["WORKERS_OPENCODE_STATUS"] = str(status)
    done = subprocess.run(
        [BUN, "run", str(script)], env=env, capture_output=True, text=True, timeout=90
    )

    assert done.returncode == 0, done.stderr
    value = json.loads(status.read_text(encoding="utf-8"))
    assert value == {
        "status_code": 403,
        "should_fallback": True,
        "reason": "upstream_blocked",
    }

    assert opencode_runner._reported(
        "t_probe", {"WORKERS_OPENCODE_STATUS": str(status)}
    ) == {"status_code": 403, "should_fallback": True}


@pytest.mark.skipif(BUN is None, reason="bun is required to run the OpenCode hook")
def test_opencode_hook_writes_nothing_without_a_card_or_a_status_path(tmp_path):
    home = tmp_path / "hermes"
    status = tmp_path / "run" / "status.json"
    script = tmp_path / "probe.mjs"
    script.write_text(
        f"import {{ reportUpstreamStatus }} from {json.dumps(str(OPENCODE_HOOK))};\n"
        "reportUpstreamStatus(402);\n",
        encoding="utf-8",
    )
    no_card = {k: v for k, v in os.environ.items() if k != "HERMES_KANBAN_TASK"}
    no_card["HERMES_HOME"] = str(home)
    no_card["WORKERS_OPENCODE_STATUS"] = str(status)
    done = subprocess.run(
        [BUN, "run", str(script)], env=no_card, capture_output=True, text=True, timeout=90
    )

    assert done.returncode == 0, done.stderr
    assert not status.exists()

    # No configured target: the hook never guesses a state directory.
    no_path = {
        k: v
        for k, v in os.environ.items()
        if k not in {"WORKERS_OPENCODE_STATUS"}
    }
    no_path["HERMES_HOME"] = str(home)
    no_path["HERMES_KANBAN_TASK"] = "t_probe"
    done = subprocess.run(
        [BUN, "run", str(script)], env=no_path, capture_output=True, text=True, timeout=90
    )

    assert done.returncode == 0, done.stderr
    assert not (home / "state").exists()


@pytest.mark.skipif(BUN is None, reason="bun is required to run the OpenCode hook")
def test_opencode_hook_rotates_only_when_the_bridge_says_so(tmp_path):
    script = tmp_path / "probe.mjs"
    script.write_text(
        "const payloads = [];\n"
        "Bun.spawn = (args) => { let payload = {}; return {\n"
        "stdin: { write(value) { payload = JSON.parse(value); payloads.push({args, payload}) }, end() {} },\n"
        "stdout: new Blob([JSON.stringify({ok:true, rotate:true, key:'next-key', credential_id:'next', should_fallback:true, reason:'auth'})]).stream(),\n"
        "stderr: new Blob([]).stream(), exited: Promise.resolve(0) }; };\n"
        "const seen = [];\n"
        "globalThis.fetch = async (url, init) => { seen.push(init.headers.get('authorization'));\n"
        "  return new Response(JSON.stringify({error:{message:'Invalid API key'}}), {status:403}); };\n"
        f"const {{ default: plugin }} = await import({json.dumps(str(OPENCODE_HOOK))});\n"
        "await plugin.server();\n"
        "const response = await fetch('https://opencode.ai/zen/go/v1/chat/completions', {\n"
        "  method:'POST', body:JSON.stringify({model:'muse', messages:[]})});\n"
        "const body = await response.json();\n"
        "console.log(JSON.stringify({seen, status: response.status, payloads, body}));\n",
        encoding="utf-8",
    )
    status = tmp_path / "run" / "status.json"
    env = {
        **os.environ,
        "HERMES_KANBAN_TASK": "t_rotate",
        "WORKERS_OPENCODE_STATUS": str(status),
        "WORKERS_OPENCODE_PROVIDER": "opencode-go",
        "WORKERS_OPENCODE_API_KEY": "test-key",
        "WORKERS_OPENCODE_BRIDGE": BRIDGE_ENV,
    }
    done = subprocess.run(
        [BUN, "run", str(script)], env=env, capture_output=True, text=True, timeout=20
    )

    assert done.returncode == 0, done.stderr
    actual = json.loads(done.stdout)
    assert actual["seen"] == ["Bearer test-key", "Bearer next-key"]
    assert actual["status"] == 403
    assert actual["body"] == {"error": {"message": "Invalid API key"}}
    assert len(actual["payloads"]) == 2
    assert actual["payloads"][0]["args"][-1] == "handle"
    assert actual["payloads"][0]["payload"]["error_body"] == {"error": {"message": "Invalid API key"}}
    assert actual["payloads"][0]["payload"]["model"] == "muse"
    assert json.loads(status.read_text(encoding="utf-8")) == {
        "status_code": 403,
        "should_fallback": True,
        "reason": "auth",
    }


@pytest.mark.skipif(BUN is None, reason="bun is required to run the OpenCode hook")
def test_opencode_hook_does_not_rotate_a_region_block(tmp_path):
    script = tmp_path / "probe.mjs"
    script.write_text(
        "const payloads = [];\n"
        "Bun.spawn = (args) => { let payload = {}; return {\n"
        "stdin: { write(value) { payload = JSON.parse(value); payloads.push({args, payload}) }, end() {} },\n"
        "stdout: new Blob([JSON.stringify({ok:true, rotate:false, should_fallback:true, reason:'upstream_blocked'})]).stream(),\n"
        "stderr: new Blob([]).stream(), exited: Promise.resolve(0) }; };\n"
        "let calls = 0;\n"
        "globalThis.fetch = async () => { calls += 1; return new Response(\n"
        "  JSON.stringify({error:{type:'RegionError', message:'This model is not available in your country'}}), {status:403}); };\n"
        f"const {{ default: plugin }} = await import({json.dumps(str(OPENCODE_HOOK))});\n"
        "await plugin.server();\n"
        "const response = await fetch('https://opencode.ai/zen/go/v1/chat/completions', {\n"
        "  method:'POST', body:JSON.stringify({model:'muse', messages:[]})});\n"
        "console.log(JSON.stringify({calls, status: response.status, payloads, body: await response.json()}));\n",
        encoding="utf-8",
    )
    status = tmp_path / "run" / "status.json"
    env = {
        **os.environ,
        "HERMES_KANBAN_TASK": "t_region",
        "WORKERS_OPENCODE_STATUS": str(status),
        "WORKERS_OPENCODE_PROVIDER": "opencode-go",
        "WORKERS_OPENCODE_API_KEY": "test-key",
        "WORKERS_OPENCODE_BRIDGE": BRIDGE_ENV,
    }
    done = subprocess.run(
        [BUN, "run", str(script)], env=env, capture_output=True, text=True, timeout=20
    )

    assert done.returncode == 0, done.stderr
    actual = json.loads(done.stdout)
    assert actual["calls"] == 1
    assert actual["status"] == 403
    assert actual["body"]["error"]["type"] == "RegionError"
    assert json.loads(status.read_text(encoding="utf-8")) == {
        "status_code": 403,
        "should_fallback": True,
        "reason": "upstream_blocked",
    }


@pytest.mark.skipif(BUN is None, reason="bun is required for the OpenCode hook")
def test_opencode_selects_credentials_per_model_and_passes_model_to_bridge(tmp_path):
    hook = OPENCODE_HOOK
    script = tmp_path / "models.mjs"
    script.write_text(
        "const selected = [];\n"
        "Bun.spawn = (args) => { selected.push(args.slice(-2)); return {\n"
        "stdin:{end(){}}, stdout:new Blob([JSON.stringify({ok:true,key:'test-key'})]).stream(),\n"
        "stderr:new Blob([]).stream(), exited:Promise.resolve(0) }; };\n"
        "globalThis.fetch = async () => new Response('{}', {status:200});\n"
        f"const {{ default: plugin }} = await import({json.dumps(str(hook))});\n"
        "await plugin.server();\n"
        "for (const model of ['model-a','model-a','model-b']) {\n"
        "await fetch('https://opencode.ai/zen/go/v1/chat/completions', {\n"
        "method:'POST',body:JSON.stringify({model,messages:[]})}); }\n"
        "console.log(JSON.stringify(selected));\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "HERMES_HOME": str(tmp_path / "hermes"),
        "HERMES_KANBAN_TASK": "t_models",
        "WORKERS_OPENCODE_PROVIDER": "opencode-go",
        "WORKERS_OPENCODE_BRIDGE": BRIDGE_ENV,
    }
    env.pop("WORKERS_OPENCODE_API_KEY", None)
    done = subprocess.run(
        [BUN, "run", str(script)], env=env, capture_output=True, text=True, timeout=20
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [
        ["opencode-go", "model-a"],
        ["opencode-go", "model-b"],
    ]


# --- 会话 id 复用（"Session ID ... is already in use"） ----------------------


def test_session_released_resumes_the_claimed_id():
    command = ["claude", "--print", "--session-id", "abc", "prompt"]
    resumed = claude_runner._session_released(command)
    assert resumed[:-1] == ["claude", "--print", "--resume", "abc"]
    assert resumed[-1] != "prompt"


def test_only_the_first_cli_run_of_a_worker_claims_the_session_id(monkeypatch):
    seen = []

    def fake_run_routed(command, env, runtime, hook_settings=None, proxy=None):
        seen.append(list(command))
        return 1, ""

    monkeypatch.setattr(claude_runner, "_run_routed", fake_run_routed)
    monkeypatch.setattr(claude_runner, "_failure_status", lambda text: None)
    monkeypatch.setattr(claude_runner, "_session_claim_taken", False)

    runtime = {"provider": "opencode-go", "model": "m", "base_url": "u", "api_key": "k"}
    command = ["claude", "--print", "--session-id", "abc", "prompt"]

    claude_runner._run_candidate(command, runtime, {})   # 首次运行
    claude_runner._run_candidate(command, runtime, {})   # 重试 / fallback 候选

    assert seen[0] == command
    assert "--session-id" not in seen[1]
    assert seen[1][seen[1].index("--resume") + 1] == "abc"
