# Claude native inbox adapter (T0315)

`python -m liteharness.mod_inbox {poll,ack,release} --agent ID --session ID
--owner ID [--receipt NAME.json]` is an argv-only transport for the companion
liteharness plugin's native inbox. It does not start a watcher, run commands from
mail, install anything, or alter LiteTUI delivery. The plugin feature is opt-in
with `LITEHARNESS_NATIVE_INBOX=1`; its installed Python package must include both
this adapter and the matching `hooks.check_inbox`/`watch_inbox` lease recognition.
Plugin-only updates cannot supply this module.

The adapter proves a registered live Claude process by PID **and** process
creation time, and verifies the adapter's ancestry against that PID. It does not
infer identity from the newest transcript or mutate presence. A short OS file
lock serializes ledger updates. Native ownership lasts at most twenty seconds;
future, stale, corrupt, dead, PID-reused and non-Claude owners never suppress
legacy delivery. Each poll/ack/release carries a distinct runtime owner plus
session and recipient identity; another fresh owner cannot claim or acknowledge
its receipts. Session identity is supplied by the engine-backed mod, while
registered process identity is proven by this adapter.

The adapter reuses canonical `inbox.claim` (atomic new-to-cur rename) and
`inbox.complete` (cur-to-done). It records scoped filenames before claiming:
a crash immediately after a rename can be replayed by the next owner. It does
not archive on poll, stdout, timer scheduling, or UI display. The plugin
acknowledges only after the session accepts the exact native prompt/context and
persists accepted content. Accepted means entered/queued, **not processed**.
Delivery is at-least-once; a host/archive failure can replay mail.

Claims survive native release/expiry in `cur`. The legacy turn hook recovers
these even when a fresh legacy watcher is attached (that watcher reads only
`new`). It otherwise preserves existing watcher deferral. During a fresh native
lease both legacy consumers remain silent. No installed watcher is stopped.
These checks are fallback coordination, not proof against every legacy process
that started a scan before the lease appeared; opt-in/live parity remains
required before replacement is claimed.

The JSON response allowlists schemaVersion, agentId, sessionId, messages and
blocked count. Each message allowlists id/from/to/body/type/priority/receipt.
Both Python body and Desktop payload text/body producers are handled. Other
recipients and self-mail remain untouched. Maximum eight messages, 24,000 UTF-16
units per body and 48,000 per batch match JavaScript's limits. Unsupported
addressed mail is retained; a blocked response releases suppression, and the
plugin pauses until reload rather than renewing an undeliverable lease forever.
Local lease ledgers contain identity/receipts, not mail bodies or credentials.

Focused test commands (temporary fixtures only):

```sh
python -m pytest tests/test_mod_inbox.py tests/test_inbox.py tests/test_hook_defers_to_a_live_watcher.py -q
```

The companion plugin has shipped-entrypoint Node fixtures and Claude test-host
tests. Source/fixture passes are not engine-host, live message delivery, runtime
installation or human acceptance. Release/version/catalog changes and public
privacy audit are integration responsibilities; no push is authorized here.
