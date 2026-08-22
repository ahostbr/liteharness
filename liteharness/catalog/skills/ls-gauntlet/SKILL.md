---
name: ls-gauntlet
description: Turns any goal into one paste-ready gauntlet prompt - concrete artifact as the quality bar, builder/critic pairs per piece, full-context critics who must measure their own side, /goal as the stop-gate and /loop as the re-entry, run on LiteSuite systems. Triggers on 'gauntlet', 'gauntlet loop', 'gauntlet this', 'loop until it beats X', 'grind until it wins'.
---

# Gauntlet — full-context edition

The user gives a goal. You hand back ONE short paste-ready prompt plus ONE `/goal` line, and offer to run it. You are not doing the work — you are writing the prompt that makes another agent grind until the work beats a real reference.

Modeled on `robonuggets/gauntlet-loop`. **Diverges from it in three places, all by ruling:** critics are never blind, the no-finish-line is enforced by `/goal` rather than requested by prose, and the run rides on LiteSuite — real terminal agents in panes, the kanban as the loop's state machine, Lite* apps for generation. The original prompt asks one agent to imagine this machinery; this house already built it.

> Every integration claim below carries a `file:line` citation, verified 2026-08-22. Lines drift — **cite the symbol, keep the line as a hint** — but a claim with no anchor is not a claim.

## Flow

1. **Read the goal.** Restate it in your head, not on screen.
2. **Collect the three inputs.** The canonical flow takes THREE, not one: *what we are trying to make*, *real examples that set the bar*, and *rules and limits we must respect*. If the user supplied a reference, use it; if not, offer 2–3 candidate bars, one line each, and stop until they pick. Ask for rules/limits only if none are stated and the goal obviously carries them (budget, stack, deadline). No bar, no prompt.
3. **Emit three things, nothing else:** the `/goal` line to type first, the prompt block, and one flat line: "I can run this here."
4. If they say run it, you are the lead agent and you follow the prompt you wrote.

## The canonical flow (Ryan's diagram, 2026-08-22 — the authority)

`C:/Projects/docs/plans/2026-08-22-gauntlet-loop/ryan-flowchart.png` — *"thats how this should work in a nutshell."* Two nested gauntlets, four exits:

```
goal + examples + rules -> figure out what GREAT actually looks like    <- re-plan re-entry
  -> break the job into CONNECTED pieces (the joins are part of the decomposition)
  -> give each piece to a specialist -> build or improve that piece

  INNER GAUNTLET, per piece:
    see it the way the USER will          (experience pass - run it, use it, before any critique)
    -> a separate critic finds the FLAWS  (absolute critique, no bar yet)
    -> ANOTHER reviewer compares with the examples   (the bar judge - a second, distinct seat)
    -> truly good enough?
         No  -> explain what falls short -> build again
         Yes -> KEEP THE PIECE AND SAVE THE EVIDENCE   (pattern record happens here)

  OUTER GAUNTLET, on the whole:
    put all accepted pieces together -> test the COMPLETE result
    -> a separate critic looks for problems in the whole
    -> another reviewer compares the WHOLE with the examples
    -> does the complete result truly hold up?
         Yes                  -> FINISHED WITH PROOF
         one piece failed     -> find the piece OR CONNECTION causing it -> that piece's build loop
         the plan failed      -> back to "what does great look like"  (re-derive the plan)
         time/budget ran out  -> STOP HONESTLY AND REPORT WHY
```

What the shape buys, in one line each:
- **The outer gauntlet is the guard against per-piece green with an unowned join** — pieces that each pass can still assemble into an inert whole.
- **Flaw-finder and bar-judge are different seats**: one asks *what is wrong with this*, the other asks *does it beat the reference*. Collapsing them loses the flaws the bar never tests.
- **The experience pass comes first**: a critic who has not used the thing critiques a diff, not a product.
- **Failure attribution routes to the CAUSE** — one failed join re-enters one build loop, never a restart of the world; a failed PLAN re-enters at "what does great look like", never at a piece.
- **Honest exhaustion is a first-class terminal**, distinct from success — and success itself is *finished with proof*, not finished.

