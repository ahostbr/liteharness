# T0405 — plugin guard compatibility candidate

Measured 2026-10-07 in `.worktrees/T0404-floor-045-port`, branch `work/T0404-floor-045-port`. Parent candidate `0864a78fb94483e6d0c36ca8219fc254effd3dda` ports the release floor over OSS main `e0ab1d7`. No install, public push, plugin enable, main write, Suite/LiteTUI edit or retirement action was performed here. Required independent review and post-refresh human tool-use check remain outstanding.

## Constraint Analysis

The **actual shipped plugin 1.0.20** `hooks/mods/guard_backend.py` requires distribution version **0.4.5** and two exact normalized-LF SHA256 values. The installed editable metadata is **0.4.3**, points at the main OSS checkout, and rejects every tested tool before policy classification. Updating source files without refreshing editable metadata is insufficient.

## The Shortcut

Keep the release policy bytes intact; update the single build-version authority and repair obsolete tests rather than restoring the removed private launcher identity. No guard relaxation is included.

Worker changes:
- `pyproject.toml`: project version `0.4.4` → `0.4.5`. This is the only pyproject/build configuration; no setup.py/setup.cfg exists. `liteharness.__version__` reads installed importlib metadata rather than duplicating a source version.
- `tests/test_owner_launcher_d3.py`: remove the obsolete `_OWNER_LAUNCHER` monkeypatch; preserve all **414** D3 cases. Other/missing launcher identities now remain denied, consistent with `test_launcher_identity_free.py`. Command-position, benign-data, environment-assignment and real deny-gate route tests remain. No skip, xfail, deletion or deselection.

Release `fba17c7` contains the same stale D3 test as the pre-worker candidate; release conftest/pyproject do not suppress it. The release test fixture is broken, not an intended collection replacement. This change retains distinct D3 coverage instead of hiding it.

## Two-line released policy delta

1. Agents newly may make bounded read-only Git checks `branch --show-current` and `merge-base --is-ancestor <40-hex-OID> <40-hex-OID>`, including protected relocation contexts; redirects/output options and uncertain Git writes still refuse (release `1ea9c5a`, provenance overlay `d3ba9a8`).
2. Agents newly cannot execute any parser-recognized `run`/`run.bat` launcher merely because it is absent, another checkout, masked by PATH/PATHEXT, or not the private owner path; reader/proven-data exclusions remain. This is a bounded recognizer, not a sandbox or generic custom-launcher ban.

## Verification

Raw receipts/reproducer in the leader's 2026-10-07 scratch briefing directory:
- `T0405-owner-base-red.log/.xml`: `python -m pytest tests/test_owner_launcher_d3.py -q --tb=short`, **414 fixture errors**, RC1, before worker edits.
- `T0405-owner-green.log/.xml`: `python -m pytest tests/test_owner_launcher_d3.py tests/test_launcher_identity_free.py -q --tb=short`, **471 passed**, 2.09s, RC0.
- `T0405-scoped-final.log/.xml`: `python -m pytest tests/test_deny_floor.py tests/test_fleet_floor_t1025.py tests/test_git_relocation_reads.py tests/test_launcher_identity_free.py tests/test_owner_launcher_d3.py -q --tb=short`, **2628 passed, 5 failed**, 21.06s, RC1. Five failure IDs: installed-gate parity `[deny_gate.py]` / `[deny_floor.py]`, and inherited Git environment `[GIT_DIR]` / `[GIT_WORK_TREE]` / `[GIT_INDEX_FILE]`. The same five are **Sentinel-measured baseline**, per parent commit `0864a78`; worker did not remeasure main or fix those unrelated failures. Not an all-green claim.
- `T0405-shipped-guard.log`, RC0; `T0405-exercise-shipped-guard.py` is the complete evidence-only reproducer. It invokes the actual cached plugin artifact through `runpy`, never executes payload commands, and writes only temporary fixture metadata. Shipped guard SHA256: `6855f71344607e3304b7f8384236fb6d4e6fa6a086bcc937750c8c95046495bb`.

Actual shipped `guard_backend.decide` observations: current real metadata **0.4.3** raises `Unverified installed policy version` for readonly Bash `git status --short`, Read of pyproject, and forbidden Bash `rm -rf ~`. Then a genuine temporary `importlib.metadata.Distribution` (0.4.5 editable direct_url pointing at the candidate) is supplied at the metadata-resolution boundary only. The shipped `load_gate` verifies hashes and executes the exact candidate bytes; no fabricated guard/gate decision. **Conditional post-refresh expected behavior:** Bash/Read return no deny, no own-write grant and no rewritten command; the forbidden Bash returns `[home-variable-delete]`. This is **NOT** proof that installed metadata was refreshed or tools work live.

Pinned normalized-LF hashes, both measured and matched by shipped loader:
- `deny_gate.py`: `15e98aea4ac8154d028aadc2e375e18892d202a95794b9fbe82ef59706d3cd79`
- `deny_floor.py`: `c60d0fee461c3a4bfcfae928c624e4c64ffbbefc7a5d9103851f03fb22704046`

Candidate floor bytes equal release `fba17c7` after LF normalization. `git diff --check` and `python -m py_compile tests/test_owner_launcher_d3.py liteharness/deny_floor.py liteharness/deny_gate.py` passed. No configured lint/type validation or full-suite pass claimed.

## Ship Plan — leader-only deployment, not executed

After exact-tree Dijkstra review, current-intent approval and Sentinel merge, refresh the **existing main editable install**, not this temporary worktree. From the merged OSS checkout, use the same Python interpreter recorded by the real guard exercise (machine-specific absolute command supplied privately to the leader):

```powershell
python -m pip install --no-index --no-deps --no-build-isolation --editable .
```

Measured local prerequisites: setuptools **70.2.0** (build requires >=68), wheel **0.47.0**, psutil **7.2.2**. `--no-index`, `--no-deps`, `--no-build-isolation` prevent dependency/index/build-environment downloads. This command changes the installed editable distribution metadata/entry points and is a separate authorized deployment action, not something the worker has run.

Sentinel then verifies installed metadata 0.4.5, its direct_url still points to merged main, both pinned source hashes, and the **actual shipped guard with actual installed metadata** on the three sample payloads. Enable/reload the human's plugin only after those checks; verify real Bash/Read tool use works while the forbidden command remains denied. Do not execute the forbidden payload itself.

## Caveats / restart handoff

- LiteTUI's vendored floor reportedly retains the old owner-identity policy. Reviewer identified this scope gap; no mirror edits/sync/rebuild here. Do not claim cross-runtime parity.
- Native Claude settings-hook copies under `.claude/hooks/deny-floor` differ from candidate files (two measured failures). This candidate does not silently replace them or prove native-settings/mods parity.
- Inherited Git-environment tests remain three baseline reds. No guard exemptions were broadened to make them green.
- Plugin remains disabled pending required gates; simulated temporary metadata is not live acceptance. Checkpoint successor's first item is real post-merge metadata refresh verification, then actual guard decisions/human tool use. Keep locks/unaccepted worktree retained; leader owns lifecycle.
- B1 birth/retirement work remains frozen separately at `da709ab8a9c1e294d3d6cf088465f48fc91e7d09`, with `docs/evidence/T0403-B1-restart-handoff.md`; no retirement followup is included.

Rubric: constraint focus 5/5; minimalism 5/5; shippability 4/5 (deployment gated); clarity 4/5; honesty 5/5.
