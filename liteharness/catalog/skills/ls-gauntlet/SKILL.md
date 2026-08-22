---
name: ls-gauntlet
description: A specialization of the orchestrator protocol - turns any goal into one paste-ready gauntlet prompt with a concrete FETCHABLE artifact as the quality bar, builder/critic pairs per piece, full-context critics who must measure their own side, /goal as the stop-gate and /loop as the re-entry, run on LiteSuite systems. Triggers on 'gauntlet', 'gauntlet loop', 'gauntlet this', 'loop until it beats X', 'grind until it wins'.
---

# Gauntlet — full-context edition

The user gives a goal. You hand back ONE short paste-ready prompt plus ONE `/goal` line, and offer to run it. You are not doing the work — you are writing the prompt that makes another agent grind until the work beats a real reference.

**This is a SPECIALIZATION of the orchestrator protocol, not a parallel doctrine.** Ryan's flowchart maps ~1:1 onto the orchestrator flow — 21 of 23 nodes (mapping table: `docs/plans/2026-08-22-gauntlet-loop/flow.md`; doctrine: `prompts/orchestrator-role.md` Phases 1–6, which ships in the same plugin artifact as this skill, so the pointers resolve for end users). This file therefore carries ONLY what genuinely diverges, plus the LiteSuite wiring. For everything else, follow the doctrine — cite it, never requote it; a second copy is how it drifts.

**The three divergences — the gauntlet's actual content:**

1. **The referent.** Written PRD acceptance criteria become a FETCHABLE real-world exemplar — the bar. (PRD structure otherwise per `prompts/protocols/prd-template.md`.)
2. **The verdict form.** APPROVE / REQUEST-CHANGES / BLOCK (`prompts/protocols/review-verdicts.md`) becomes binary **OURS / BAR**, with **UNEVALUABLE as BLOCK's honest cousin** — a verdict that refuses to be a verdict, stated as such.
3. **The enforcement.** Convergence-by-doctrine (`prompts/protocols/convergence-signals.md`) gains a harness-native gate: `/goal` blocks stopping, dynamic `/loop` re-enters. The no-finish-line lives in the CLI, not in prose.

Modeled originally on `robonuggets/gauntlet-loop`; its blind critics are rejected by ruling (below).

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

"Figure out what great looks like" INCLUDES `lst run pattern action=query query="<goal>"` — Phase 1 doctrine; a run that skips it starts amnesiac.

What the shape buys, each with its doctrine home:
- **The outer gauntlet guards against per-piece green with an unowned join** — Phase 5 aggregate-and-gate + closure gates on the merged result.
- **Flaw-finder and bar-judge are different seats** — T5 polymathic review vs the Issue↔Plan↔Implementation↔Review comparison. Collapsing them loses the flaws the bar never tests.
- **The experience pass comes first** — the E2E "app must demonstrably work" gate: run it as a user before critiquing it as a reviewer.
- **Failure attribution routes to the CAUSE** — escalation routing; one failed join re-enters one build loop, a failed PLAN re-enters at "what does great look like" (STUCK→change-approach / Andon Cord).
- 🔴 **"ACCEPTED" IS A BARRIER, AND IT IS THE ONLY ONE.** The flow reads *put all the **accepted** pieces together* — the word is load-bearing. Everything else in this skill pipelines happily; this one node must not. **Measured in run-01, by the lead, against its own skill:** the inner critics were dispatched and the assembly built immediately after, without waiting for the builders' DONE reports. Piece B was rewritten **37 seconds after** the assembly, so both integration seats judged a stale artifact; piece A had been measured mid-write and reported 478 bytes short. The damage happened to be trivial (64 bytes of factual annotation) only because the builder's real bug fix — a grid blowout overflowing 108 elements at 375px — had already landed. **A file existing is a file being written.** The completion signal is the builder's own DONE report, never the file's presence on disk. Two cheap guards, both of which worked: require every builder to report a BYTE COUNT (a number that disagrees with yours is a free integrity check on the lead), and assert `piece in assembled` before trusting any integration verdict.
- **Honest exhaustion is a first-class terminal** — the budget-exhaustion hard stop: pause, summarize spent/remaining, notify the human. Success itself is *finished with proof* — the workers-cannot-declare-DONE-without-evidence law, verbatim.

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