## The bar is the whole trick

The loop only produces quality if the thing it compares against is real. A bar must be:

- **Named.** "Stripe's pricing page," not "great SaaS sites."
- **Fetchable.** The critic can screenshot the live page, read the published piece, run the binary, open the repo. A bar the critic cannot obtain becomes a bar it hallucinates.
- **Comparable.** Both can sit side by side and a judge can pick one. If you cannot picture the A/B, it is not a bar.

Prefer the hardest bar the agent can genuinely reach — a soft bar exits the loop on round one. If the goal has a measurable half (load time, pass rate, benchmark score, word count), name it beside the reference: taste plus a number beats taste alone.

| Goal | Bar that works |
|---|---|
| Website, app, UI | A specific best-in-class product's live page, screenshotted at the same viewport |
| Game, 3D, visual | Footage or screenshots from a named shipped title |
| Writing | A named author's actual published pieces, same length and format |
| Code, tooling | A named repo's implementation plus its test suite as the measurable half |
| Research, analysis | A named report or a paper's methods section, judged on rigour and coverage |

## 🔴 Critics get EVERYTHING — blindness is not independence

**RULING (Ryan, 2026-08-21): "no blind critics by design thats my call, with proper prompting they will not be sympatheic."**

The original skill blinds its critics — output only, labels stripped, never the code or the builder's reasoning — on the theory that a critic who watches the builder starts sympathising. This house runs the counter-example daily: fleet reviewers read the commits, the evidence, and the reasoning, and are MORE adversarial for it. Context is what catches a 4/4 claim whose gate never stamped the ledger.

So independence is bought differently here, and it costs the critic work, not information:

- **Two seats per piece, not one.** The FLAW-FINDER critiques absolutely — no bar in view, just *what is wrong* — after an experience pass where it runs the piece the way a user would. The BAR-JUDGE is a second, distinct seat that only compares against the examples. Different failure modes, different questions, different agents (`liteharness spawn --model <m>` makes the second seat a different model when the run warrants it).
- **Fresh context, full evidence.** Neither seat inherits the builder's conversation — but each is handed the output, the code, the reasoning, AND (for the bar-judge) the bar.
- **It must measure its own side.** Re-run the thing, re-screenshot both at the same viewport, re-fetch the bar itself. A builder's claim the critic did not check is not evidence — endorsing an unmeasured claim is sympathy no matter how little the critic saw.
- **Binary verdict.** Ours or the bar, plus the single biggest remaining gap. Never scores — an agent left to grade itself calls its own work done (a cited study: 54 loop cycles, improvement claimed in all 54, more than half actually worse or flat), and scores out of 10 drift upward every round.
- **Non-verdicts stay non-verdicts.** A critic that could not fetch the bar or could not run the output returns UNEVALUABLE and the reason. That is not a loss and not a win, and collapsing it into either invents a result nobody produced. This is the structural cure for hallucinated comparisons — no bar in hand, no judgment.

## The stop-gate is `/goal`, the re-entry is `/loop`

The viral prompt's "do not stop until the critics are wowed" reads as persuasion. The actual machine is enforcement — two native Claude Code commands the explainer videos never name:

- **`/goal <condition>`** arms a session-scoped Stop hook: the CLI checks the condition before the agent is ALLOWED to stop, and auto-clears when it holds. The refusal to finish is in the harness, not the prompt. The exit is the gate clearing or the human pulling the plug. Never a round count.
  *Evidence:* CLI bundle 2.1.239 registers `name:"goal", description:"Set a goal Claude checks before stopping", argumentHint:"[<condition> | clear]"`; live capture 2026-08-21 (session `ac965cc1`): arming a goal produced *"A session-scoped Stop hook is now active … The hook will block stopping until the condition holds. It auto-clears once the condition is met."*
