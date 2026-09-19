import os
os.environ["HARNESS_MOCK"] = "1"

def test_recent_routes():
    from harness.router import _record, recent_routes
    _record("openrouter", "deepseek-v3.2", 1234, True, "auto-fastest")
    routes = recent_routes()
    assert routes and routes[-1]["requested"] == "auto-fastest"
    assert routes[-1]["provider"] == "openrouter"
