# Secret Scrub Extension for pi-mono

A pi extension that keeps **API keys, passwords and other credentials out of everything sent
to the LLM**. Scrubbing is **always ON at session start**; the user can turn it OFF mid-session
and back ON at any time.

## Quick Reference

| Item | Value |
|------|-------|
| Extension File | `~/.pi/agent/extensions/secret-scrub.ts` |
| Commands | `/redact` · `/redact-on` · `/redact-off` · `/secret-scrub [on\|off\|status\|test <text>]` |
| Log File | `/tmp/pimono-secret-scrub.log` |
| Default State | ON at every session start |

`/redact`, `/redact-on` and `/redact-off` are short aliases: `/redact` shows the current
redaction state (and accepts the same `on|off|status|test <text>` arguments as
`/secret-scrub`), while `/redact-on` / `/redact-off` are dedicated one-shot toggles that
ignore their arguments.

## Behavior

| Hook | What it does |
|------|--------------|
| `session_start` | Resets scrubbing to **ON** (deterministic, no persisted setting) and notifies the user. |
| `context` | Fires before **each LLM call**. Scrubs user prompts, assistant messages, tool-call arguments and tool-result messages in the outgoing request. Copy-on-write: the local transcript keeps originals; only the provider payload is redacted. |
| `tool_result` | Redacts tool output **durably** (content + `structuredContent`, replaced together per pi's contract, plus `details` only when it contains hits), so transcript consumers such as compaction summaries and memory extensions receive redacted values. |
| `/secret-scrub` | Toggles/queries state; `test <text>` previews redaction without touching the conversation. |

Tool-call **arguments are never scrubbed before execution** (mutating input would break
legitimate credential workflows, e.g. storing a key in a file). Instead the recorded
tool-call arguments are scrubbed in outgoing LLM requests by the `context` hook.

## State Semantics

- Session start → **ON** (always, even if it was turned off in a previous session).
- `/redact-off` or `/secret-scrub off` → OFF for the rest of the current session; full
  content flows to the LLM.
- `/redact-on` or `/secret-scrub on` → re-enables; previously transcribed content is
  re-scrubbed on the next request because `context` runs before every LLM call.
- `/redact` or `/secret-scrub` (no argument) → status line.
- `/redact test <text>` → shows the redacted preview and the pattern names matched.

## Detection Patterns

Regex-based, deliberately protective (favors redacting plausible matches over missing secrets):

| Kind | Matches |
|------|---------|
| `private_key` | PEM blocks (`BEGIN … PRIVATE KEY`) |
| `github_token` / `github_pat` / `gitlab_token` | `ghp-…`, `gho-/ghs-/ghu-/ghr-…`, `github-pat-…`, `glpat-…` |
| `anthropic_api_key` / `openai_api_key` | `sk-ant-…`, `sk-…`/`sk-proj-…` (≥20 chars) |
| `google_api_key` | `AIza…` (35 chars) |
| `aws_access_key_id` / `aws_secret_access_key` | `AKIA/ASIA/ABIA/ACCA…`, contextual 40-char secrets |
| `slack_token` / `stripe_secret_key` / `npm_token` | `xox-…`, `sk-live/rk-live (Stripe live/restricted keys)`, `npm-…` |
| `jwt` | `eyJ` two/three-segment tokens |
| `auth_header` | `Bearer`/`Basic` + token (≥12 chars) |
| `db_url_password` | password segment of `postgres://user:***@…`, `mysql://`, `mongodb(+srv)://`, `redis(s)://`, `amqp(s)://` |
| `env_credential` | `UPPER_SNAKE` names ending in `_API_KEY`, `_TOKEN`, `SECRET`, `PASSWORD`, … with `name = value` (name kept in output) |
| `credential_value` | mixed-case `password:`, `api_key:`, `token:`, `secret:`, … values ≥6 chars |

False-positive guards: values that look like variable references (`${VAR}`, `tokens[i]`,
`%VAR%`), already-masked output (`********`) or placeholders (`changeme`, `example`) are left
untouched. Replacement placeholders (`[REDACTED:<kind>]`) are themselves never re-matched.

## Known Limitations

- Short secrets (<6 characters) are not detected.
- Compaction summarization runs inside pi and rebuilds its request from the transcript:
  durable `tool_result` redaction covers tool outputs, but user prompts are only redacted in
  normal requests. Prefer not pasting raw secrets into prompts while scrubbing is OFF.
- Headless modes (JSON/print) have no UI: state changes are logged to
  `/tmp/pimono-secret-scrub.log` instead of notified.