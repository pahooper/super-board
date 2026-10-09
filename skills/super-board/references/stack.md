# super-board stack — see or change what the project is built with and how it is tested

Loaded by `SKILL.md` for `super-board stack`. Interactive. It is onboard step 4 on its own,
written straight to the config instead of waiting for onboard's Review.

| Invocation | Does |
|---|---|
| `/super-board stack` | show the stack; keep it, change one part, or detect it again |
| `/super-board stack starter` | add a new project's starter on a board that skipped it (onboard step 4 → path B) |

Script: `python3 .claude/bin/super-board-stack.py` (`detect · presets · preset · resolve ·
verify · apply · scaffold`).

## Flow

```
0. GUARD
   ├─ Config: as lint Phase 0.
   └─ A run in flight (.claude/super-board/inflight/ has a lock, or workflow-wave.lock exists) →
      "A run is going. Stop it first (/super-board stop) — workers read the stack mid-run."

1. SHOW — the block onboard step 4 shows, from:
      python3 .claude/bin/super-board-stack.py resolve --config <cfg> --files
   source "detected-at-runtime" (an older config with no stack) → say so in one line:
      "This board has no saved stack yet — here is what I detect. Save it?"

2. ASK — AskUserQuestion "What now?" (header "Stack")
   • Keep it (Recommended) — saved stack: nothing changes; unsaved: save it
   • Change one part — exactly onboard step 4 → A → "Change one part"
   • Detect it again — re-read the repo (new framework, new test runner since onboard)
   `starter` → onboard step 4 → B (new projects only; scaffold refuses a folder with code).

3. VERIFY — onboard step 4 → "Every path": `verify`, missing commands through the permission
   prompt, the skills question.

4. WRITE
   ├─ super-board-stack.py apply --config <cfg> --stack <staged> --qa-mode <mode> --verify-commands keep --dry-run
   ├─ The config already has verify_commands that differ from the stack's → ask once:
   │    "Your merge gate runs <n> hand-written commands. Use the stack's instead?"
   │    [Keep mine (Recommended) / Use the stack's — <list>] → `--verify-commands keep|replace`.
   │    Empty → `auto` fills them, no question.
   ├─ apply (for real), then show the config diff (git diff --stat + the stack block).
   ├─ New runners need permission lines → `super-board-settings.py allow .claude/settings.json
   │    "Bash(<first two words>:*)" … --dry-run`, then ask [Add <N> lines (Recommended) / Skip].
   └─ AskUserQuestion "Commit the config change?" [Yes (Recommended) — "a run needs a clean
      tree" / No]. Yes → `🔧 [chore] stack: <what changed>` (writing-standard.md § 1).

5. DONE — one line: `✓ Stack saved: <components> · QA: <mode> · merge gate: <n> commands.`
```

## Rules

- NEVER edit the config's `stack` by hand from this verb — `apply` validates and writes atomically.
- NEVER change the stack while a run is in flight.
- A runner the user typed that `verify` cannot find is saved anyway when they say so; lint's
  pre-flight then lists it as a `[ ]` gate, and `run` will not start until it is green.
