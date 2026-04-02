# Tests & Evals

## Quick Start

```bash
pip install pytest
python -m pytest tests/ -v
```

## Test Suites

### `test_safety.py` — Safety Layer Evals

Pure Python tests for the blocked-pattern detection, normalization, service allowlist, and injection resistance. No LLM or network access needed — runs in under a second.

```bash
python -m pytest tests/test_safety.py -v
```

**122 tests** across 8 groups:

| Group | Tests | What it validates |
|-------|-------|-------------------|
| `TestDirectBlocks` | 28 | Every blocked pattern category catches its obvious use case (rm, dd, shutdown, fork bombs, etc.) |
| `TestNormalizationCatches` | 18 | Evasion via case variation (`RM`), whitespace tricks (`rm\t-rf`), and quote wrapping (`"rm"`) |
| `TestAllowedCommands` | 20 | Legitimate sysadmin commands (ls, df, journalctl, systemctl status, etc.) pass through |
| `TestServiceAllowlist` | 4 | ALLOWED_SERVICES membership, EXTRA_SERVICES env var parsing, SafetyLayer allowlist check |
| `TestArgumentHandling` | 6 | Positional args, kwargs, non-string values ignored, empty args |
| `TestInjectionScenarios` | 15 | Compound attack strings: semicolon injection, `&&` chaining, `$()` subshells, base64 pipes |
| `TestKnownGaps` | 8 | Documented evasion techniques that bypass pattern matching (marked `xfail`). These are accepted gaps — OS permissions (Layer 3) are the real security boundary. |
| `TestPatternCompleteness` | 29 | Every entry in BLOCKED_PATTERNS actually blocks something (guards against typos or dead patterns) |

The 6 `xfail` tests document known gaps: `python3 -c`, `ruby -e`, `mv` overwrite, `curl | python3`, variable expansion, and `cp` overwrite. These pass through the pattern matcher by design — see `docs/05-safety-layer.md` for the full threat model.

---

### `test_tool_selection.py` — LLM Tool Selection Evals

Tests whether the LLM picks the correct tool for a natural-language question. Calls the real LLM with the actual tool definitions but does **not execute** any tools — it only inspects which tools the model chose to call.

**Requires a working LLM backend** (set `LLM_PROVIDER` / API keys as usual).

```bash
# Uses whatever is in .env
python -m pytest tests/test_tool_selection.py -v -s

# Test a specific provider
LLM_PROVIDER=openai python -m pytest tests/test_tool_selection.py -v -s
LLM_PROVIDER=anthropic python -m pytest tests/test_tool_selection.py -v -s
LLM_PROVIDER=ollama OLLAMA_MODEL=llama3.1:8b python -m pytest tests/test_tool_selection.py -v -s
```

Use `-s` to see which tools the model selected for each question.

**30 test cases** covering all 7 tool categories:

| Category | Tests | Example question |
|----------|-------|------------------|
| Logs | 4 | "Show me kernel messages" → `check_dmesg` |
| System health | 7 | "How much RAM is being used?" → `check_memory` |
| Services | 4 | "Restart the nginx service" → `restart_service` |
| Network | 5 | "Can we reach 8.8.8.8?" → `ping_host` |
| Users & files | 3 | "Show me the cron jobs" → `check_cron_jobs` |
| Security | 3 | "Run a security audit" → `system_audit` |
| General purpose | 2 | "Show me the routing table" → `run_command` |
| Ambiguous | 2 | "The website is down" → any of several tools |

Each test case defines a set of **acceptable tools** — the eval passes if the model's first tool call is in that set. Open-ended questions (like "Why is the server slow?") accept multiple tools since there are several valid first steps.

**Baseline results:**

| Model | Score | Notes |
|-------|-------|-------|
| gpt-4o-mini | 29/30 (97%) | Only miss: checked outdated packages before updating (cautious, not wrong) |

Run against different models to compare tool selection quality — especially useful when evaluating smaller/local models.

---

## Running All Tests

```bash
# Safety evals only (fast, no LLM needed)
python -m pytest tests/test_safety.py -v

# Tool selection evals only (needs LLM, ~30-60s)
python -m pytest tests/test_tool_selection.py -v -s

# Everything
python -m pytest tests/ -v -s
```
