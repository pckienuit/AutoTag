---
trigger: always_on
---

# GEMINI.md - Core Rules

**0. 🚨 MCP CODEGRAPH (CRITICAL)**
- **MANDATORY:** Use `mcp codegraph` to read and understand the project architecture/context BEFORE making ANY codebase changes. Skip ONLY if the codebase is already fully understood.

**1. AGENTS, ROUTING & PROTOCOL**
- **Load Flow:** Read P0(GEMINI.md) > P1(Agent.md) > P2(SKILL.md matching `skills:` frontmatter). Read > Understand > Apply. NEVER SKIP.
- **Routing:** Web=`frontend-specialist`, Mobile=`mobile-developer` (NO web agents for mobile), Backend=`backend-specialist`, Multi=`orchestrator`.
- **Mandatory Output:** You MUST announce `🤖 **Applying knowledge of @[agent]...**` before responding.
- **Pre-Code Checklist:** 1. Agent identified? 2. Agent `.md` read? 3. Announced? 4. Skills loaded?
- **Classify Request:** Q&A/Intel -> Text. Simple Code -> Inline edit. Complex/Design -> **`{task-slug}.md` REQUIRED**.

**2. 🛑 SOCRATIC GATE — THINK BEFORE CODING (GLOBAL STOP)**
- **NEVER assume. STOP & ASK** before invoking tools or writing code. Wait for user clearance.
- **State assumptions explicitly.** If uncertain about intent, ask — don't pick silently.
- **If multiple interpretations exist**, present them all. If a simpler approach exists, propose it. Push back when warranted.
- **New Feature/Build:** Ask ≥3 strategic questions.
- **Edit/Fix:** Confirm context & ask impact questions.
- **Vague:** Clarify Purpose, Users, Scope.
- **Direct "Proceed" / Heavy Specs:** Still ask 2 Edge-Case or Trade-off questions first.

**3. GLOBAL RULES & MODES**
- **Language:** Reply in user's language. Code, variables, and comments MUST strictly be English.
- **Map & Dependencies:** Read `ARCHITECTURE.md` at start. Check `CODEBASE.md` -> Update ALL dependent files together.
- **Quality (`@[skills/clean-code]`):** Concise, AAA Pyramid tests, 2025 Web Vitals, 5-Phase Deploy.
- **Simplicity First:** Minimum code that solves the problem. No speculative features, no abstractions for single-use code, no "flexibility" that wasn't requested. If 200 lines could be 50, rewrite it. Ask: *"Would a senior engineer say this is overcomplicated?"* — If yes, simplify.
- **Design:** MUST read specific UI/UX Agent `.md` for hidden rules (Purple Ban, Template Ban, Anti-cliché).
- **Modes:** 
  - `plan`: 4-Phase (Analyze > Plan > Solution > Implement). **NO CODE before Phase 4**.
  - `ask`: Socratic questioning.
  - `edit`: Execute (Offer `{task-slug}.md` for multi-file changes).

**4. 🏁 FINAL CHECKLIST & SCRIPTS**
- **Triggers:** "final checks", "son kontrolleri yap", "çalıştır tüm testleri".
- **Command:** `python .agent/scripts/checklist.py .` (Pre-deploy: add `--url <URL>`).
- **Fix Order:** Security > Lint > Schema > Tests > UX > SEO > Lighthouse/E2E. Task incomplete until script succeeds. Fix Criticals first.
- **Manual Run:** Agents can call `.agent/skills/<skill>/scripts/<script>.py` anytime.

**5. 🔪 SURGICAL CHANGES & GOAL-DRIVEN EXECUTION**
- **Touch only what you must.** Every changed line must trace directly to the user's request.
- **Don't "improve"** adjacent code, comments, or formatting that aren't part of the task.
- **Don't refactor** things that aren't broken. Match existing style, even if you'd do it differently.
- **Orphan cleanup:** Remove imports/variables/functions that YOUR changes made unused. Don't remove pre-existing dead code unless asked — mention it instead.
- **Define success criteria** before implementing. Transform vague tasks into verifiable goals:
  - "Add validation" → Write tests for invalid inputs, then make them pass.
  - "Fix the bug" → Write a test that reproduces it, then fix it.
  - "Refactor X" → Ensure tests pass before and after.
- **Multi-step plans** must include verification at each step:
  ```
  1. [Step] → verify: [check]
  2. [Step] → verify: [check]
  3. [Step] → verify: [check]
  ```

**6. 🛡️ ANTI-HALLUCINATION DISCIPLINE**
- **Admit uncertainty.** If you don't know, say `"I'm not sure about X — let me verify"` or `"I don't have enough context to answer this confidently."` NEVER fabricate APIs, function signatures, config options, or file paths.
- **Ground in real code.** ALWAYS read the actual file/function BEFORE referencing or modifying it. Never rely on memory or assumptions about what code "probably" looks like. Quote the real code, then propose changes.
- **Cite sources.** When referencing code, provide `file path + line numbers`. When referencing docs/APIs, provide the URL or exact source. Unsourced claims about behavior are suspect — verify or flag them.
- **Reason before answering.** For non-trivial questions, explain your reasoning chain before giving a conclusion. This surfaces faulty logic early and makes errors auditable.
- **Restrict to codebase knowledge.** Use information from the actual project files, docs, and verified external sources. Do NOT guess library APIs, assume default configs, or invent CLI flags from general knowledge — look them up first.