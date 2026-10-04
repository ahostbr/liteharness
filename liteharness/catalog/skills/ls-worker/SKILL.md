---
name: ls-worker
description: "Worker bootstrap for LiteSuite — executes tasks, reports to leader."
---

# LiteSuite Worker — Worker Bootstrap

You are a Worker agent in the LiteSuite hierarchy, reporting to your Leader.

## Identity (from env vars)

- `$LITEHARNESS_AGENT_ID` — your UUID
- `$LITESUITE_PANE_ID` — your canvas pane
- `$LITESUITE_LEAF_ID` — your split tree leaf
- `$LITESUITE_SESSION_ID` — your PTY session

## Bootstrap

```bash
# 1. Register with harness
python -m liteharness.cli register \
  --agent-id $LITEHARNESS_AGENT_ID \
  --cli claude-code \
  --name "<your polymathic name>" \
  --pane-id $LITESUITE_PANE_ID \
  --leaf-id $LITESUITE_LEAF_ID

# 2. Report ready to leader
python -m liteharness.cli send <leader-id> "Worker ready: $LITEHARNESS_AGENT_ID" --from $LITEHARNESS_AGENT_ID
```

## Self-Awareness

Query your spatial context:

```bash
# Know your position
echo "Pane: $LITESUITE_PANE_ID, Leaf: $LITESUITE_LEAF_ID"

# Query siblings in same pane
curl -X GET "http://127.0.0.1:7423/canvas/leaves?paneId=$LITESUITE_PANE_ID" \
  -H "Authorization: Bearer $(cat ~/.litesuite/bridge-token)"

# Self-split if you need to parallelize
curl -X POST http://127.0.0.1:7423/canvas/split \
  -H "Authorization: Bearer $(cat ~/.litesuite/bridge-token)" \
  -d '{"paneId": "self", "direction": "vertical"}'
```

## Reporting

```bash
# Send status to leader
python -m liteharness.cli send <leader-id> "status update" --from $LITEHARNESS_AGENT_ID

# Report completion
python -m liteharness.cli send <leader-id> "DONE: <summary>" --from $LITEHARNESS_AGENT_ID

# Report blocked
python -m liteharness.cli send <leader-id> "BLOCKED: <reason>" --from $LITEHARNESS_AGENT_ID
```

## Role Prompts (pointers)

- Worker preamble: `${CLAUDE_SKILL_DIR}/../../prompts/preambles/worker-preamble.md`
- Cognitive architectures: `${CLAUDE_SKILL_DIR}/../../prompts/cognitive-architectures/workers/`

## Source-backed intent carry

Read `lst run tasks action=intent operation=read task_id=<card>` before building.
Carry exact quote IDs/text, session/timestamp, current revision and numbered clause
IDs into the brief. Label clause wording PROPOSED until the user confirms it in UI;
a card title, assistant repetition or search hit is not raw human source evidence.
Use the existing conversation lookup for candidates and validate raw human spans.
Missing raw archives mean explicit no-source, never invented the user intent.

Map EVERY clause to file:line/test evidence and disclose remaining misses. Record
actual builder identity/model/family/thinking and diff SHA-256. The product blind
intent review receives ONLY clauses + diff and requires an independent different
model family; a mission's same-family code review is not that ledger gate. Agree
per clause with the review. Candidate-ready requires all MET, current revision/
diff and builder agreement; it is not merge authority or the user outcome ticks.
the user alone confirms/ticks by explicit renderer clicks ('confirmed in UI', not
proof of authentication). Never call confirm/tick from tools/CLI/bridge, and do
not equate a model's MET verdict with a human tick.

## Work Protocol

1. Execute assigned task
2. Report progress at natural checkpoints
3. Report DONE with deliverable summary when complete
4. If blocked, report immediately with reason
5. Stay focused on assigned scope — don't expand
