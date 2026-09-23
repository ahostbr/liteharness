---
name: ls-local-batch-agent
description: Spawn isolated, headless LiteTUI local-model agents for evidence-only batch judgments with a polymathic preamble. Use for branch censuses, dossiers, or similar repeated read-only tasks.
---

# Local batch agent

Use this when many independent items need the same local-model judgment. The shipped runner uses LiteTUI's existing `--rpc --backend lmstudio --server-mode connect` path, one isolated conversation per item. It does not implement another model transport. Evidence is collected by the host with restricted, read-only Git commands; the LiteTUI seat has `tools_enabled: false` in a temporary `LITETUI_DATA_ROOT`. The model cannot call file, shell, network, or LiteHarness tools. It returns only structured judgments; the orchestrator owns interpretation and any action.

1. Choose a polymathic **method** file suited to the question, such as `prompts/cognitive-architectures/thinkers/holmes.md` in this plugin. Supply its absolute path as `preamble`; the runner uses its leading 6,000 characters so Windows can pass it as a launch argument. The operational Tier 1 preamble and human authority remain with the orchestrator. Do not use a worker method that asks the model to execute actions.
2. Write a JSON manifest like the example below with `task`, `repo`, `preamble`, `items`, `evidence_argv`, `output_schema`, and `required_keys`. Each `{item}` is substituted as one argv element, never through a shell. Every evidence command must start `git -C {repo}` and use an allowed read-only verb. For other evidence sources, collect and review them separately; do not add arbitrary shell execution to this runner.
3. Run `lms ps --json` first. Reuse an already-loaded suitable model and name its exact `identifier` with `--model`. **Never load or swap a model without explicit approval from Sentinel through LiteHarness inbox.** If none is resident, stop and send that approval request. The runner itself never loads or unloads a model. If Sentinel approves a load, record that this invocation owns it, load only the approved model/context, and unload only that owned model after the batch. Never unload a model that was resident before this job, and never use `unload --all`. Check real VRAM with `nvidia-smi` before any approved load; `lms ps` size excludes KV cache.
4. Run `python <skill-dir>/run_batch.py <manifest.json> <out.json> --model <resident-id> --litetui-exe <path-to-litetui.exe>`. An installed `litetui` on PATH needs no final flag. The runner refuses an absent model, blocked LiteTUI launch, missing/invalid JSON, or missing required keys. It writes the output atomically only after every item succeeds. It grants no tools, sets a generous response budget (default 24,000), and refuses reasoning-only empty output.
5. Review the JSON and source evidence. Model verdicts are advisory; verify before any merge, deletion, or other external change. Report actual model, item count, output path, and incomplete/blocked items. Do not present a generated judgment as a tested fact.

Example manifest for branch triage:

```json
{
  "task": "Judge whether each unmerged branch is live, held, orphaned, or superseded. State uncertainty.",
  "repo": "<repo>",
  "preamble": "<plugin-dir>/prompts/cognitive-architectures/thinkers/holmes.md",
  "evidence_argv": [
    ["git", "-C", "{repo}", "log", "-1", "--format=fuller", "{item}"],
    ["git", "-C", "{repo}", "diff", "--shortstat", "develop...{item}"],
    ["git", "-C", "{repo}", "cherry", "develop", "{item}"]
  ],
  "output_schema": "{\"state\": \"LIVE|HELD|ORPHANED|SUPERSEDED|UNKNOWN\", \"reason\": \"one evidence-grounded sentence\"}",
  "required_keys": ["state", "reason"],
  "max_tokens": 24000,
  "items": [{"id": "feat/example"}, {"id": "fix/example"}]
}
```

For a dossier, give an item its own `evidence_argv` list, use a richer JSON `output_schema`, and set `required_keys` to its mandatory fields. The existing one-off branch census/dossier scripts in your own scripts directory are examples of evidence selection and Holmes wording; this skill provides the reusable, isolated LiteTUI execution path.
