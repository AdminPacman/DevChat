# Local Instance — Pac's Arcade setup

ChatDev 2.0 (DevAll) running fully local against Ollama. No cloud keys needed.

## Config

`.env` (gitignored, create from `.env.example`):

```
BASE_URL=http://localhost:11434/v1
API_KEY=ollama
MODEL_NAME=qwen3:4b
```

All agent nodes in `yaml_instance/` were converted from hardcoded `name: gpt-4o`
(and variants) to `name: ${MODEL_NAME}`, resolved from `.env` at load time by
`entity/config_loader.py`. Per-run override: pass `"variables": {"MODEL_NAME": "..."}`
to `POST /api/workflow/run`.

## Model notes (Ollama)

| Model | Tools? | Use for |
|-------|--------|---------|
| `qwen3:4b` | yes (native function calling) | default — tool workflows work end-to-end |
| `gemma3:4b` | no (`completion` only) | plain chat-only agent chains |
| `gemma3:1b` | no | fast/cheap chat-only |

qwen3 is a thinking model — it spends tokens on reasoning before tool calls, so
keep `max_tokens` on agent nodes at 2000+, not the 200 some demos shipped with.
`gemini-*` image-gen workflows still need real Gemini keys; left untouched.

## Run

```bash
export PATH="$HOME/.local/bin:$PATH"   # uv lives here
make dev                                # backend :6400 + frontend :5173
# or separately:
uv run python server_main.py --port 6400 --reload
cd frontend && VITE_API_BASE_URL=http://localhost:6400 npm run dev
```

Web console: http://localhost:5173
After editing YAMLs: `make sync` (uploads to DB), `make validate-yamls`.

## Smoke tests (both verified working)

```bash
# chat-only chain (Bug Analyst -> Fix Planner, arcade bug triage themed)
curl -s -X POST http://localhost:6400/api/workflow/run -H "Content-Type: application/json" \
  -d '{"yaml_file":"smoke_test_local.yaml","task_prompt":"<bug report text>","session_name":"triage-1"}'

# tool-calling chain
curl -s -X POST http://localhost:6400/api/workflow/run -H "Content-Type: application/json" \
  -d '{"yaml_file":"demo_function_call.yaml","task_prompt":"Beijing","session_name":"tools-1"}'
```
