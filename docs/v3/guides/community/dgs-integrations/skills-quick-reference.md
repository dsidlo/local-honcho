# Skills Quick Reference: Learnings → Implementation

## 🎯 IMMEDIATE ACTIONS (This Week)

### 1. State Consolidation → **state-manager** (NEW SKILL)
**Problem**: Email config scattered across 4+ files  
**Solution**: Unified state API

```bash
# Before: Each script has own state file
check_recruiter_emails.py → /tmp/recruiter_seen_ids.txt
check_comfy_email.py → /tmp/comfy_seen_ids.txt
grocery_deals.py → /tmp/grocery_seen_ids.txt

# After: Single skill manages all
state-manager record_seen recruiter/seen_emails <ID>
state-manager is_processed grocery/seen_deals <ID>
state-manager list_states
```

---

### 2. Configuration Fragmentation → **config-unifier** (NEW SKILL)
**Problem**: Settings in .env, settings.json, .bashrc, ad-hoc files  
**Solution**: ~/.openclaw/config/

```bash
# Before: 4+ mechanisms
LLM_MODELS in .env
PEER_NAME in .openclaw/
WORKSPACE_PATHS in settings.json
GOG_KEYRING_PASSWORD in .bashrc

# After: Unified
cat ~/.openclaw/config/llm-config.yaml
cat ~/.openclaw/config/notification-config.yaml
```

---

### 3. Credential Inventory → **Extend dt-reviewer** (EXISTING SKILL)
**Problem**: Credentials scattered, manual rotation  
**Solution**: Security audit tool

```bash
# Extend dt-reviewer with:
dt-reviewer audit_credentials
dt-reviewer credential_rotate --type discord
dt-reviewer check_permissions ~/.env
```

---

## 📋 MEDIUM TERM (Next 3 Weeks)

### 4. Validation Gaps → **validation-engine** (EVOLVES markdown-writer)
**Problem**: 6 formats declared, only Mermaid implemented  
**Solution**: Pluggable validation framework

```bash
# markdown-writer becomes validation-engine
validation-engine validate file.md --format mermaid
validation-engine validate file.md --format plantuml
current_gap: markdown-writer only covers mermaid
```

**Capability Separation**:
- `qna` → uses read-only validation
- `markdown-writer` → uses validation + fix

---

### 5. Auto-Verification → **auto-verifier** (NEW SKILL)
**Problem**: Manual config verification despite preference for automation  
**Solution**: Automated post-config checks

```bash
# Triggered automatically after:
- settings.json modification
- extension installation
- crontab update

# Outputs:
✅ Honcho: running on 5433
✅ API: responsive
❌ Deriver: process not found
```

---

### 6. Skill Boundary Enforcement → **skill-governor** (NEW SKILL)
**Problem**: Declared capability ≠ actual implementation  
**Solution**: Skill audit layer

```bash
skill-governor audit markdown-writer
# Output:
# Declared: [mermaid, plantuml, d2, graphviz, structurizr, wireviz]
# Implemented: [mermaid]
# Gap: 5 formats missing implementation
```

---

## 🔧 SKILL EXTENSIONS (Existing Skills)

### dt-developer → Add resilient-processing
**Purpose**: Consolidate scattered retry/fallback logic

```typescript
// Replaces implementations in:
// - honcho.ts: flushMessages chunk retries
// - message chunking with fallback
// - API calls with exponential backoff

ResilientProcessor.configure({
  maxRetries: 3,
  retryDelay: 1000,
  fallbackStrategies: ['paragraph', 'sentence', 'character']
}).process(item)
```

---

### dt-tester → Add pre-validation
**Purpose**: Catch errors before API calls

```bash
dt-tester validate_workspace_name "-pi"  # ❌ Invalid
dt-tester preflight_check honcho_request
```

---

### agent-memory → Add config validation
**Purpose**: Ensure token threshold consistency

```typescript
// Validates:
// - reflector.maxTokens > observer.maxTokens
// - compactionTrigger > keepRecentTokens
honcho_config.validate_thresholds()
```

---

## 🗺️ LEARNINGS TO SKILL MATRIX

| Learning | Best Fix | Skill Type |
|----------|----------|------------|
| State Consolidation | **state-manager** | NEW |
| Config Fragmentation | **config-unifier** | NEW |
| Validation Gaps | **validation-engine** | EVOLVE |
| Auto-Verification | **auto-verifier** | NEW |
| Credential Inventory | **dt-reviewer** | EXTEND |
| Skill Boundaries | **skill-governor** | NEW |
| Retry/Resilience | **dt-developer** | EXTEND |
| Pre-Validation | **dt-tester** | EXTEND |
| Token Config | **agent-memory** | EXTEND |

---

## 👷 RECOMMENDED IMPLEMENTATION ORDER

### Phase 1 (Security & Automation) - Week 1
1. **config-unifier** - Centralize scattered config
2. **dt-reviewer** (extend) - Add credential audit
3. **auto-verifier** - Automated verification hooks

### Phase 2 (State Management) - Week 2-3
4. **state-manager** - Unified state for pipelines
5. **agent-memory** (extend) - Token threshold validation

### Phase 3 (Validation) - Week 4-5
6. **validation-engine** - Multi-format validation
7. **dt-tester** (extend) - Pre-validation layer

### Phase 4 (Governance) - Week 6
8. **skill-governor** - Capability audit

---

## 📊 IMPACT ANALYSIS

| Skill | Synthesis Docs Addressed | Current Pain Level |
|-------|------------------------|---------------------|
| config-unifier | 18 (config fragmentation) | 🔥 HIGH |
| state-manager | 12 (state consolidation) | 🔥 HIGH |
| dt-reviewer (extend) | 5 (security/credentials) | 🔥 HIGH |
| validation-engine | 7 (architecture gaps) | ⚠️ MEDIUM |
| auto-verifier | 6 (verification gaps) | ⚠️ MEDIUM |
| skill-governor | 3 (skill boundaries) | ⚠️ MEDIUM |

**Total Synthesis Coverage**: 51/45 (some docs address multiple themes)

---

## 💡 WHY THESE SKILLS?

### Why NEW skills vs extending existing?

**Extend When**:
- Existing skill has right context (dt-reviewer for security)
- Natural fit with existing purpose
- Not too large/complex

**Create NEW When**:
- Spans multiple existing skill boundaries
- Requires new architectural layer
- High reuse potential across skills

---

## 🎬 GETTING STARTED

### Today:
```bash
# 1. Read the full mapping
open /home/dsidlo/.openclaw/workspace/skills-to-learnings-mapping.md

# 2. Start with highest impact
# Create config-unifier first (P0)
cd ~/.pi/agent/skills/pi-skills/
mkdir config-unifier
# Use synthesis as requirements doc

# 3. Extend dt-reviewer
cd ~/.pi/agent/skills/dytopo-skills/dt-reviewer
# Add credential audit capability
```

---

**Full Reports**:
- Synthesis Analysis: `/home/dsidlo/.openclaw/workspace/synthesis-report-2026-03-28.md`
- Detailed Mapping: `/home/dsidlo/.openclaw/workspace/skills-to-learnings-mapping.md`
