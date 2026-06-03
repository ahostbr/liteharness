/**
 * LiteHarness Plugin for OpenCode / KiloCode
 *
 * Provides inter-agent messaging via LiteHarness.
 * Hooks into session.created (register presence) and
 * tool.execute.after (check inbox for messages).
 *
 * Install:
 *   Copy to ~/.config/opencode/plugins/liteharness.ts
 *   Or for KiloCode: ~/.kilocode/plugins/liteharness.ts
 */

import type { Plugin } from "@opencode-ai/plugin";

export const LiteHarness: Plugin = async ({ $ }) => {
  // Register presence on session start
  try {
    await $`python -m liteharness.hooks register`;
  } catch {
    // LiteHarness not installed — skip silently
  }

  return {
    "session.created": async () => {
      try {
        await $`python -m liteharness.hooks register`;
      } catch {
        // Ignore if not installed
      }
    },

    "tool.execute.after": async () => {
      try {
        const result = await $`python -m liteharness.hooks check`.text();
        if (result.trim()) {
          console.log(result);
        }
      } catch {
        // Ignore
      }
    },

    "session.idle": async () => {
      try {
        await $`python -m liteharness.hooks heartbeat`;
      } catch {
        // Ignore
      }
    },

    "shell.env": () => {
      // Inject LiteHarness env vars so config.get_agent_id() can detect the CLI
      return {
        OPENCODE_SESSION_ID: process.env.OPENCODE_SESSION_ID || "",
      };
    },
  };
};
