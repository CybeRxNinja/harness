import os
os.environ["HARNESS_MOCK"] = "1"

def _serve():
    import threading
    from http.server import ThreadingHTTPServer
    from pathlib import Path
    from harness.serve import Handler, ensure_token
    Handler.root = Path(".").resolve()
    Handler.token = ensure_token()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_endpoints():
    import json, urllib.request
    srv = _serve()
    port = srv.server_address[1]
    tok = open(os.path.expanduser("~/.harness/token")).read().strip()

    def get(path):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     headers={"Authorization": f"Bearer {tok}"})
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    assert get("/health")["ok"] is True
    assert isinstance(get("/api/skills"), list)
    assert any(s["name"] == "using-agent-skills" for s in get("/api/skills"))
    assert isinstance(get("/api/memory?q=test"), list)
    assert isinstance(get("/api/agents"), list)
    assert get("/v1/models")["object"] == "list"
    srv.shutdown()


def test_usage_tracking():
    import json, urllib.request
    srv = _serve()
    port = srv.server_address[1]
    tok = open(os.path.expanduser("~/.harness/token")).read().strip()

    def post(path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {tok}",
                                              "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    def get(path):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     headers={"Authorization": f"Bearer {tok}"})
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    post("/api/chat", {"session_id": "usetest", "mode": "ask", "message": "hello world usage"})
    u = get("/api/usage?session=usetest")
    assert u["calls"] >= 1 and u["input"] > 0 and u["total"] == u["input"] + u["output"]
    assert isinstance(u["by_model"], list)
    srv.shutdown()


def test_sse_text_extract():
    from harness.serve import _sse_chars, _msg_chars, _model_ctx
    assert _msg_chars([{"role": "user", "content": "hello"}]) == 5
    assert _msg_chars([{"role": "user", "content": [{"type": "text", "text": "abc"}]}]) == 3
    assert _sse_chars(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n') == 2
    assert _model_ctx("auto-fastest") == 200000
    assert _model_ctx("deepseek-v3.2") == 128000
    assert _model_ctx("tag:reasoning") == 200000
    assert _model_ctx("nope-unknown-xyz") == 0


def test_tasks_global_and_usage_global():
    import json, urllib.request
    srv = _serve()
    port = srv.server_address[1]
    tok = open(os.path.expanduser("~/.harness/token")).read().strip()

    def get(path):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     headers={"Authorization": f"Bearer {tok}"})
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    # global views work without any session id (what the TUI panel uses)
    tasks = get("/api/tasks")
    assert "todos" in tasks and "inbox" in tasks
    u = get("/api/usage")
    assert u["total"] == u["input"] + u["output"] and "by_model" in u
    srv.shutdown()