- **`/loop <prompt>`** (no interval — dynamic mode) re-enters the work, self-paced via ScheduleWakeup; with an interval it is CronCreate-backed.

⚠️ **Never pair the gate with an interval loop.** Two documented behaviors compose badly: CronCreate's contract states *"Jobs only fire while the REPL is idle"*, and the Stop hook works precisely by refusing to let the session go idle — so an interval loop waiting behind an armed gate starves the tick it waits for. **This is inference from the two contracts (both observed separately, 2026-08-21), not an end-to-end observed starvation** — the test loop was killed before an unmet-stop attempt. Gate + dynamic loop, or gate alone.

## Run it on LiteSuite — the machinery already exists, fill in the pieces

When the run happens on a LiteSuite box, the gauntlet's abstract roles map onto systems that are already built. Use them — do not re-imagine them as prompt prose:

| Gauntlet role | LiteSuite system | Verified anchor |
|---|---|---|
| Lead agent | you, the session running this skill | — |
| Builder / critic fan-out | `liteharness spawn` — a real terminal agent per seat, fresh context by construction; canvas pane inside LiteSuite, PTY daemon (`--pty`, :7460) or Windows Terminal otherwise | spawn branch `cli.py:3758`; mode table in 05-LiteHarness |
| Per-piece state | `lst run tasks` kanban. **Seven columns:** `queued → thinking → building → reviewing → fixing → merging → done` (`task_store.py:18` VALID_STATUSES; CHECK constraint `:37`). **Nine actions:** `list, claim, complete, unclaim, create, update, heartbeat, sweep, help` (`tasks.py:22`). `claim` moves queued→thinking; move a piece with `update status=building/reviewing/fixing`; `complete` lands it in done. `merging` exists for the lead's integration step — a piece that needs no merge skips it. The human watches this live; an unmoved card is invisible work | `packages/litesuite-tools/litesuite_tools/tools/{task_store,tasks}.py` |
| Verdict transport | inbox (`lst run inbox` / `liteharness.cli send`). Verdict format: `OURS` / `BAR` / `UNEVALUABLE` + the single biggest gap. 🔴 Prove delivery by your own message appearing in the maildir, never by the send command returning — a hung send exits silently having delivered nothing, and unknown flags are DROPPED silently (hand-rolled `sys.argv` scan, no argparse) | `cli.py` spawn-branch flag scan; maildir `~/.liteharness/inbox/` |
| The bar, fetched | AgentBridge (`127.0.0.1:7423`, token file `agent-bridge.ts:173` → `~/.litesuite/bridge-token`) — `POST /canvas/browser` opens the live reference in a pane (`/canvas/` dispatch `agent-bridge.ts:541` → route switch `:2185`, cases `terminal :2194 / browser :2211 / editor :2297 / media :2312`), and ours goes in a second pane beside it. The A/B is on screen, not in an agent's imagination | `apps/desktop/src/litesuite/services/agent-bridge.ts` |
| Artifacts shown | `POST /editor/open` (`agent-bridge.ts:274`) for code, browser panes for the built page and progress page, the `media` canvas case for renders | same file |
| Image/video generation | the `image` tool → LiteImage REST `http://127.0.0.1:7426` (`_litemcp_vendor/config.py:45` IMAGE_API_URL) → `POST /generate` (`LiteImage/src/main/services/api-server.ts:209`), base64 image in the response (`:262`). ⚠️ License-gated + runtime-provisioned: routes 503 until sd.cpp/CUDA are on disk. The bridge's `POST /v1/image/generate` is a DIFFERENT, Codex-backed surface — prefer the `image` tool | vendored handler `image.py:405` |
| Audio generation | the `sound` tool → LiteSound REST `http://127.0.0.1:7427` (`sound.py:26` LITESOUND_API_URL), async `POST /generate/{mode}` → poll job (`sound.py:240`) | `litesuite_tools/tools/sound.py` |
| 3D generation | the `model` tool — **LiteModeler has NO HTTP server** (`model.py:174`): it spawns `litemodeler-cli.mjs`, located via `LITEMODELER_CLI` / `LITEMODELER_ROOT` / dev checkout / installed copy (`model.py:224`) → `.glb` out | `litesuite_tools/tools/model.py` |
| Progress surface | the kanban strip IS the live progress page (IPC-push, no polling); add a browser pane with a summary page for the human when the work is visual | 05-LiteHarness → Real-time Sync |

