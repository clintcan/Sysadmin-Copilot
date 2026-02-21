# Sysadmin Copilot — A Code Walkthrough

> Talk to your Linux server in plain English. An AI agent built with LangChain that selects CLI tools, runs them, and explains what it found.

This mini-book walks through the entire codebase: what each module does, how the pieces fit together, and the design decisions behind them. Snippets are taken directly from the source, so you can follow along.

---

## How to Read This Book

Each chapter builds on the previous one, but they also stand alone:

1. Read **Chapters 1–2** to understand the problem and the overall design.
2. Read **Chapters 3–6** to understand each module in depth, with annotated code.
3. Read **Chapters 7–8** to configure, deploy, and extend the system.

If you prefer to learn by doing, start with Chapter 8 (adding a new tool) and refer back to earlier chapters as questions come up.

---

## Table of Contents

| # | Chapter | Description |
|---|---------|-------------|
| 1 | [Introduction](01-introduction.md) | What it is, who it's for, and a sample session transcript |
| 2 | [Architecture](02-architecture.md) | Component map, data flow, and file responsibilities |
| 3 | [The Agent](03-the-agent.md) | LangChain ReAct pattern, streaming, conversation history |
| 4 | [Tools](04-tools.md) | `@tool` decorator, `run_cmd()`, output truncation, security |
| 5 | [Safety Layer](05-safety-layer.md) | Three permission tiers, blocklist, allowlist, wrapping |
| 6 | [Audit Logger](06-audit-logger.md) | JSONL logging, status codes, in-session and past-session views |
| 7 | [Configuration & Installation](07-configuration.md) | LLM providers, env vars, service account, `install.sh` |
| 8 | [Extending: Add Your Own Tool](08-extending.md) | Full tutorial — write, register, and test a new tool |
