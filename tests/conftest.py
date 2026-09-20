"""Suite hermeticity: provider keys for keyless CI runners.

Router candidate/rank paths depend on env keys; without them CI (keyless)
resolves differently than a dev machine. We inject DUMMY keys only when
absent so local runs with real keys (and tests that set their own) are
unaffected. Live network is still never touched: tests run under
HARNESS_MOCK=1 (see CI env + per-module guards).
"""
import os

_DUMMY_KEYS = {
    "OPENROUTER_API_KEY": "sk-or-v1-test-dummy",
    "OPENAI_API_KEY": "sk-test-dummy",
    "ANTHROPIC_API_KEY": "sk-ant-test-dummy",
    "GOOGLE_API_KEY": "AIza-test-dummy",
    "GROQ_API_KEY": "gsk-test-dummy",
    "CEREBRAS_API_KEY": "csk-test-dummy",
    "NVIDIA_API_KEY": "nvapi-test-dummy",
    "KILO_API_KEY": "kilo-test-dummy",
    "OPENCODE_API_KEY": "opencode-test-dummy",
}

for _k, _v in _DUMMY_KEYS.items():
    os.environ.setdefault(_k, _v)


def pytest_configure():
    for k, v in _DUMMY_KEYS.items():
        os.environ.setdefault(k, v)
