# Managed launcher policy

The deny floor rejects every parser-recognized executable `run` or `run.bat`
launcher, regardless of absolute/relative spelling, file existence, PATH,
PATHEXT, or environment/configuration identity claims. Agents launch managed
seats through `liteharness spawn`; owner application launch remains outside
this agent-tool path.

This deliberately also blocks unrelated absolute `run.bat` launchers. Existing
reader and proven literal-data exclusions remain. The command recognizer is
bounded, not a sandbox for arbitrary scripts, variables, aliases, or custom
launcher names. A future authenticated local selective-launch channel is
tracked separately as T0337; caller-controlled configuration is not authority.
