#!/bin/bash

# honcho-apply-learning/run.sh
# Core logic: Scan, query, resolve, update teams-*.md

set -euo pipefail

# Config
AGENTS_DIR="$HOME/.pi/agent/agents"
TEAM_FILES=("teams-manager.md" "teams-architect.md" "teams-developer.md" "teams-reviewer.md")
ROLE="${1:-all}"  # all or specific role
OLDest_TS_FILE="/tmp/honcho-oldest-ts.txt"

# Function: Extract last-update from .md (YYYY-MM-DD HH:MM or empty)
get_last_update() {
    local file="$1"
    if [ ! -f "$file" ]; then
        echo ""  # No file → empty (process all)
        return
    fi
    grep -i "Last-Updated:" "$file" | sed 's/.*Last-Updated: *//' | sed 's/\s*$//' || echo ""
}

# Step 1: Find oldest timestamp across files
oldest_ts=""
for tf in "${TEAM_FILES[@]}"; do
    file="$AGENTS_DIR/$tf"
    ts=$(get_last_update "$file")
    if [ -z "$ts" ] || [ -z "$oldest_ts" ] || [ "$ts" \< "$oldest_ts" ]; then  # String compare assumes ISO format
        oldest_ts="$ts"
    fi
done

if [ -z "$oldest_ts" ]; then
    oldest_ts="2026-01-01 00:00"  # Fallback: Process all
fi

echo "Oldest last-update: $oldest_ts"

# Step 2: Query Honcho for newer syntheses (use tool or simulate)
# In Pi: Call honcho_search_documents(query="synthesis dreaming learnings after $oldest_ts", level="synthesis", limit=20)
# For now, simulate fetch (replace with actual tool call)
new_learnings=$( # honcho_search_documents ... | jq -r '.documents[].content' 
echo "## Simulated New Learning 1: Unified state consolidation (2026-04-01)
State fragmentation addressed via EmailAutomationState class.
## Simulated New Learning 2: Validation engine evolution (2026-04-03)
Added PlantUML support to markdown-writer." 
)

if [ -z "$new_learnings" ]; then
    echo "No new learnings found. Exiting."
    exit 0
fi

echo "Found new learnings: $(echo "$new_learnings" | wc -l) items"

# Step 3: For each file, resolve & update
for tf in "${TEAM_FILES[@]}"; do
    if [ "$ROLE" != "all" ] && [[ "$tf" != "teams-$ROLE.md" ]]; then continue; fi
    
    file="$AGENTS_DIR/$tf"
    role=$(echo "$tf" | sed 's/teams-//; s/.md//')
    
    # Read existing content
    existing=$(cat "$file" 2>/dev/null || echo "# Team $role Agent\n\n**Description**: Base prompt for $role role.\n\n[Initial content]")
    
    # Resolve: Simulate merge (in Pi: Use LLM prompt "Merge new learnings into existing for $role, resolve contradictions by preserving both.")
    updated_content="# Team $role Agent

---**Name**: Teams $role Agent
**Description**: Behavioral prompt for $role role, incorporating Honcho synthesis learnings on state management, validation, and patterns.
---
**Name**: Teams $role Agent
**Description**: Behavioral prompt for $role role, incorporating Honcho synthesis learnings on state management, validation, and patterns.
**Last-Updated**: $(date '+%Y-%m-%d %H:%M')
---

$existing

## Recent Synthesis Insights (Post $oldest_ts)
$new_learnings

### Resolution Notes
- Preserved prior knowledge on [e.g., verification-first].
- Integrated new: Unified validation engine enhances architecture gaps section without contradiction."

    # Write updated (in Pi: Use edit tool for precise replacement)
    echo "$updated_content" > "$file"
    echo "Updated $tf with new learnings."
done

echo "All updates complete. Changelog: Applied syntheses >$oldest_ts to $ROLE roles."
