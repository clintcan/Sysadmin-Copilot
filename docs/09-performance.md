# Chapter 9 — Performance Tuning

Sysadmin Copilot's REPL can feel slower than quick LLM tests that use the same libraries. This chapter explains why, shows how to measure your setup, and covers the knobs that make the biggest difference.

---

## Why the REPL is slower than a bare LLM call

The libraries are the same, but the workload the model sees is not:

1. **Tool schema overhead.** Every `@tool`-decorated function's name, docstring, and JSON argument schema is serialized and sent with **every** request. With 30–40 tools loaded, that's thousands of tokens added to each prompt.
2. **Conversation history.** The REPL keeps previous turns in context across the loop — each question ships progressively more history.
3. **ReAct round-trips.** A single user question usually produces 2–4 LLM calls (reason → pick tool → interpret tool output → maybe another tool). One REPL turn is not one LLM call.
4. **Tool execution wall time.** `journalctl`, `ss`, `dig`, etc. take real subprocess time (often hundreds of milliseconds each).
5. **Auto-sized context window (Ollama).** When the tool count rises, `agent.py` grows `num_ctx` to fit. A larger context allocates a bigger KV cache, which makes **every token** slower — not just long prompts.
6. **Streaming + terminal rendering.** Token streaming with raw-mode ANSI handling adds latency batch-mode calls don't see.
7. **Cold-start model load.** Ollama unloads idle models after 5 minutes; the first query after an idle period pays the re-load cost.

Most of these compound each other. The dominant cost on a typical Ollama-backed install is #1 + #5 (tool count drives context size drives KV-cache size drives per-token latency).

---

## Measuring your setup

Run this snippet to see your real tool-token footprint:

```bash
python -c "
from tools import ALL_TOOLS
import json
try:
    import tiktoken
    enc = tiktoken.get_encoding('cl100k_base')
    real = sum(len(enc.encode(t.name + (t.description or '') + json.dumps(t.args))) for t in ALL_TOOLS)
    print(f'Tools loaded: {len(ALL_TOOLS)}')
    print(f'Tokens per tool (avg): {real // len(ALL_TOOLS)}')
    print(f'Total tool tokens: {real:,}')
except ImportError:
    print(f'Tools loaded: {len(ALL_TOOLS)} (install tiktoken for exact token counts)')
"
```

Sample output on a typical install with no threat-intel API keys set:

```
Tools loaded: 40
Tokens per tool (avg): 85
Total tool tokens: 3,423
```

And to see what Ollama will actually allocate, launch the REPL and read the banner:

```
Context window: 16,788 tokens (model max: 32,768, KV cache: 2.6 GB, history: ~4,000 tokens)
```

The `KV cache` line is the memory Ollama allocates up-front. Cutting `num_ctx` in half cuts this in half.

---

## Tuning knobs, ranked by impact

### 1. Cap `num_ctx` manually (Ollama only) — biggest win

The auto-sizer in `agent.py` targets `tool_tokens + system + 8K history + output`. For routine sysadmin Q&A you don't need 8K of history room. Override it:

```bash
OLLAMA_NUM_CTX=8192 python agent.py
```

Rough impact: halving `num_ctx` halves the KV cache and roughly halves prefill time, which dominates latency on tool-heavy ReAct turns. Going from a default ~20K window to 8K typically yields **2–2.5× faster turns**.

If tools don't fit, the agent will print a warning at startup. For a 40-tool install, 8K is tight but workable; 10–12K is comfortable.

### 2. Drop unused plugins

Each plugin you remove (or move to `tools_extra/_unused/`) saves ~85 tokens shipped per turn and ~100 tokens of allocated context budget (per the `_TOKENS_PER_TOOL` constant in `agent.py`). Skipping 6–8 unused plugins typically removes another ~10% of context pressure.

Two zero-code ways to shrink the plugin set:

- **Leave API keys unset.** Plugins with `REQUIRED_ENV` (threat intel, breach lookup) self-skip when their keys aren't in the environment.
- **Underscore-rename.** `mv tools_extra/foo.py tools_extra/_foo.py` — the loader skips any file or directory starting with `_`.

### 3. Switch to a smaller model

On CPU-only hardware, the model is the largest single cost. For routine sysadmin queries, 2–4B parameter models are often enough:

```bash
OLLAMA_MODEL=qwen3.5:2b OLLAMA_NUM_CTX=8192 python agent.py
```

Typical speedup from `llama3.1:8b` → a 2B model: **3–5×** on a tool-heavy prompt. Quality of tool selection degrades noticeably below ~3B for complex multi-step tasks; test with queries that matter to you.

