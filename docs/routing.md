# Routing (builtin relay)
Strings: `auto-fastest | <group> | tag:<name> | tag:<name>+min_ctx:<32k> | provider/model`.
Groups carry quality/ctx/tags (see `harness/router.py:GROUPS`). Candidates filtered by keys present, ban list, min_ctx (relaxed, never hard-fail), then QoS-ranked `quality * target/(target+ewma) * 0.5^errors`, 60s cooldown after 2 fails, 2 retries + failover. `/v1/models` lists groups+tags. `did-you-mean` on miss.

## Free tier (best-of-free first)
Groups free-reasoner/free-coder/free-general map per-provider to $0 endpoints
(OpenRouter :free, Groq/Cerebras free tiers, Ollama local). deep/ultrabrain try
tag:free before paid. IDs churn; verified live on demand, failover covers 404/429.
