# super-board plan — an idea becomes ordered tickets on the board

Loaded by `SKILL.md` for `super-board plan`. Interactive: the user is at the keyboard.

**Nothing here is new machinery.** The thinking is Matt Pocock's skills, the board placement is
`/super-collect`'s adopt, and the check is `super-board lint`. This file only puts them in order
and hands each one the context it needs.

| # | Step | Who runs it | Uses |
|---|---|---|---|
| 0 | Ready? | the session | config · `super-board-stack.py resolve` · Pocock's setup |
| 1 | Grill the idea | the session | `mattpocock-skills:grilling` |
| 2 | Write the spec | **the user types it** | `/to-spec` |
| 3 | Slice into tickets | **the user types it** | `/to-tickets` |
| 4 | Put them on the board | the session | `super-collect-file.sh --adopt` |
| 5 | Lint them | the session | `references/lint.md` on the new cards |

`/to-spec` and `/to-tickets` set `disable-model-invocation: true` — Pocock made them user-run
on purpose, so the session never calls them for the user. It says the one command to type and
waits. `grilling` is model-invocable and runs in-session.

## Intro

```
🧭 super-board plan
─────────────────────────────────────────────────────────
Tell me what you want built. I'll grill it until it's clear, you turn
it into a spec and tickets with Matt Pocock's /to-spec and /to-tickets,
and I put the tickets on the board in the order they can be built.

Progress: ✅ onboard  →  🧭 plan (you are here)  →  🧹 lint  →  🤖 run
─────────────────────────────────────────────────────────
```

## 0 — Ready?

```
├─ Config: as lint Phase 0 (0 configs → "Run /super-board onboard first."; 2+ → ask which)
├─ Context the grilling starts from — read, never asked:
│    python3 .claude/bin/super-board-stack.py resolve --config <cfg> --files     (all components)
│    docs/super-board/PROJECT.md, README.md, AGENTS.md, `git log --oneline -15`
│    the board: .claude/bin/super-board-card.sh --config <cfg> items  (titles + columns)
├─ Pocock's setup present? AGENTS.md / CLAUDE.md has his "## Agent skills" block AND
│    docs/agents/issue-tracker.md names GitHub as the tracker.
│    Missing → one line, then wait: "Run /setup-matt-pocock-skills once (pick GitHub
│    issues) — /to-spec and /to-tickets read it. Tell me when it's done."
│    Onboard step 6 already wrote the `## Ticket format` section into issue-tracker.md;
│    if /setup-matt-pocock-skills rewrote the file, put that section back (references/
│    ticket-format.md) — it is what makes /to-tickets write a `## Blocked by` section.
└─ Baseline: gh issue list --state open --json number --limit 500 → the issue numbers that
   exist now. Step 4 adopts only issues created after this.
```

## 1 — Grill the idea

Ask once, in chat: **"What do you want built? A sentence is enough — I'll ask the rest."**
Then invoke `mattpocock-skills:grilling` with the idea plus the step-0 context, and these
standing constraints said up front so the design tree does not reopen them:

- The stack is settled (`resolve` output). Changing it is `/super-board stack`, not this.
- Every piece of work ends as a card the board builds unattended: acceptance criteria a
  test can check, at a rung the stack has a runner for.
- Small cards: under ~400 changed lines each (`merge_policy.auto_max_lines`).
- A new project with a starter commit: the starter already builds and tests; the first
  slice is the thinnest end-to-end feature. No starter: the first slice is a walking
  skeleton — the stack builds, one test passes, CI is green.

Grilling ends when its own rule says so (no open frontier). Do not summarise it — /to-spec
reads the conversation.

## 2 — Spec, typed by the user

```
⏺ Next, type: /to-spec
  It turns this conversation into a spec on GitHub. Tell me when it's posted.
```

Wait. Do not paraphrase the spec back.

## 3 — Tickets, typed by the user

```
⏺ Next, type: /to-tickets
  It slices the spec into tracer-bullet tickets and asks you to approve the breakdown
  before it files anything. Tell me when they're filed.
```

`/to-tickets` shows the breakdown and iterates with the user before publishing; that
approval is the user's, not the session's. Wait for "done" (or for the user to say what
went wrong).

## 4 — Put them on the board

```
├─ New issues = gh issue list --state open --json number,title,labels,body --limit 500
│    minus the step-0 baseline, minus anything already on the board (card.sh items).
├─ None → "I don't see new issues since we started. Did /to-tickets file them on GitHub,
│    or locally under .scratch/? (local → run /setup-matt-pocock-skills and pick GitHub)."
├─ One block, then one question:
│      7 new tickets from /to-tickets:
│        #41 Ledger entries persist to SQLite          blocked by: none
│        #42 Add entry form                            blocked by: #41
│        …
│    AskUserQuestion "Put these 7 on the board?" (header "Board")
│      • Yes — into Backlog, then lint them (Recommended)
│      • Pick which — "next screen: tick the ones to add"
│      • Not now
├─ Each picked issue, in issue-number order (blockers were published first):
│      bash .claude/skills/super-collect/scripts/super-collect-file.sh --config <cfg> \
│        --adopt <N> --type <bug if the title/body is a fix, else feature> --label source:plan --yes
│    (`--adopt` labels it with its type and `source:collect`, places the existing issue in
│    the holding column — Backlog — and never copies it; `source:plan` shows where it came
│    from. Exit 71 = labelled but not placed: name it, carry on.)
└─ Blocked-by check, no question:
       bash .claude/bin/super-board-deps.sh --repo <owner/name> --issues <N,N,…>   (the adopted cards)
     A card whose blocked-by line is unreadable (`parseable: false`) is listed for step 5 —
     lint criterion 13 rewrites it; nothing is guessed here.
```

## 5 — Lint them

Run `references/lint.md` with its scan limited to the adopted issue numbers (Phase 1 shows
them instead of the whole board). Lint routes anything vague back through `grilling`, splits
anything too big with `/to-tickets`, and fixes the ticket format. When lint finishes, offer:

```
AskUserQuestion "Move the ones with no open blockers to Ready?" (header "Ready")
  • Yes — <k> cards; the rest wait in Backlog for their blockers (Recommended)
  • No — I'll move them myself
```

Yes → `super-board-card.sh --config <cfg> move <N> Ready` for each `runnable: true` card from
step 4's deps check (the wave planner's Blocked sweep handles the rest as blockers close).

## Done

```
⏺ 🧭 Planned: 7 tickets on the board — 2 Ready, 5 waiting on blockers.
  🤖 Next: /super-board run
```

## Rules

- NEVER invoke `/to-spec`, `/to-tickets` or `/setup-matt-pocock-skills` for the user — they
  are user-run by design. Say the command, wait.
- NEVER write tickets by hand here. A ticket the user wants that /to-tickets did not produce
  goes back through /to-tickets (or `/super-collect`), so every card has one format.
- NEVER move a card to Ready that has an open or unreadable blocker.
- The stack is context, not a question. A user who wants to change it mid-plan → finish or
  stop the plan, run `/super-board stack`, then plan again.
