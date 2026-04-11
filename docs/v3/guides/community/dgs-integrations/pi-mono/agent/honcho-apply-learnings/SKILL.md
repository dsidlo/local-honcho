---
name: honcho-apply-learnings
description: |
  This skill applies learnings from Honcho Dreaming (synthesis documents) to update the pi-mono team agent prompt files in ~/.pi/agent/agents/teams-[Manager,Architect,Developer,Reviewer].md. It scans for the oldest "Last-Updated" header timestamp, queries Honcho for newer synthesis documents, resolves contradictions while preserving prior knowledge, and generates targeted updates. Adds a plain-text header (first lines, no MD formatting) to each .md: `Name: Teams [Role] Agent\nDescription: [Brief role summary]\nLast-Updated: [YYYY-MM-DD HH:MM]\n---\n\n# Team [Role] Agent\n\n[Updated content]`. The header is separated by `---` from the Markdown body. Key behaviors include temporal filtering, contradiction resolution via merging, full preservation of original content, post-update validation, and fallback for no new learnings.
enabled: false  # Prevents auto-invocation

---

# honcho-apply-learning Skill

## Usage in Pi

- **Activation**: Load as skill in Pi (copy to `~/.pi/agent/skills/`).
- **Invocation**: `/honcho-apply-learning [role=Manager|Architect|Developer|Reviewer|all]` (default: all).
- **Output**: Updated files + changelog (e.g., "Applied 3 new syntheses to Architect.md: state consolidation patterns").
- **Example Flow**:
  1. Scan: Oldest last-update=2026-03-28.
  2. Query: Search syntheses >2026-03-28 (e.g., "state fragmentation fixes").
  3. Resolve: New learning "unified validation engine" → Enhance Architect prompt's "Architecture Gaps" section.
  4. Update: Add header, integrate, set Last-Updated=2026-04-05 15:30.

## Implementation Notes

- **Timestamp Parsing**: Use `bash` to extract "Last-Updated: YYYY-MM-DD HH:MM" via grep/sed; convert to epoch for comparison.
- **Query Honcho**: `honcho_search_documents(query="synthesis dreaming learnings", level="synthesis", limit=20, metadata_filter="created_at > $oldest_ts")`.
- **Resolution Logic**: Prompt-based (e.g., "Merge this new synthesis [content] into existing prompt [old section], resolving conflicts by preserving both views.").
- **Header Addition**: If missing, prepend; always update timestamp.
- **File Paths**: Target: `~/.pi/agent/agents/teams-*.md` (create if absent with base template).
- **Edge Cases**: No files → Create stubs; Contradictions → Flag in changelog; API fail → Use local .md/docs search.
- **Apply Learning**: Only apply learings to roles for which the learning is relevant and aligns with the agent's purpose.
- **Project Specific Guidance**: If the guidance is project-specific, isolate all advice that is specific to a project under a heading "# Project: <project-name>"

**Version**: 1.0 | Author: Pi Agent | Date: 2026-04-05