### 4. Cap `MAX_OUTPUT_TOKENS`

Sysadmin answers rarely need 4K tokens of response — most are "here's what the command showed; the disk is 78% full." Lowering the cap both shortens generation and shrinks `target_ctx` (the formula reserves output tokens up-front):

```bash
MAX_OUTPUT_TOKENS=2048 python agent.py
```

Provider defaults: Ollama 4096, OpenAI 16384, Anthropic 8192 (`agent.py:84`).

### 5. GPU offload (Ollama)

If you have a GPU but it's not being used, force offload:

```bash
OLLAMA_NUM_GPU=999 ollama serve
```

Check what's actually on GPU:

```bash
ollama ps
```

The `PROCESSOR` column shows `100% GPU`, `30%/70% CPU/GPU`, or `100% CPU`. If the model doesn't fit entirely on GPU, a smaller model or smaller `num_ctx` lets more of it offload.

### 6. Keep the model warm

Ollama unloads idle models after 5 minutes, so the first question after a coffee break pays a full model reload. Set:

```bash
export OLLAMA_KEEP_ALIVE=24h
```

(in your shell, the service account's `.env`, or `/etc/systemd/system/ollama.service.d/override.conf` if you run Ollama via systemd).

### 7. Use `raw` mode

Inside the REPL, type `raw` to toggle raw output mode. In raw mode the agent shows you the tool's output directly and skips the summarization/interpretation LLM pass. That cuts one full LLM round-trip per question — best for "just show me the data" queries where you don't need a written summary.

Type `raw` again to toggle back.

### 8. Shorten tool docstrings

Every word in a tool's docstring goes into every prompt. The shipped plugins use verbose docstrings for clarity; if you're token-constrained on a small model, trimming them to 1–2 sentences can save hundreds of tokens across the tool list. Keep enough that the LLM still picks the right tool for ambiguous questions.

### 9. Swap to a cloud provider for heavy prompts

When the prompt is large (many tools, long history), cloud inference often beats local CPU inference even after network round-trip:

```bash
LLM_PROVIDER=anthropic ANTHROPIC_MODEL=claude-haiku-4-5-20251001 python agent.py
# or
LLM_PROVIDER=openai OPENAI_MODEL=gpt-4o-mini python agent.py
```

Haiku and `gpt-4o-mini` are fast and inexpensive for sysadmin tool-calling workloads. Use the mental model: small-prompt work stays local; tool-heavy work goes cloud.

---

## Combining knobs — recommended starting points

**Local Ollama, CPU-only, getting-started profile:**

```bash
OLLAMA_MODEL=qwen3.5:2b OLLAMA_NUM_CTX=8192 MAX_OUTPUT_TOKENS=2048 \
    OLLAMA_KEEP_ALIVE=24h python agent.py
```

**Local Ollama, GPU available:**

```bash
OLLAMA_MODEL=llama3.1:8b OLLAMA_NUM_CTX=12288 \
    OLLAMA_KEEP_ALIVE=24h python agent.py
```

**Cloud inference, maximum quality:**

```bash
LLM_PROVIDER=anthropic ANTHROPIC_MODEL=claude-sonnet-4-20250514 python agent.py
```

---

## Using these env vars with the `sysadmin-copilot` wrapper

The wrapper at `/usr/local/bin/sysadmin-copilot` drops your shell env because it runs under `sudo -u sysadmin-copilot`. Your `OLLAMA_NUM_CTX=...` in front of `sysadmin-copilot` will not reach `agent.py`.

Set performance env vars permanently in the service account's `.env`:

```bash
sudo bash -c 'cat >> /opt/sysadmin-copilot/.env' <<'EOF'
OLLAMA_NUM_CTX=8192
MAX_OUTPUT_TOKENS=2048
OLLAMA_KEEP_ALIVE=24h
EOF
```

Then run `sysadmin-copilot` normally.

---

## What to do when things are still slow

1. Re-measure tool count and real tool tokens with the snippet above. If the count has crept up (new plugins, extra keys set), consider re-trimming.
2. Check `ollama ps` to confirm the model is actually on GPU if you expected it to be.
3. Watch `journalctl`/`free`/`htop` during a query — if a tool is taking the time, not the LLM, the fix is tool-side (smaller query, a different tool, a narrower log window).
4. Increase the `_TOKENS_PER_TOOL` constant in `agent.py` if you see "tools may not work" warnings at startup — but only as much as needed; every token you grant here is KV cache.

---

[← Back to Table of Contents](README.md)
