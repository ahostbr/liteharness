#!/usr/bin/env node
/**
 * Stitch MCP Bridge — Thin stdio MCP server that wraps stitch-mcp CLI tool calls.
 * Exposes Stitch tools (get_screen_code, get_screen_image, build_site, etc.)
 * as proper MCP tools over JSON-RPC stdio transport.
 */
import { execSync } from "node:child_process";
import { createInterface } from "node:readline";

const API_KEY = process.env.STITCH_API_KEY;
if (!API_KEY) {
  process.stderr.write("STITCH_API_KEY environment variable required\n");
  process.exit(1);
}

// Resolve the stitch-mcp binary. Override via STITCH_MCP_BIN env var if your
// global npm prefix differs from the default PATH location.
// Install with: npm install -g @_davideast/stitch-mcp
const STITCH_BIN = process.env.STITCH_MCP_BIN || "stitch-mcp";

function callStitchTool(toolName, args) {
  const dataArg = JSON.stringify(args);
  try {
    const result = execSync(
      `${STITCH_BIN} tool ${toolName} -d "${dataArg.replace(/"/g, '\\"')}"`,
      {
        env: { ...process.env, STITCH_API_KEY: API_KEY },
        timeout: 60000,
        encoding: "utf-8",
        stdio: ["pipe", "pipe", "pipe"],
        shell: true,
      },
    );
    return result.trim();
  } catch (e) {
    return JSON.stringify({ error: e.stderr || e.message });
  }
}

const TOOLS = [
  {
    name: "get_screen_code",
    description: "Get the HTML/CSS code for a specific Stitch screen",
    inputSchema: {
      type: "object",
      properties: {
        projectId: { type: "string", description: "Stitch project ID" },
        screenId: { type: "string", description: "Screen ID to retrieve" },
      },
      required: ["projectId", "screenId"],
    },
  },
  {
    name: "get_screen_image",
    description: "Get a base64 screenshot of a specific Stitch screen",
    inputSchema: {
      type: "object",
      properties: {
        projectId: { type: "string", description: "Stitch project ID" },
        screenId: { type: "string", description: "Screen ID to screenshot" },
      },
      required: ["projectId", "screenId"],
    },
  },
  {
    name: "build_site",
    description: "Map Stitch screens to routes and generate full site HTML",
    inputSchema: {
      type: "object",
      properties: {
        projectId: { type: "string", description: "Stitch project ID" },
        routes: {
          type: "array",
          description: "Array of {screenId, route} mappings",
          items: {
            type: "object",
            properties: {
              screenId: { type: "string" },
              route: { type: "string" },
            },
            required: ["screenId", "route"],
          },
        },
      },
      required: ["projectId", "routes"],
    },
  },
  {
    name: "list_projects",
    description: "List all Stitch projects for the authenticated user",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_screens",
    description: "List all screens in a Stitch project",
    inputSchema: {
      type: "object",
      properties: {
        projectId: { type: "string", description: "Stitch project ID" },
      },
      required: ["projectId"],
    },
  },
];

function send(msg) {
  process.stdout.write(JSON.stringify(msg) + "\n");
}

function handleRequest(req) {
  const { id, method, params } = req;

  if (method === "initialize") {
    send({
      jsonrpc: "2.0",
      id,
      result: {
        protocolVersion: "2024-11-05",
        capabilities: { tools: {} },
        serverInfo: { name: "stitch-bridge", version: "1.0.0" },
      },
    });
  } else if (method === "notifications/initialized") {
    // No response needed for notifications
  } else if (method === "tools/list") {
    send({ jsonrpc: "2.0", id, result: { tools: TOOLS } });
  } else if (method === "tools/call") {
    const { name, arguments: args } = params;
    const result = callStitchTool(name, args || {});
    send({
      jsonrpc: "2.0",
      id,
      result: { content: [{ type: "text", text: result }] },
    });
  } else if (id) {
    send({ jsonrpc: "2.0", id, error: { code: -32601, message: `Unknown method: ${method}` } });
  }
}

const rl = createInterface({ input: process.stdin });
rl.on("line", (line) => {
  try {
    handleRequest(JSON.parse(line));
  } catch (e) {
    process.stderr.write(`Parse error: ${e.message}\n`);
  }
});

process.stderr.write("[stitch-bridge] MCP server ready\n");
