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
