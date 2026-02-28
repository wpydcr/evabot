---
name: skill-vetter
description: Security-first skill vetting for AI agents. Use before installing any skill from ClawdHub, GitHub, or other sources. Checks for red flags, permission scope, and suspicious patterns.
---

# Skill Vetter 🔒

Security-first vetting protocol for AI agent skills. **Never install a skill without vetting it first.**

## When to Use

- Before installing any skill from ClawdHub
- Before running skills from GitHub repos
- When evaluating skills shared by other agents
- Anytime you're asked to install unknown code

## Vetting Protocol

### Step 1: Source Check (Optional)

```
Questions to answer:
- [ ] Where did this skill come from?
- [ ] Is the author known/reputable?
- [ ] How many downloads/stars does it have?
- [ ] When was it last updated?
- [ ] Are there reviews from other agents?
```

### Step 2: Code Review (MANDATORY)

Read ALL files in the skill. Check for these **RED FLAGS**:

```
🚨 REJECT IMMEDIATELY IF YOU SEE:
─────────────────────────────────────────
• curl/wget to unknown URLs
• Sends data to external servers
• Requests credentials/tokens/API keys
• Reads ~/.ssh, ~/.aws, ~/.config without clear reason
• Accesses MEMORY.md, USER.md, SOUL.md, IDENTITY.md
• Uses base64 decode on anything
• Uses eval() or exec() with external input
• Modifies system files outside workspace
• Installs packages without listing them
• Network calls to IPs instead of domains
• Obfuscated code (compressed, encoded, minified)
• Requests elevated/sudo permissions
• Accesses browser cookies/sessions
• Touches credential files
─────────────────────────────────────────
```

### Step 3: Permission Scope

```
Evaluate:
- [ ] What files does it need to read?
- [ ] What files does it need to write?
- [ ] What commands does it run?
- [ ] Does it need network access? To where?
- [ ] Is the scope minimal for its stated purpose?
```

### Step 4: Risk Classification

| Risk Level | Examples | Action |
|------------|----------|--------|
| LOW | Notes, weather, formatting | Basic review, install OK |
| MEDIUM | File ops, browser, APIs | Full code review required |
| HIGH | Credentials, trading, system | Human approval required |
| EXTREME | Security configs, root access | Do NOT install |

## Output Format

After vetting, produce this report:

```
SKILL VETTING REPORT
═══════════════════════════════════════
Skill: [name]
───────────────────────────────────────
METRICS:
• Downloads/Stars: [count]
• Last Updated: [date]
• Files Reviewed: [count]
───────────────────────────────────────
RED FLAGS: [None / List them]

PERMISSIONS NEEDED:
• Files: [list or "None"]
• Network: [list or "None"]  
• Commands: [list or "None"]
───────────────────────────────────────
RISK LEVEL: [LOW / MEDIUM / HIGH / EXTREME]

VERDICT: [SAFE TO INSTALL / INSTALL WITH CAUTION / DO NOT INSTALL]

NOTES: [Any observations]
═══════════════════════════════════════
```

## Quick Vet Commands

For skill.sh skills:
```bash
# Check repo stats
curl -s "https://api.github.com/repos/OWNER/REPO" | jq '{stars: .stargazers_count, forks: .forks_count, updated: .updated_at}'

# List skill files
curl -s "https://api.github.com/repos/OWNER/REPO/contents/skills/SKILL_NAME" | jq '.[].name'

# Fetch and review SKILL.md
curl -s "https://raw.githubusercontent.com/rexshang/skillscanner/main/skills/SKILL_NAME/SKILL.md"
```
For clawhub skills:
1. **Run the lookup** with the real skill URL (format: `https://clawhub.ai/author/skill-name`):

   ```bash
   curl --request POST --url "https://ai.gendigital.com/api/scan/lookup" \
     --header "Content-Type: application/json" \
     --data '{"skillUrl":"https://clawhub.ai/AUTHOR/SKILL_SLUG"}'
   ```

   Replace `AUTHOR` with actual author id and `SKILL_SLUG` with the actual skill identifier from the ClawHub URL.

2. **Interpret the response:**
   - **`status: "done"`** — Check the **`severity`** field for the verdict.
   - **`status: "analysis_pending"`** — The skill is awaiting manual review. Do not treat as safe; advise the user that the skill is pending review.

3. **Proceed or advise caution:**
   - **Proceed** only if **`severity` is `SAFE`**.
   - If severity is anything else (e.g. `WARNING`, `DANGEROUS`, `MALICIOUS`), **advise caution** and do not recommend using the skill until reviewed or cleared.


## Trust Hierarchy

1. **Official OpenClaw skills** → Lower scrutiny (still review)
2. **High-star repos (1000+)** → Moderate scrutiny
3. **New/unknown sources** → Maximum scrutiny

## Remember

- No skill is worth compromising security
- When in doubt, don't install
- Ask your human for high-risk decisions
- Reprot what you vet for future reference

---

*Paranoia is a feature.* 🔒🦀
