import os
os.environ["HARNESS_MOCK"] = "1"

def test_sse_framing():
    """Strict clients (AI SDK) require blank-line SSE separators. Regression test."""
    from harness.router import chat_stream
    body = b"".join(chat_stream([{"role": "user", "content": "hi"}], model="auto-fastest", cfg={}))
    assert body.count(b"\n\n") >= 2
    assert body.rstrip().endswith(b"data: [DONE]")
