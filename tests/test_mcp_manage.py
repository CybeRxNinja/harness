"""MCP management: list both config scopes, delegate add, edit remove in place."""
import json
import os

os.environ.setdefault("HARNESS_MOCK", "1")


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def test_list_merges_project_and_global_with_project_winning(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness import mcp as MC
    _write(MC.config_file(tmp_path, True), {"mcp": {"servers": {
        "shared": {"type": "local", "command": ["global"]},
        "gonly": {"type": "remote", "url": "https://g.example/mcp",
                  "headers": {"Authorization": "Bearer x"}},
    }}})
    _write(MC.config_file(tmp_path, False), {"mcp": {"servers": {
        "shared": {"type": "local", "command": ["project"]},
        "ponly": {"type": "local", "command": ["echo"],
                  "environment": {"TOKEN": "s3cret"}},
    }}})
    rows = {r["name"]: r for r in MC.servers(tmp_path)}
    assert set(rows) == {"shared", "gonly", "ponly"}
    assert rows["shared"]["source"] == "project"
    assert rows["shared"]["command"] == ["project"]
    assert rows["gonly"]["headers"] == ["Authorization"]
    assert rows["ponly"]["environment"] == ["TOKEN"]
    assert "s3cret" not in json.dumps(rows), "list never returns secret values"


def test_remove_edits_only_the_mcp_block(tmp_path, monkeypatch):
    """Comments and unrelated keys keep their exact bytes; only mcp is rewritten."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness import mcp as MC
    path = MC.config_file(tmp_path, False)
    path.write_text('''{
  // keep this comment and the model key
  "model": "acme/x",
  "note": "braces } inside a string",
  "mcp": {
    "servers": {
      "alpha": {"type": "local", "command": ["a"]},
      "beta": {"type": "local", "command": ["b"]}
    }
  },
  "theme": {"name": "dark"}
}
''')
    assert MC.remove(tmp_path, "alpha") is True
    text = path.read_text()
    assert "// keep this comment" in text
    assert '"model": "acme/x"' in text and '"theme"' in text
    assert "braces } inside" in text
    assert "alpha" not in text and "beta" in text
    data = json.loads(MC._strip_jsonc(text))
    assert list(data["mcp"]["servers"]) == ["beta"]
    assert path.with_suffix(".json.bak").exists()


def test_remove_last_server_leaves_a_valid_empty_block(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness import mcp as MC
    path = MC.config_file(tmp_path, False)
    _write(path, {"mcp": {"servers": {"only": {"type": "local",
                                              "command": ["x"]}}}})
    assert MC.remove(tmp_path, "only") is True
    data = json.loads(MC._strip_jsonc(path.read_text()))
    assert data["mcp"]["servers"] == {}


def test_remove_missing_touches_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness import mcp as MC
    path = MC.config_file(tmp_path, False)
    _write(path, {"mcp": {"servers": {"alpha": {"type": "local",
                                               "command": ["a"]}}}})
    before = path.read_text()
    assert MC.remove(tmp_path, "nope") is False
    assert path.read_text() == before
    assert not path.with_suffix(".json.bak").exists()


def test_add_delegates_to_opencode_and_redacts_values(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness import mcp as MC
    calls = []

    def fake(argv, root, timeout=60):
        calls.append(argv)
        return {"ok": True, "code": 0,
                "out": "added gh (token=s3cret-value, header=Bearer abc123)"}

    monkeypatch.setattr(MC, "_run", fake)
    res = MC.add(tmp_path, "gh", command=["npx", "-y", "server-github"],
                 env=[("GITHUB_TOKEN", "s3cret-value")],
                 headers=[("Authorization", "Bearer abc123")])
    assert res["ok"] is True
    assert "s3cret-value" not in json.dumps(res)
    assert "Bearer abc123" not in json.dumps(res)
    argv = calls[0]
    assert argv[:3] == ["opencode", "mcp", "add"]
    assert "GITHUB_TOKEN=s3cret-value" in argv
    assert argv[-4:] == ["--", "npx", "-y", "server-github"]
    assert res["path"] == str(MC.config_file(tmp_path, False))


def test_add_remote_uses_url_and_global_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness import mcp as MC
    calls = []
    monkeypatch.setattr(MC, "_run",
                        lambda argv, root, timeout=60: calls.append(argv) or
                        {"ok": True, "code": 0, "out": "added"})
    assert MC.add(tmp_path, "remote", url="https://x.example/mcp",
                  headers=[("X-Api-Key", "secret")], global_=True)["ok"]
    argv = calls[0]
    assert "--global" in argv
    assert "--url" in argv and "https://x.example/mcp" in argv
    assert "--" not in argv, "a remote server has no command"
    assert str(MC.config_file(tmp_path, True)) == str(
        tmp_path / "cfg" / "opencode" / "opencode.json")


def test_add_validates_name_and_transport_without_running_anything(tmp_path, monkeypatch):
    from harness import mcp as MC
    monkeypatch.setattr(MC, "_run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("opencode must not be called")))
    assert MC.add(tmp_path, "bad name", command=["x"])["code"] == 2
    assert MC.add(tmp_path, "ok", command=["x"], url="https://x")["code"] == 2
    assert MC.add(tmp_path, "ok")["code"] == 2
    assert MC.add(tmp_path, "ok", command=["x"], env=[("BAD KEY", "v")])["code"] == 2


def test_cli_mcp_lists_and_removes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness import mcp as MC
    _write(MC.config_file(tmp_path, False),
           {"mcp": {"servers": {"alpha": {"type": "local", "command": ["a"]}}}})
    from harness.cli import main
    assert main(["--root", str(tmp_path), "mcp", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["name"] == "alpha"
    assert main(["--root", str(tmp_path), "mcp", "remove", "alpha",
                 "--no-reload"]) == 0
    assert "removed alpha" in capsys.readouterr().out
    assert main(["--root", str(tmp_path), "mcp", "remove", "alpha",
                 "--no-reload"]) == 1


def test_cli_mcp_add_passes_parsed_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from harness import mcp as MC
    seen = []
    monkeypatch.setattr(MC, "_run",
                        lambda argv, root, timeout=60: seen.append(argv) or
                        {"ok": True, "code": 0, "out": "added gh"})
    from harness.cli import main
    assert main(["--root", str(tmp_path), "mcp", "add", "gh",
                 "--command", "npx -y server-github",
                 "--env", "TOKEN=abc", "--no-reload"]) == 0
    assert "added gh" in capsys.readouterr().out
    assert seen[0][-4:] == ["--", "npx", "-y", "server-github"]
