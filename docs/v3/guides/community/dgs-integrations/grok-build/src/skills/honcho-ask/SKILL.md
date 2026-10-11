---
name: honcho-ask
description: >
  Ask Honcho's dialectic a single question grounded in accumulated memory.
  Use when the user asks what we know about them, or where a project left
  off and a search hit is not enough. Takes 5-30 seconds. Do not call at
  session start. Do not call on every turn. Do not inject the answer into
  a startup prompt.
when-to-use: >
  what do we know about me, where did we leave off, honcho ask, dialectic
user-invocable: true
---

# honcho-ask

Call the `honcho_ask` tool on the `honcho` MCP server.

- Default: observer `agent-grok`, no target, reasoning level `low`. Omitting `target` asks the agent about its own representation, which is the pair the deriver fills.
- Conclusion recall for this call uses RRF (vector, full text, and trigram), not vector search alone.
- Use `minimal` when a fast pass is enough. Use `medium` or higher only when the user asked for a deeper pass.
- Pass `target=dsidlo` to ask what the agent has concluded about the user. That pair stays empty until `observe_others` is on.
- Pass `workspace` only when the user named a project. Otherwise leave it empty so the server applies the git-origin rule.
- One question per turn. Quote the answer and say it came from Honcho. If the call fails or times out, stop. Do not retry with a higher reasoning level on your own.
- Do not store the answer back with `honcho_store` unless the user asks to record it.
