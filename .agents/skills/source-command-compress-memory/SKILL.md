---
name: "source-command-compress-memory"
description: "Archive finished tasks, snapshot active memory, keep AGENTS.md under 40k chars"
---

# source-command-compress-memory

Use this skill when the user asks to run the migrated source command `compress-memory`.

## Command Template

# /compress-memory

Perform a memory compression pass on this project. Work through the four steps below in order. Report results at the end — do not ask for confirmation between steps.

---

## Step 1 — Check AGENTS.md size

Read `AGENTS.md` and count its characters (`wc -c AGENTS.md`).

- If ≤ 40,000 chars: note the size and continue.
- If > 40,000 chars: the root file has grown beyond the lightweight-index contract. Identify which sections were added directly to `AGENTS.md` instead of to a `.Codex/rules/` file, move them, and replace with a `@.Codex/rules/<file>.md` pointer. Target: back under 40,000 chars.

---

## Step 2 — Archive finished tasks from roadmap-todo.md

Read `.Codex/rules/roadmap-todo.md`.

Find all items that are unambiguously done — marked with strikethrough (`~~...~~`), labelled `RESOLVED`, `IMPLEMENTED`, `DONE`, or already present in the codebase with no remaining action.

Move them to `.Codex/rules/archive.md` under a heading:
```
## Archived <YYYY-MM-DD>
```
Append to the file if it already exists; create it if it does not.

Remove the moved items from `roadmap-todo.md`. Do not remove items that are partially done or still have sub-tasks open.

---

## Step 3 — Snapshot active memory

Read `MEMORY.md` (the project memory index at `/Users/jens/.Codex/projects/-Users-jens-develop-geberit-aquaclean/memory/MEMORY.md`).

For each linked memory file:
- If the memory duplicates something already captured in `.Codex/rules/` verbatim, remove the memory file and its MEMORY.md index entry.
- If the memory refers to a fact now embedded in `AGENTS.md` or a rules file (e.g. a feedback rule that became a mandatory rule), remove it from MEMORY.md and its file.
- If the memory is still load-bearing (non-obvious, not derivable from the current code or rules), keep it exactly as-is.

After pruning, write a one-line snapshot entry to `.Codex/rules/archive.md`:
```
## Memory snapshot <YYYY-MM-DD>
Active memory entries: N  |  Pruned: M  |  Snapshot taken by /compress-memory
```

---

## Step 4 — Report

Print a concise summary:
```
/compress-memory results
========================
AGENTS.md:         <before> chars → <after> chars  (limit: 40,000)
Tasks archived:    N items moved to .Codex/rules/archive.md
Memory pruned:     M entries removed from MEMORY.md
Files modified:    list them
```

If nothing needed changing in a step, say so explicitly (e.g. "AGENTS.md: 3001 chars — within limit, no change").
