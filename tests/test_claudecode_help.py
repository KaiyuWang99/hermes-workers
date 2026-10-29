from __future__ import annotations

from workers_core.backends import claudecode_cli as cli


HELP = """
  --effort <level>                      Effort level for the current session
                                        (low, medium, high)
  --permission-mode <mode>              Permission mode to use for the session
                                        (choices: "auto", "manual")
  --setting-sources <sources>           Comma-separated list of setting sources
                                        to load (user, project, local).
  --output-format <format>              Output format (only works with --print):
                                        "text" (default), "json", or "stream-json"
                                        (realtime streaming output)
  --input-format <format>               Input format (only works with --print):
                                        "text" (default), or "stream-json"
  --include-partial-messages            Include partial message chunks as they
                                        arrive (only works with --print and
                                        --output-format=stream-json)
  --strict-mcp-config                   Only use MCP servers from --mcp-config
  --disable-slash-commands              Disable all skills
"""


def test_choices_ignore_continuation_lines():
    sections = cli._help_sections(HELP)

    assert "stream-json" in sections["output-format"]
    assert cli._choices_from_section(sections["effort"]) == (
        "low",
        "medium",
        "high",
    )
    assert cli._choices_from_section(sections["permission-mode"]) == (
        "auto",
        "manual",
    )
    assert cli._choices_from_section(sections["setting-sources"]) == (
        "user",
        "project",
        "local",
    )


def test_capabilities_expose_features_choices_and_version(monkeypatch):
    cli._probe_cli_flags.cache_clear()
    cli._help_info.cache_clear()
    monkeypatch.setattr(cli, "_resolve_binary", lambda value: "/fake/claude")
    monkeypatch.setattr(cli, "_binary_stamp", lambda binary: 0)
    monkeypatch.setattr(cli, "_probe_help_text", lambda binary, stamp: HELP)
    monkeypatch.setattr(cli, "_probe_cli_version", lambda binary, stamp: "9.9.9")

    caps = cli.capabilities("/fake/claude")

    assert caps["version"] == "9.9.9"
    assert set(caps["features"]) == {
        "strict_mcp_config",
        "settings_sources",
        "native_skills",
        "permission_mode",
        "effort",
    }
    assert list(caps["choices"]["setting_sources"]) == ["user", "project", "local"]
    assert list(caps["choices"]["effort"]) == ["low", "medium", "high"]
    assert "constraints" not in caps


def test_capabilities_are_unknown_when_help_probe_fails(monkeypatch):
    cli._probe_cli_flags.cache_clear()
    cli._help_info.cache_clear()
    monkeypatch.setattr(cli, "_resolve_binary", lambda value: "/fake/claude")
    monkeypatch.setattr(cli, "_binary_stamp", lambda binary: 0)
    monkeypatch.setattr(cli, "_probe_help_text", lambda binary, stamp: "")
    monkeypatch.setattr(cli, "_probe_cli_version", lambda binary, stamp: "")

    caps = cli.capabilities("/fake/claude")

    assert caps["features"] == {}
    assert caps["choices"] == {}
    assert "constraints" not in caps
