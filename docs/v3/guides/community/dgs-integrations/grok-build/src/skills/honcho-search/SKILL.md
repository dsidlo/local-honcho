---
name: honcho-search
description: >
  Search Honcho's record of this workspace (prompts, thoughts, and replies
  captured from Grok, plus Pi and jcode when they share the workspace).
  Use when the user asks what was tried, decided, or said earlier, or says
  "search memory" or "what did we do about". Do not call on every turn.
  Do not use it to find files in the repo.
when-to-use: >
  what did we decide, what did we try, search memory, earlier session,
  project history
user-invocable: true
---

# honcho-search

Call the `honcho_search` tool on the `honcho` MCP server.

- Pass a short query. Limit defaults to 5.
- Leave `workspace` empty. The server uses the git origin name inside a repo, and the flattened directory path otherwise.
- The result is episodic text, not a conclusion. For distilled memory, use `honcho_conclusions`. For a reasoned answer, use `honcho_ask`, and only when the user asked for one.
- If the MCP server is not connected, say so. Do not invent a memory and do not read `~/.grok/sessions` to reconstruct one.