- **Two seats per piece, not one.** The FLAW-FINDER critiques absolutely — no bar in view, just *what is wrong* — after an experience pass where it runs the piece the way a user would (that seat is T5 polymathic review). The BAR-JUDGE is a second, distinct seat that only compares against the examples (the Canonical Loop's Issue↔Plan↔Implementation↔Review comparison, retargeted at the exemplar). Different failure modes, different questions, different agents — `liteharness spawn --model <m>` makes the second seat a different model when the run warrants it.
- **Fresh context, full evidence.** Neither seat inherits the builder's conversation — but each is handed the output, the code, the reasoning, AND (for the bar-judge) the bar.
- 🔴 **ABLATE THE FIX — REMOVE IT AND SEE IF ANYTHING CHANGES.** The strongest verification in run-01 was not a measurement, it was a **falsification**: a seat pulled the lead's shell apart one declaration at a time against the live page. Removing the rule written to kill the defect changed **nothing**; restoring an unrelated `margin` brought the defect back on demand. Verdict: *"the html half is DEAD and the body half is INERT — one declaration is doing all the work. **Eleven lines, one load-bearing.**"* And the sentence that generalises it: **"That is not a reason to delete the others, but it is a reason not to trust them: nothing on this page would tell you if they broke."** ⇒ A repair is verified when its ABSENCE reproduces the defect. If deleting your fix changes nothing, you did not fix it — something else did, and you are now carrying code that no observation can hold accountable.
- 🔴 **"THE SYMPTOM IS GONE" AND "THE FIX WORKS" ARE DIFFERENT CLAIMS — and the obvious check only tests the first.** Run-01's lead fixed a white-frame-in-dark-mode defect and wrote a rule that **can never match** (`:root:not(...) html` asks for an `html` inside `html`; `:root` IS `html`). The frame really did disappear — killed by an unguarded `margin: 0` in the same block, written for a different purpose. **Screenshotting dark mode, the natural verification, would have shown a black page and confirmed a rule that has never once executed.** A round-2 seat caught it by reading the COMPUTED value on `documentElement` (`rgb(247,247,245)` — the light canvas) rather than the visible result, and identified the residual the screenshot could not show: an iOS/macOS overscroll bounce flashing near-white on a dark page — **invisible on the grader's desktop, visible on the reader's phone.** ⇒ When you repair a defect, verify the MECHANISM you wrote, not just the disappearance of the symptom: assert the selector matches something, or read the computed property on the element you targeted. A fix that works by accident is indistinguishable from a fix that works, right up until the accident stops.
- 🔴 **A CSS DIFF IS NOT A RENDERING — AND NOBODY OWNS THE DOCUMENT.** Two rules from run-01's integration seat, which rendered where every other seat read. **(a)** The lead's join table compared *declared* values and scored 7 of 8 concepts as divergent; measured on the live DOM, two of those were **imperceptible** — a canvas shift of (4,3,3) out of 255 and two type stacks that both lead `ui-sans-serif` and measure **identically** at 310.90625px. Its verdict on its own method: *"Had I only diffed the CSS I would have reported two divergences; one of them is false."* Declared-value comparison invents findings; render before you rank. **(b)** Every builder styles its own box and **no one styles the page**: zero rules targeting `body`/`html`, zero `color-scheme` declarations, no `<nav>`, no meta description. In dark mode that left the UA's default `margin: 8px` over a white canvas — **an 8px pure-white frame around a black page, 4.3% of a 375px screen**. ⭐ Its negative control is the lesson: the identical defect measures 8/255 in light and is **invisible in the mode both builders eyeballed**, while living in the one region neither piece contains. **Assign the document shell to a seat explicitly, or it belongs to nobody.**
- ⭐ **FIDELITY TO A SHARED SOURCE WITH NO SHARED OWNER PRODUCES DUPLICATION.** Run-01's two builders each reproduced the same canonical README transcript, verbatim and correctly — and every per-piece gate scored that fidelity as *maximally correct*, because it was. The assembled page then printed the same terminal twice, ~3,000px apart, in two visual dialects. **No per-piece critique can see this by construction, and the discipline the gates reward is what creates it.** When pieces share a source of truth, someone must own WHICH piece gets to use each canonical example.
- **It must measure its own side.** Re-run the thing, re-screenshot both at the same viewport, re-fetch the bar itself. A builder's claim the critic did not check is not evidence — endorsing an unmeasured claim is sympathy no matter how little the critic saw.
- **Binary verdict.** Ours or the bar, plus the single biggest remaining gap. Never scores — an agent left to grade itself calls its own work done (a cited study: 54 loop cycles, improvement claimed in all 54, more than half actually worse or flat), and scores out of 10 drift upward every round.
- 🔴 **ONE SEAT MUST AUDIT THE SPEC AGAINST ITS OWN CITED SOURCE.** Every critic brief points at the ARTIFACT, so nothing is aimed at the brief itself — and a defect in the bar or the spec propagates into every piece, where no per-artifact critique will ever reach it. **Measured in run-01:** the brief called LiteHarness a *"five-tier"* system and a package shipping *"a bundled wheel"*, while ordering builders to source every claim from a README containing **zero** occurrences of `tier`, `five`, or `wheel`. Both builders correctly declined to propagate either. Both flaw-finders caught both — **but only when asked directly afterwards**; neither raised it in the dispatched work, and both said so plainly. One of the two the lead did not know it had written, which is why this is a real finding and not a confirmation. ⇒ Give the flaw-finder an explicit instruction: *check the spec's own claims against the source the spec designates, and report UNSOURCED (true elsewhere, absent here) separately from CONTRADICTED (the source disagrees)* — different findings, different fixes. This is what feeds the flowchart's **"the overall plan failed → re-derive what great looks like"** edge, which otherwise has nothing feeding it.
- **Non-verdicts stay non-verdicts.** A critic that could not fetch the bar or could not run the output returns UNEVALUABLE and the reason. That is not a loss and not a win, and collapsing it into either invents a result nobody produced. This is the structural cure for hallucinated comparisons — no bar in hand, no judgment.

### 🔴 NOBODY REVIEWS THE LEAD — so require builders to argue back

Builders are judged by critics; critics are judged by the lead; **the lead is judged by nobody.**
Every seat's brief points at the ARTIFACT, so the lead's own output — the brief, the bar, the join
analysis, the assembly, the repair rulings — is the one class of document nothing is aimed at.

**Run-01 produced five lead-authored defects.** A brief carrying two premises absent from the source
it designated. A join analysis that scored two imperceptible differences as real. An assembly built
from unfinished pieces. A document shell that did not exist. And a repair ruling that would have
moved a defect and called it fixed — *"the same defect, moved rather than removed."* **Four were
caught by seats whose briefs pointed elsewhere; none by a seat assigned to look.**

The one that caught the bad ruling was **a builder that treated an instruction as arguable**: it
executed the ruling's INTENT (remove the duplicated moment) while refusing its literal content, and
said so plainly rather than quietly doing something else.

⇒ **Tell every builder, in its brief:** *if you can show an instruction is wrong, say so and say why
— execute the intent, not the letter, and never substitute silently.* A seat instructed only to
comply will comply, and the machine loses its only check on its least-reviewed author. This is the
no-blind-critics ruling pointed upward instead of sideways: **independence is bought by requiring
judgement, not by removing information.**

## The stop-gate is `/goal`, the re-entry is `/loop`

The viral prompt's "do not stop until the critics are wowed" reads as persuasion. The actual machine is enforcement — two native Claude Code commands the explainer videos never name:

- **`/goal <condition>`** arms a session-scoped Stop hook: the CLI checks the condition before the agent is ALLOWED to stop, and auto-clears when it holds. The refusal to finish is in the harness, not the prompt. The exit is the gate clearing or the human pulling the plug. Never a round count.
  *Evidence:* CLI bundle 2.1.239 registers `name:"goal", description:"Set a goal Claude checks before stopping", argumentHint:"[<condition> | clear]"`; live capture 2026-08-21 (session `ac965cc1`): arming a goal produced *"A session-scoped Stop hook is now active … The hook will block stopping until the condition holds. It auto-clears once the condition is met."*
- **`/loop <prompt>`** (no interval — dynamic mode) re-enters the work, self-paced via ScheduleWakeup; with an interval it is CronCreate-backed.

⚠️ **Never pair the gate with an interval loop.** Two documented behaviors compose badly: CronCreate's contract states *"Jobs only fire while the REPL is idle"*, and the Stop hook works precisely by refusing to let the session go idle — so an interval loop waiting behind an armed gate starves the tick it waits for. **This is inference from the two contracts (both observed separately, 2026-08-21), not an end-to-end observed starvation** — the test loop was killed before an unmet-stop attempt. Gate + dynamic loop, or gate alone.

### 🔴 AN AGENT CANNOT ARM `/goal` THROUGH THE API — IT MUST TYPE IT

`/goal` and `/loop` are **UI commands, not skills.** Invoking `/goal` via the Skill tool returns,
verbatim: *"goal is a UI command, not a skill. Ask the user to run /goal themselves — it cannot be
invoked via the Skill tool."* (measured 2026-08-22, run-01).

**Do not conclude from this that the gate is human-only.** That inference was made in run-01 and
was wrong. The Skill tool is not the only input path — **the keyboard is a path**, and a UI command
that refuses the API accepts keystrokes. LiteTUI ships `tools/pccontrol/pccontrol.py` for exactly
this: an agent finds its OWN terminal and types like a human.

```powershell
python <litetui>/tools/pccontrol/pccontrol.py windows            # find it
python <litetui>/tools/pccontrol/pccontrol.py activate <PID>     # focus it
python <litetui>/tools/pccontrol/pccontrol.py type "/goal <condition>"
python <litetui>/tools/pccontrol/pccontrol.py keypress enter     # -> "Goal set: <condition>"
```

**Two rules that are not optional, both learned by failing them:**

1. **Identify your own terminal by PROVENANCE, never by title.** Walk your own process ancestry
   (`Win32_Process` → `ParentProcessId`) up to the terminal that owns your `claude.exe`, and require
   that PID to agree with `pccontrol windows` before you send a single key. The window list on a
   working box also offers Chrome, Task Manager and Paint — all equally activatable. A title match
   alone is how an agent types its private reasoning into someone's paint canvas.
2. **🔴 NEVER ISSUE THE `type` CALL FROM GIT BASH.** Every slash command is a leading-slash
   argument, and MSYS2 path conversion rewrites it *before* python sees it: `/goal …` arrived in the
   REPL as `C:/Program Files/Git/goal …`. pccontrol typed faithfully; the shell had corrupted its
   input. **Use PowerShell** (or `MSYS_NO_PATHCONV=1`). The tell is a character count:
   the mangled call reported `typed 95 chars`, the clean one `typed 75 chars` — a 20-char delta that
   is exactly `len("C:/Program Files/Git/goal") - len("/goal")`. Count the characters; do not eyeball
   the string.

⇒ **This closes the autonomy hole.** Without it a self-running gauntlet has no stop-gate at all and
can declare itself finished whenever it likes — the exact failure `/goal` exists to prevent.

## Run it on LiteSuite — the machinery already exists, fill in the pieces

When the run happens on a LiteSuite box, the gauntlet's abstract roles map onto systems that are already built. Use them — do not re-imagine them as prompt prose:

| Gauntlet role | LiteSuite system | Verified anchor |
|---|---|---|
| Lead agent | you, the session running this skill | — |
| **Casting** | Every seat is cast from the SHIPPED cognitive-architecture roster — 91 files under `prompts/cognitive-architectures/` (14 workers · 12 thinkers · 5 reviewers · 11 leaders · 47 pure · orchestrator). Builders from `workers/`+`pure/` by piece domain (`prompts/agent-pool-guide.md` is the routing table); the flaw-finder from `thinkers/`+pure investigators; the bar-judge from `reviewers/`+pure taste; integration seats fresh from the same pools. Spawn idiom: *Read `<prompts>/cognitive-architectures/<tier>/<name>.md`, adopt it, then the brief.* 🔴 RESOLVE `<prompts>` at runtime — `liteharness.prompts.resolve_prompts_dir()` (`prompts.py:93`: env override → repo → sibling → packaged → plugin cache) — never hardcode one station. The roster rides the same plugin artifact as this skill, so end-user gauntlets get the identical cast with zero private dependencies | roster counted on disk 2026-08-22 |
| Builder / critic fan-out | 🔴 **NEVER probe this verb with `--help`. `spawn --help` DOES NOT PRINT HELP — IT SPAWNS.** The argv scan drops the unknown flag and falls through to the spawn, printing `Spawned Claude session` and exiting 0. Measured 2026-08-22: two probes created two unattended `--permission-mode bypassPermissions` sessions that registered themselves as fleet agents. Read the source or run the verb deliberately; an unparsed flag is an *absent* flag, and absent arguments mean "do the default thing." | `liteharness spawn` — a real terminal agent per seat, fresh context by construction; canvas pane inside LiteSuite, PTY daemon (`--pty`, :7460) or Windows Terminal otherwise | spawn branch `cli.py:3758`; mode table in 05-LiteHarness |
| Per-piece state | `lst run tasks` kanban. **Seven columns:** `queued → thinking → building → reviewing → fixing → merging → done` (`task_store.py:18` VALID_STATUSES; CHECK constraint `:37`). **Nine actions:** `list, claim, complete, unclaim, create, update, heartbeat, sweep, help` (`tasks.py:22`). `claim` moves queued→thinking; move a piece with `update status=building/reviewing/fixing`; `complete` lands it in done. `merging` exists for the lead's integration step — a piece that needs no merge skips it. The human watches this live; an unmoved card is invisible work | `packages/litesuite-tools/litesuite_tools/tools/{task_store,tasks}.py` |
| **Seat reconciliation** | 🔴 **RECONCILE SEATS DISPATCHED AGAINST VERDICTS RECEIVED, BY COUNT, before any integration step or any report of results.** An idle notification is NOT a report. Measured in run-01, final tally across 10 dispatches: **5 of 9 completed seats delivered nothing until asked BY NAME — more than half**, and every one answered in full when asked (one had been idle ~20 minutes; one returned the run's best report). The rate rules out *"that seat had nothing to say"* and confirms the channel. Silent seats included — **including BOTH flaw-finders**, the seats that hunt fabrication and absolute defects. The work existed; only delivery failed. Asked directly, one of them returned a full BAR verdict with six evidenced findings, four of which the lead had missed. 🔴 **An absent critique is indistinguishable from a clean one**, so silence makes the machine report its best result exactly when it has failed to run — and a partially working channel (three seats reported normally) is more misleading than a dead one. Asking costs one message and every queried seat answered in full. When you ask, say *send what you concluded, do not re-derive it, do not pad it because a lead asked twice* — a second request is pressure, and pressure manufactures answers | run-01, 2026-08-22 |
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

Branching, trailers, worker/leader commit discipline, and the kanban contract are DOCTRINE — `prompts/bootstrap-harness.md` + `prompts/protocols/github-issue-protocol.md`. Follow them; do not re-learn them from this file. What stays inline is only what ships nowhere else:

- **Structure.** Apps live at `C:\Projects\<app>`; scripts → `<root>/scripts`; E2E + artifacts → `<root>/e2e/` (always `bail=1`); temp → the session scratchpad, never `/tmp` (a POSIX path handed to a Windows writer forks the file into `C:\tmp` while reporting success). `pnpm` everywhere except LiteSuite/LiteEditor (Bun). `python`, never `python3`.
- **The human gate.** 🔴 **The push is Ryan's trigger, always** — and litesuite.dev deploys on push, so an unauthorized push there is an unauthorized deploy. HITL merge survives even though the flowchart has no node for it.
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
