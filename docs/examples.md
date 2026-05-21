# Usage Examples

Practical recipes for common ContextSpy workflows.

---

## Measure how much of your context window tool results consume

You're using an agent with many tools (web search, file reads, code execution) and want
to understand how much of the context budget they eat.

```bash
contextspy start
```

In VS Code settings.json:
```json
{ "http.proxy": "http://127.0.0.1:8888", "http.proxyStrictSSL": false }
```

Run your agent task. In the dashboard → **Requests**, click any request and open the
**Overview** tab. The block map shows each tool result as a proportional rectangle.
Hover to see exact token counts. Category breakdown in the sidebar shows the
`tool_results` slice as a percentage of total input tokens.

---

## Compare context usage across two coding sessions

You want to see whether a refactored system prompt uses fewer tokens.

```bash
# Before refactor
contextspy session start "before-refactor"
# ... run your agent task ...
contextspy session end

# After refactor
contextspy session start "after-refactor"
# ... run the same task ...
contextspy session end
```

Go to **Sessions** in the dashboard. Select each session to compare total token usage,
category breakdowns, and per-request details side by side.

---

## Profile a Python script using the OpenAI SDK

```python
# your_script.py
import os
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:8888"

from openai import OpenAI
client = OpenAI()

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Summarise this codebase: ..."}],
)
```

```bash
contextspy start --no-browser
python your_script.py
```

Open http://127.0.0.1:5173 → **Requests** to inspect the captured call.

---

## Monitor a local Ollama model

```bash
# config.toml (auto-created at ~/.contextspy/config.toml)
[[reverse_targets]]
name        = "ollama"
listen_port = 8890
target_url  = "http://127.0.0.1:11434"
provider    = "openai"
```

```bash
contextspy start-local
```

Point your client at the ContextSpy port instead of Ollama directly:

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8890/v1", api_key="ollama")
response = client.chat.completions.create(
    model="llama3",
    messages=[{"role": "user", "content": "Hello"}],
)
```

---

## Find which requests are hitting the context limit

In the **Requests** table, the `Tokens in` column shows total input tokens per request.
Sort descending to find the largest ones. Click a request to see the **Overview** tab —
blocks near or at the model's context window will be obvious from the sheer size of the
block map.

Use **Sessions** to track token growth over a multi-turn conversation: each successive
request will show the growing `conversation_history` slice.

---

## Capture Claude Code usage

```bash
# Set env vars before launching your terminal / IDE
export HTTPS_PROXY=http://127.0.0.1:8888
export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem

contextspy start
# Now launch VS Code or Claude CLI in the same shell
code .
```

Or use the printed snippet:
```bash
contextspy setup-claude
```

---

## Export or query captured data directly

All data lives in a standard SQLite database:

```bash
sqlite3 ~/.contextspy/contextspy.db

# Total tokens by model
SELECT model, SUM(tokens_total_input) as total_in
FROM requests
GROUP BY model
ORDER BY total_in DESC;

# Largest requests
SELECT timestamp, provider, tokens_total_input
FROM requests
ORDER BY tokens_total_input DESC
LIMIT 10;
```

Or use the built-in report:
```bash
contextspy report
```
