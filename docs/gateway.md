# Gateway
`harness serve --port 8787` binds 127.0.0.1 (`--expose` binds LAN — only with
a token, and only on networks you trust). Singleton per port with a live
sentinel (`~/.harness/serve.json` root/port/pid): launching in another
directory restarts it for that root (sessions persist per directory).
`harness serve --stop`. Requests + errors logged to the project serve log.

Auth: loopback clients (127.0.0.1, ::1) are trusted without a token — required
because opencode v2 does not forward provider `apiKey` headers for custom npm
providers. Any local process already runs as your user, so this grants nothing
new on a single-user machine. Non-loopback binds (`--expose`) still require
`Authorization: Bearer <~/.harness/token>` (600).

Endpoints: `POST /api/chat` (SSE `?stream=1`, session/mode/message),
`GET /api/sessions|/api/agents|/api/tasks|/api/skills|/api/memory|/api/route|
/api/usage|/health`, `POST /api/spawn|/api/stop`,
`GET|POST /v1/models|/v1/chat/completions` (OpenAI-compat incl. SSE-fixed
framing, `reasoningEffort` passthrough, usage accounting). Auth Bearer
except /health. Redacted logs, no CORS, reattach via session_id.
