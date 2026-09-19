# Routing (builtin relay)
Strings: `auto-fastest | <group> | tag:<name> | tag:<name>+min_ctx:<32k> | provider/model`.
Groups carry quality/ctx/tags (see `harness/router.py:GROUPS`). Candidates filtered by keys present, ban list, min_ctx (relaxed, never hard-fail), then QoS-ranked `quality * target/(target+ewma) * 0.5^errors`, 60s cooldown after 2 fails, 2 retries + failover. `/v1/models` lists groups+tags. `did-you-mean` on miss.

## Free tier (best-of-free first)
Groups free-reasoner/free-coder/free-general map per-provider to $0 endpoints
(OpenRouter :free, Groq/Cerebras free tiers, Ollama local). deep/ultrabrain try
tag:free before paid. IDs churn; verified live on demand, failover covers 404/429.

## Role policy (audited)
Planning/heavy (deep, ultrabrain, plan-consultant/reviewer, security-auditor):
tag:reasoning first (best quality, e.g. openrouter/deepseek-v3.2), tag:free fallback.
Bulk/cheap (quick, explore, writing): auto-fastest / tag:general.
Zen models (Muse Spark free) are TUI-side only: no stable third-party endpoint
exists, so the Python router cannot spend them. Pick them with ctrl+p in the TUI.
