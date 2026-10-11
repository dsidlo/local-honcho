---
name: honcho-conclusions
description: >
  Read Honcho conclusions for this workspace: the distilled observations the
  deriver and dreamer have already written. Use when the user asks what
  Honcho has concluded, or when a decision needs the standing model of the
  user rather than a raw transcript. Do not call on every turn.
when-to-use: >
  what has Honcho concluded, user model, standing conclusions, dreamer
user-invocable: true
---

# honcho-conclusions

Call the `honcho_conclusions` tool on the `honcho` MCP server.

- Default pair is observer `agent-grok`, observed `agent-grok`. With `observe_others` off, that is the pair the deriver fills.
- Pass `observed=dsidlo` when the question is what the agent has concluded about the user. That pair stays empty until `observe_others` is turned on for `agent-grok`.
- This is hybrid search (vector, full text, and trigram) on the local server. It is the fast read. It is not the dialectic.
- Do not write a conclusion you like into `~/.grok/rules/SOUL.md`. Promotion into Grok memory is a separate, explicit step.
- If the tool errors, report the error. Do not fill the gap with a guess.
