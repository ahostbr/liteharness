# 0.4.9 release prep: the one full test run

Receipt for card LH-RELEASE-PREP-20261111, kept so the run's logs can be deleted. Nothing below
was fixed by that card.

**Run:** 2026-11-11, branch `work/lh-release-prep-20261111` with the content of commits
`42c47bd`, `3c3a54b` and `f71554d` in the working tree, host Python 3.11 on Windows,
`python -m pytest -q -p no:cacheprovider`, with a seat's own `LITEHARNESS*`, `CLAUDE*`,
`LITESUITE*` and `LITETUI*` variables removed from the environment.

**Result:** 37 failed, 7350 passed, 7 skipped, 46 subtests passed, 900 s.

## 33 that fail the same on untouched develop

Measured by running these eleven files alone on develop `e443c13`: 33 failed, 133 passed.

```
tests/test_catalog_provenance.py::test_provenance_hash_and_count_match_the_catalog
tests/test_catalog_provenance.py::test_reconciliation_payload_inventory_and_digest
tests/test_claude_identity_edges_t0589.py::test_uuid_never_launches_a_missing_foreign_ambiguous_or_live_native_session[wrong-cwd]
tests/test_cognitive_event_continuity_t0589.py::test_concurrent_actual_hooks_for_same_verified_owner_emit_one_method
tests/test_cognitive_t0465.py::test_a_claude_resume_launches_with_the_cognitive_file
tests/test_cognitive_t0465.py::test_control_a_claude_resume_without_cognitive_keeps_name_resolution
tests/test_cognitive_t0465.py::test_an_owned_litetui_resume_hands_cognitive_to_the_resolver
tests/test_cognitive_t0465.py::test_control_an_owned_litetui_resume_without_cognitive_sends_none
tests/test_git_scanner_roles.py::test_fleet_brief_still_refuses_missing_unknown_or_duplicate[no line at all]
tests/test_git_scanner_roles.py::test_fleet_brief_still_refuses_missing_unknown_or_duplicate[Agent-Role: janitor]
tests/test_git_scanner_roles.py::test_fleet_brief_still_refuses_missing_unknown_or_duplicate[Agent-Role: owner\nAgent-Tier: leader]
tests/test_git_scanner_roles.py::test_fleet_brief_still_refuses_missing_unknown_or_duplicate[Agent-Role: owner\nAgent-Role: scout]
tests/test_litetui_tab_rename_t0077.py::test_a_rename_that_misses_twice_lands_on_the_third_try
tests/test_litetui_tab_rename_t0077.py::test_a_rename_that_never_lands_is_reported_honestly_and_the_spawn_still_succeeds
tests/test_litetui_tab_rename_t0077.py::test_a_transport_error_is_a_retry_not_a_crash
tests/test_litetui_tab_rename_t0077.py::test_the_first_success_does_not_sleep
tests/test_owned_launch.py::test_actual_owned_resume_ignores_poisoned_registry_index_and_uses_home
tests/test_owned_launch.py::test_actual_owned_guards_refuse_before_resolution_and_never_close[owned-Resource owned or inaccessible]
tests/test_owned_launch.py::test_actual_owned_guards_refuse_before_resolution_and_never_close[live-pty-Live owned seat exists]
tests/test_register_preamble_targets_git.py::RegisterPreambleTargetsGitTests::test_the_preamble_is_actually_in_this_function
tests/test_resume_bridge_data_root.py::test_plain_shell_resume_uses_bridge_root_and_folder_execution[False-owned-only]
tests/test_resume_bridge_data_root.py::test_plain_shell_resume_uses_bridge_root_and_folder_execution[False-11111111-1111-4111-8111-111111111111]
tests/test_resume_bridge_data_root.py::test_plain_shell_resume_uses_bridge_root_and_folder_execution[True-owned-only]
tests/test_resume_bridge_data_root.py::test_plain_shell_resume_uses_bridge_root_and_folder_execution[True-11111111-1111-4111-8111-111111111111]
tests/test_resume_bridge_data_root.py::test_without_registry_discovery_uses_only_explicit_execution
tests/test_resume_bridge_data_root.py::test_second_resolver_must_keep_discovered_root
tests/test_resume_bridge_data_root.py::test_claude_resume_without_root_does_not_discover_litetui
tests/test_resume_bridge_data_root.py::test_resume_preserves_complete_launch_text_and_first_turn[role_args0]
tests/test_resume_bridge_data_root.py::test_resume_preserves_complete_launch_text_and_first_turn[role_args1]
tests/test_resume_bridge_data_root.py::test_resume_preserves_complete_launch_text_and_first_turn[role_args2]
tests/test_resume_bridge_data_root.py::test_bare_role_flag_refused_before_resume_placement[--system-prompt-file]
tests/test_resume_bridge_data_root.py::test_bare_role_flag_refused_before_resume_placement[--system-prompt]
tests/test_roles.py::test_resume_takes_the_role_from_a_role_only_registry_row
```

The first one's message is "stale release stamp": `liteharness/catalog/PROVENANCE.json` says
`42780e2f2120` and the catalog hashes to `5408290a2926`. The catalog changed after it was last
stamped.

## 4 that fail only in the full-suite order

The same file run alone on the branch gives 6 passed. Whether they fail in a full run of develop
was not measured.

```
tests/test_resume_does_not_evict_takeover.py::ResumeEvictionTests::test_both_ids_are_logged_from_the_one_process_that_decided
tests/test_resume_does_not_evict_takeover.py::ResumeEvictionTests::test_hypothesis_a_env_id_absent_from_the_hook
tests/test_resume_does_not_evict_takeover.py::ResumeEvictionTests::test_hypothesis_b_env_id_present_but_already_rewritten
tests/test_resume_does_not_evict_takeover.py::ResumeEvictionTests::test_the_adopted_record_is_refreshed_not_merely_left_alone
```

In the full run each resolved the session to the payload's id where the test expects the
takeover's id (`test_resume_does_not_evict_takeover.py:181`), which points at state left by an
earlier test, not at this file.
