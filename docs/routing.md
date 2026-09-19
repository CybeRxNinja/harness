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

## Self-managed catalog (the router owns selection/verification/updates)
Discovery: providers catalogs re-fetched automatically (daily) + on demand
(harness router refresh [--probe]). Each id is classified: param count parsed
from the id (550b-a55b = 550B total/55B active), benchmark estimate by family,
first-seen recency. Unknown families are quarantined, never silently routed:
harness router catalog shows verdicts, harness router approve <provider/model>
allows one, router.new_model_policy allow|ask|deny sets the default (ask).
Live QoS (latency/errors/429 backoff/tool-support flags) continuously verifies
and re-ranks everything actually served.
