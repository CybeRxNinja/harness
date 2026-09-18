# Gateway
`harness serve --port 8787` binds 127.0.0.1 (use `--expose` only with token). Token in `~/.harness/token` (600). Endpoints: POST /api/chat (SSE ?stream=1), GET /api/sessions|/api/agents|/api/tasks|/health, POST /api/spawn|/api/stop, GET|POST /v1/models|/v1/chat/completions. Auth Bearer except /health. Redacted logs, no CORS, session lock, reattach via session_id.