`liteharness spawn` flags, from the parser itself (never `--help` — unknown flags fall through silently): `--model --cwd --worktree --permission-mode --prompt --name --tier --team --pty --new-window --split --pane --direction --exec --args --thread-id --workspace-id --project-id`.

### Tool registry coverage — measured, not vibes

The `litesuite-tools` registry holds **36 tools** (count derived from `NAME` exports in `litesuite_tools/tools/*.py`; a module is a tool iff it exports `NAME` + `execute`). The gauntlet's relationship to each, so "ties into LiteSuite systems" is a checkable claim:

- **Drives (12):** `spawn` `tasks` `inbox` `terminal` `browser` `editor` `image` `sound` `model` `environment` `pattern` (record the run's outcome) `render_widget` (progress widget in Frontier Chat when running there).
- **Available to builders as ordinary tools (12):** `shell` `file_io` `sandbox` `web_fetch` `web_search` `youtube` `rag` `repo_intel` `project_state` `lens` (summarise long outputs) `memory` `lcm`.
- **Deliberately NOT used (12):** `pccontrol` (armed-flag desktop automation — a gauntlet must never need the human's desktop), `halt`/`reassign`/`inject` (orchestrator-tier interventions, not loop mechanics), `evolution` `bench` `credit` `agent` `chronicle` `vault` `youtube`-adjacent `prompt_widget` (blocks on human input — the gauntlet's human gate is `/goal`, not a modal), `ui_render` (harness-MCP compat path; `render_widget` is the current surface).

### Fullest-potential adjudication (Ryan's bar, 2026-08-22) — adopt with wiring, or reject with reasons from code

| System | Disposition | Wiring / reason |
|---|---|---|
| **Collective memory (`pattern`)** | **ADOPT — mandatory** | Before round one: `lst run pattern action=query query="<goal>"` — a run that skips this starts amnesiac. After every round: `action=record` the builder approach + critic angle + outcome, with `supersedes=` when a later round retires an earlier finding (supersession must be written at record time — it cannot be reconstructed from timestamps). A 34-hour grind's most valuable output is which approaches beat the bar. |
| **Multi-model critics** | **ADOPT** | `liteharness spawn --model <m>` (flag verified in the spawn parser) — a critic on a DIFFERENT model is structurally more independent than fresh-context-same-model. Spawn at least one critic seat on another provider when the run is long enough to matter. |
| **LiteBench arena as the judge** | **ADOPT as optional mode** | When the artifact is a game/web build and LiteBench is installed: report completion the arena way — `liteharness send litebench-arena "BENCH_COMPLETE competitor=<tag> …"` — and let human-pick → ELO judge (`LiteBench/src/main/engine/{litebench-inbox,cli-competitor-runner,battle-orchestrator}.ts`). ELO is comparative, never self-graded, which is this skill's own score-drift objection solved by an existing system. |
| **Bar fetchers, named** | **ADOPT** | Page bars: `browser` tool `screenshot` action (`agent-bridge.ts:469` route, `:1002` case) — both sides at the SAME viewport. Footage bars: `youtube` tool / `/ls-youtube-transcript` for the reference title. Repo bars: clone and run the named repo's suite. The fetcher named per bar is what keeps "fetchable" from decaying into prose. |
| **HITL by name (`halt`)** | **ADOPT** | "The human pulls the plug" has a tool: `lst run halt` (`halt`/`resume`/`status`). The lead checks `status` between rounds; a `halt` is the human gate closing mid-grind, distinct from `/goal` clearing. |
| **Compression (`lens`)** | **ADOPT** | Critic analyses are long; pipe them through `lens` (`tools/lens.py:7`, local-model summarisation) before they enter the lead's context. Verdict + biggest gap travel whole; the analysis travels summarised. |
| **Voice on gate-clear** | **ADOPT** | `POST http://127.0.0.1:7438/v1/tts/speak` (`voice/api-server.ts:435`) — one sentence when the gate clears or a `halt` lands. Fire-and-forget by contract. |
| **Per-piece durable state** | **ADOPT** | `memory`/`rag` tools hold per-piece state that survives a session — bar location, rounds so far, standing verdicts — so a resumed gauntlet re-enters instead of restarting. |
| **`evolution` cross-wire** | **REJECT, with the reason from its code** | `MUTATION_TARGETS` (`liteagent/evolution/targets.py:116` — `identity_soul :121`, `identity_heartbeat :134`, …) mutate the AGENT and bench with variance calibration: its unit of selection is the agent's configuration. A gauntlet round selects on the ARTIFACT against a fixed bar. Cross-wiring conflates two objects of selection; the legitimate join already exists — gauntlet outcomes recorded as patterns (row 1), which evolution's benching may later consume. |
| **GlassBox telemetry** | **BLOCKED — symbol not found** | `GlassBoxBrain` has **zero hits** in LiteSuite develop @ `6cd49ac5` (only the Glass Box *Token Inspector* data contract exists). Adopt the moment a real symbol lands; a skill citing a tool that does not exist is a dead pointer at birth. |

### Paths, git, and worktrees — prereqs every spawned builder inherits

- **Structure.** Apps live at `C:\Projects\<app>`. Quick scripts go to `<root>/scripts`, E2E tests and their artifacts to `<root>/e2e/` (always `bail=1`), temp files to the session scratchpad — never `/tmp`, because a POSIX path handed to a Windows writer forks the file into `C:\tmp` while reporting success.
- **Toolchains.** `pnpm` everywhere except LiteSuite and LiteEditor, which use Bun. `python`, never `python3`.
- **Git.** Gitflow: `master` / `develop` / `feature/*` / `hotfix/*` / `release` already exist — day-to-day work goes on `develop`, and nobody invents new branches. Builders commit only inside their own worktree branch; the lead merges. Trailers on every commit: `Task-id` / `Agent-Tier` / `Agent-Name` / `Agent-ID` / `Complexity`. **Never `Co-Authored-By`.** 🔴 **The push is Ryan's trigger, always** — and litesuite.dev deploys on push, so an unauthorized push there is an unauthorized deploy.
- **Worktrees.** Parallel builders get isolated worktrees under `<root>\.worktrees\`. 🔴 **Before ANY worktree removal, scan for junctions** — `git worktree remove --force` FOLLOWS Windows junctions and has already destroyed 264 GB of models here. This workspace junctions `bin/`, `node_modules/`, `lite-ui` into worktrees BY DESIGN, and a suspiciously small worktree is a junction tell. A refused non-force remove is a warning to investigate, never a license to escalate:
  ```powershell
  Get-ChildItem <worktree> -Recurse -Depth 3 -Force | Where-Object { $_.LinkType } | Select FullName, LinkType, Target
  ```
- **Processes.** Spawned agents and any app they launch run detached, never attached to a console. Count inbox consumers for your id before starting a watcher, never after.

**Bridge down / no LiteSuite running?** Degrade honestly: Agent-tool subagents, artifacts as files on disk, verdicts in the transcript — and say that is what happened. A pane that was never opened is not a progress surface that "worked anyway."

## Output template

Adapt the wording every time. First the gate, then the prompt.

```
/goal the ASSEMBLED result passed its own gauntlet — whole-critic and bar-judge both — [and MEASURABLE is met], or an honest-exhaustion report exists
```

```
Build [GOAL].

The bar is [BAR]. Fetch the real thing first and compare against it directly, never
against a description or a memory of it. If the bar cannot be obtained, stop and say so.

First figure out what great actually looks like from the examples and these rules:
[RULES/LIMITS]. Then break the goal into connected pieces — the joins are part of the
decomposition — and give each piece to a specialist builder.

For each piece: first experience it the way the user will. Then a separate critic finds
the flaws — no bar, just what is wrong. Then ANOTHER reviewer compares it with the real
examples. Both get everything — output, code, reasoning — and must measure their own
side: run it themselves, screenshot both at the same viewport, fetch the bar themselves.
A claim they did not check is not evidence. Verdicts are binary — ours or the bar, plus
the single biggest gap — or UNEVALUABLE with the reason. No scores. Praise is not useful.
A piece that passes is kept WITH its evidence saved.

Then run the same gauntlet on the WHOLE: assemble the accepted pieces, test the complete
result, a separate critic hunts problems, another reviewer compares the whole against
the examples. If one piece fails, find the piece or connection that caused it and rework
that — never restart everything. If the plan itself failed, go back to what great looks
like and re-derive it. If time or budget runs out, stop honestly and say why.

/loop until the gate clears. Done means finished WITH PROOF.

Run this on LiteSuite: spawn the builders and critics as liteharness terminal agents,
drive every piece through the kanban so I can watch the columns move, send verdicts by
inbox, and put the artifacts where I can see them — the bar and ours side by side in
browser panes, code in the editor, renders in the media pane. Generate images through
LiteImage, audio through LiteSound, 3D through LiteModeler — not by hand. Builders work
in isolated worktrees on their own branches; nothing gets pushed — that trigger is mine.

Keep a live progress surface updating as the work evolves so I can watch it — it is
also how I tell a grinding loop from a dead one.

Fan out subagents and ultracode.
```

Fill-in rules: bake the bar in as a concrete fetchable thing (URL, product, repo, title). Add a budget line only if the user named one. Add tool names only if the goal needs them. Drop the LiteSuite paragraph only when the prompt is destined for a machine without LiteSuite. Everything else stays out — no architecture, no file layout, no round count, no stack choice unless the user demanded it. Every extra instruction is one fewer decision the agent makes with its own judgment.

## Length and voice

The prompt block stays around 200 words with the LiteSuite paragraph, 120–180 without it — plain sentences, no bullets, no headings. It should read like someone telling an agent what perfect looks like and refusing to accept less.

## Portability

`/goal`, `/loop`, and `ultracode` are Claude Code features; the spawn/kanban/panes layer is LiteSuite. For any other agent, drop the LiteSuite paragraph and replace the gate and loop lines with: "Keep looping until every critic picks ours. Run the builders and critics as parallel subagents with fresh context." The structure carries unchanged; the enforcement becomes best-effort.

## What breaks a gauntlet

- **A vague bar.** The critic invents the comparison and approves everything. Most common failure by far. The UNEVALUABLE rule is the cure: no bar in hand, no verdict.
- **The builder grading its own work.** Separate agent, fresh context, always.
- **Blinding mistaken for rigour.** A blind critic that endorses the builder's claim without measuring is still sympathetic — it just cannot explain why. Required measurement catches what withheld context never will.
- **Scores.** They drift upward every round. Binary verdict plus biggest gap.
- **A round-count exit.** The published runs are honest here: one went 34 hours and 251 sub-agents and the critics were STILL rejecting when the human stopped it. The last stretch is human — the gate ends the loop, or you do.
- **Silence read as progress.** A loop whose consumer died looks identical to one that is grinding. The kanban and the progress pane are the liveness instruments, not decoration — no fresh movement, assume dead, and check.
- **Per-piece green, unowned joins.** Every piece passed and the whole is inert — the reason the outer gauntlet exists. Decompose into CONNECTED pieces and test the assembly as hard as any piece.
- **One critic wearing two hats.** The flaw-finder and the bar-judge ask different questions; merged, the flaws the bar never tests go unfound.
- **Exhaustion dressed as success.** Out of time is a terminal with a REPORT, never a quiet stop — and never a reason to soften the last verdict.
- **Over-specifying.** Minimal wins.
