# Routing (builtin relay + self-managed catalog)
Model strings: `auto-fastest | <group> | tag:<name> | tag:<name>+min_ctx:<32k>`
| `provider/model` pin. Groups (`kimi-k2.5`, `free-reasoner`, …) map per
provider to real IDs (verified against live catalogs); failover covers churn.

QoS ranking: `quality × target/(target+ewma) × 0.5^errors`, 60s cooldown after
2 fails, Retry-After honored on 429s (capped 300s), up to 5 attempts, tool
requests skip models flagged `no_tools`. Free-tier OpenRouter keys are
auto-detected (`/v1/auth/key`) and restricted to `:free` endpoints (paid 402s).

## Providers beyond keys
`opencode` (Zen, `OPENCODE_API_KEY`, base `https://opencode.ai/zen/v1`) and
`kilo` (`KILO_API_KEY`, base `https://api.kilo.ai/api/gateway`, key optional —
anonymous `:free` works keyless) are first-class providers with per-provider
model-ID maps. Attempt ordering is provider-diverse (round-robin by provider,
score order within) so one dead provider can't eat the whole attempt budget.

402 handling is precise: 402 on a `:free` route stains the whole provider
(10 min cooldown); 402 on paid routes cools only that model (30 min). Retry-After
on 429s is honored (capped 300s). Every failure returns the full attempt trail.

## Free tier (best-of-free first)
Groups `free-giant/reasoner/glm/coder/coder-qwen/swift/general` map to $0
endpoints (OpenRouter `:free`, Kilo gateway incl. anonymous, Groq/Cerebras
tiers, Ollama local). `deep`/`ultrabrain` try `tag:reasoning` first (best),
`tag:free` as fallback. IDs churn; verified live on demand, failover covers
404/429. Add keys (`GROQ_API_KEY`, `KILO_API_KEY`, …) to widen the pool.

## Self-managed catalog (the router owns selection/verification/updates)
Discovery: provider catalogs re-fetched automatically (daily) + on demand
(`harness router refresh [--probe]`). Each id is classified: param count parsed
from the id (`550b-a55b` = 550B total/55B active), benchmark estimate by family,
first-seen recency. Unknown families are quarantined, never silently routed:
`harness router catalog` shows verdicts, `harness router approve <provider/model>`
allows one, `router.new_model_policy` (`ask` default) can flip to `allow`/`deny`.
Live QoS continuously verifies and re-ranks everything actually served.

## Role policy (audited)
Planning/heavy (deep, ultrabrain, plan-consultant/reviewer, security-auditor):
`tag:reasoning` first (best quality), `tag:free` fallback. Bulk/cheap (quick,
explore, writing): `auto-fastest`. Zen models (Muse Spark free) are TUI-side
only: no stable third-party endpoint exists, so pick them with `ctrl+p`.
