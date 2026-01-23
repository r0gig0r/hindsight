---
name: hindsight
description: Store user preferences, learnings from tasks, and procedure outcomes. Use to remember what works and recall context before new tasks. (user)
---

# Hindsight Memory Skill

You have persistent memory via Hindsight. **Proactively store learnings and recall context** to provide better assistance.

## Setup Check (First-Time Only)

Before using memory commands, check if Hindsight is configured:

```bash
# Check for cloud config
cat ~/.hindsight/config 2>/dev/null

# Or check for local daemon
uvx hindsight-embed daemon status 2>/dev/null
```

**If neither works**, prompt the user to run the interactive setup:

```bash
curl -fsSL https://hindsight.vectorize.io/get-skill | bash
```

This configures either **local mode** (data on their machine) or **cloud mode** (shared team memory). After setup, the commands below will work.

## Commands

### Detect Which Mode

- **Cloud mode**: `~/.hindsight/config` exists with `api_url` and `api_key` → use `hindsight` CLI
- **Local mode**: No cloud config → use `uvx hindsight-embed`

### Store a memory

**Local mode:**
```bash
uvx hindsight-embed memory retain default "User prefers TypeScript with strict mode"
uvx hindsight-embed memory retain default "Running tests requires NODE_ENV=test" --context procedures
uvx hindsight-embed memory retain default "Build failed when using Node 18, works with Node 20" --context learnings
```

**Cloud mode** (ask user for bank ID if not known, or check their existing SKILL.md):
```bash
hindsight memory retain <bank-id> "User prefers TypeScript with strict mode"
hindsight memory retain <bank-id> "Running tests requires NODE_ENV=test" --context procedures
```

### Recall memories

**Local mode:**
```bash
uvx hindsight-embed memory recall default "user preferences for this project"
uvx hindsight-embed memory recall default "what issues have we encountered before"
```

**Cloud mode:**
```bash
hindsight memory recall <bank-id> "user preferences for this project"
hindsight memory recall <bank-id> "what issues have we encountered before"
```

### Reflect on memories

**Local mode:**
```bash
uvx hindsight-embed memory reflect default "How should I approach this task based on past experience?"
```

**Cloud mode:**
```bash
hindsight memory reflect <bank-id> "How should I approach this task based on past experience?"
```

## IMPORTANT: When to Store Memories

**Always store** after you learn something valuable:

### User Preferences
- Coding style (indentation, naming conventions, language preferences)
- Tool preferences (editors, linters, formatters)
- Communication preferences
- Project conventions

### Procedure Outcomes
- Steps that successfully completed a task
- Commands that worked (or failed) and why
- Workarounds discovered
- Configuration that resolved issues

### Learnings from Tasks
- Bugs encountered and their solutions
- Performance optimizations that worked
- Architecture decisions and rationale
- Dependencies or version requirements

## IMPORTANT: When to Recall Memories

**Always recall** before:
- Starting any non-trivial task
- Making decisions about implementation
- Suggesting tools, libraries, or approaches
- Writing code in a new area of the project

## Best Practices

1. **Store immediately**: When you discover something, store it right away
2. **Be specific**: Store "npm test requires --experimental-vm-modules flag" not "tests need a flag"
3. **Include outcomes**: Store what worked AND what did not work
4. **Recall first**: Always check for relevant context before starting work
