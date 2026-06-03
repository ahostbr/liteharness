---
name: ls-find-skills
description: "Use when discovering, searching for, or installing new agent skills from the open ecosystem. Triggers on 'find a skill', 'is there a skill for', 'search for skills', 'install a skill', 'search skills.sh', 'find-skill-sh'. Covers both npx skills CLI and the skills.sh ecosystem."
allowed-tools: Bash, WebSearch, WebFetch, Read, Glob
---

# Find Skills

Discover, search, and install skills from the open agent skills ecosystem.

## npx skills CLI

The Skills CLI (`npx skills`) is the package manager for the open agent skills ecosystem. Skills are modular packages that extend agent capabilities with specialized knowledge, workflows, and tools.

### Key Commands

- `npx skills find [query]` - Search for skills interactively or by keyword
- `npx skills add <package>` - Install a skill from GitHub or other sources
- `npx skills add <owner/repo@skill> -g -y` - Install globally, skip confirmation
- `npx skills check` - Check for skill updates
- `npx skills update` - Update all installed skills
- `npx skills init my-skill` - Create a new skill

### When to Use

Use this when the user:

- Asks "how do I do X" where X might be a common task with an existing skill
- Says "find a skill for X" or "is there a skill for X"
- Asks "can you do X" where X is a specialized capability
- Expresses interest in extending agent capabilities
- Wants to search for tools, templates, or workflows
- Mentions they wish they had help with a specific domain (design, testing, deployment, etc.)

### How to Help Users Find Skills

**Step 1: Understand What They Need**

Identify:

1. The domain (e.g., React, testing, design, deployment)
2. The specific task (e.g., writing tests, creating animations, reviewing PRs)
3. Whether this is a common enough task that a skill likely exists

**Step 2: Search for Skills**

```bash
npx skills find [query]
```

Examples:

- User asks "how do I make my React app faster?" -> `npx skills find react performance`
- User asks "can you help me with PR reviews?" -> `npx skills find pr review`
- User asks "I need to create a changelog" -> `npx skills find changelog`

The command returns results like:

```
Install with npx skills add <owner/repo@skill>

vercel-labs/agent-skills@vercel-react-best-practices
  https://skills.sh/vercel-labs/agent-skills/vercel-react-best-practices
```

**Step 3: Present Options**

Present results with:

1. The skill name and what it does
2. The install command they can run
3. A link to learn more at skills.sh

**Step 4: Offer to Install**

```bash
npx skills add <owner/repo@skill> -g -y
```

The `-g` flag installs globally (user-level) and `-y` skips confirmation prompts.

### When No Skills Are Found

1. Acknowledge that no existing skill was found
2. Offer to help with the task directly using general capabilities
3. Suggest the user could create their own skill with `npx skills init`

### Common Skill Categories

| Category        | Example Queries                          |
| --------------- | ---------------------------------------- |
| Web Development | react, nextjs, typescript, css, tailwind |
| Testing         | testing, jest, playwright, e2e           |
| DevOps          | deploy, docker, kubernetes, ci-cd        |
| Documentation   | docs, readme, changelog, api-docs        |
| Code Quality    | review, lint, refactor, best-practices   |
| Design          | ui, ux, design-system, accessibility     |
| Productivity    | workflow, automation, git                |

### Tips for Effective Searches

1. **Use specific keywords**: "react testing" is better than just "testing"
2. **Try alternative terms**: If "deploy" doesn't work, try "deployment" or "ci-cd"
3. **Check popular sources**: Many skills come from `vercel-labs/agent-skills` or `ComposioHQ/awesome-claude-skills`

## skills.sh Ecosystem

Browse and fetch skills directly from the skills.sh website (37K+ skills).

### Local Cache

Check the local cache first before fetching from the network:

```
.claude/plugins/liteharness/skills/_skills-sh-data/
```

Cached skills:

- `vite/INDEX.md` - Vite build tool
- `vitest/INDEX.md` - Vitest testing
- `vercel-react-best-practices/AGENTS.md` - React best practices

If a match exists, read INDEX.md directly (no network needed).

### Online Search

```
WebSearch("site:skills.sh {technology}")
```

### Present Results

| #   | Skill      | Source     | Description       |
| --- | ---------- | ---------- | ----------------- |
| 1   | skill-name | owner/repo | Brief description |

Show top 3-5 results.

### Fetch Selected Skill

```
WebFetch("https://skills.sh/{owner}/{repo}/{skill-name}",
         "Extract complete skill content including best practices and patterns")
```

### Apply Instructions

After fetching, apply to current work:

- Best practices
- Code patterns
- Common pitfalls
- Recommended approaches

### URL Patterns

- Search: `https://skills.sh/?q={query}`
- Skill page: `https://skills.sh/{owner}/{repo}/{skill-name}`

### High Quality Sources

- `vercel-labs/agent-skills` - Official Vercel skills
- `anthropics/skills` - Official Anthropic skills
- Install count indicates popularity

### Security

Skills fetched from skills.sh are automatically scanned for malicious patterns:

- **Secrets**: Embedded API keys, tokens, private keys
- **Shell injection**: Command substitution, dangerous shell commands
- **Filesystem writes**: File write operations that could modify system
- **Exfiltration**: Network requests to external domains
- **Code injection**: eval(), exec(), dynamic code execution
- **Obfuscation**: Base64 encoding, hex escapes, character code tricks

If malicious patterns are detected, a detailed warning is displayed and the content
should NOT be applied. Report suspicious skills to skills.sh maintainers.

## Usage Examples

- `/find-skills typescript` - TypeScript skills
- `/find-skills react` - React skills
- `/find-skills python fastapi` - Python FastAPI skills
- `/find-skills testing` - Testing frameworks
- `/find-skills electron` - Electron development
