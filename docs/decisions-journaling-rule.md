# Project rule — decisions journaling

**Installed repo path:** `/home/mikl/KurchatovCoop/autosaxs/.cursor/rules/decisions-journaling.mdc`  
**Companion artifact:** `docs/DECISIONS.md` (repo) + this store’s [DECISIONS.md](DECISIONS.md).

Keep this file byte-aligned in meaning with the `.mdc` rule (frontmatter aside).

## When this applies

Whenever Mikhail (or an agent with Mikhail’s explicit acceptance) **accepts or rejects** a design/product choice that will shape future code or playbooks — especially after stepwise review of large proposals (e.g. code-vs-skills split).

## What to do

1. Append an entry to **`docs/DECISIONS.md`** (do not rewrite history). Use sections **autoSAXS** vs **GUI**.
2. Each entry must include: short title, date, status (`accepted` / `rejected` / `superseded by D-…`), context, decision, justification (enough that a stranger understands why), and refs (chat date, doc links, literature if scientific).
3. If a new decision contradicts an older one, keep the old entry and set its status to superseded; update Project `preferences.md` only when the decision is a lasting preference (one sentence + date).
4. Agents must **read** `docs/DECISIONS.md` before implementing related work and must not silently reverse an accepted decision.
5. Keep the Project store mirror (`docs/DECISIONS.md`) in sync when working in the autoSAXS Project.

## What not to do

- Do not dump implementation TODOs or PR chatter into DECISIONS.
- Do not journal every micro-choice; only decisions that bind future behavior or review outcomes.
- Do not put end-user UI strings about “DECISIONS.md” in the product.
