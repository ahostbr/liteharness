"""
LiteHarness Nudge Bot — autonomous agent feedback loop.

Watches the inbox for agent reports and auto-replies with pattern-matched
encouragement, impersonating Sentinel. Keeps agents churning without
burning orchestrator context.

Usage:
    liteharness nudge-bot                          # default config
    liteharness nudge-bot --config custom.yaml     # custom config
    liteharness nudge-bot --lmstudio               # opt-in local LLM reply mode
"""

import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config, inbox

logger = logging.getLogger("nudge-bot")


@dataclass
class NudgeRule:
    name: str
    pattern: re.Pattern
    responses: list[str]


@dataclass
class LMStudioConfig:
    enabled: bool = False
    base_url: str = "http://localhost:1234"
    model: str = "qwen/qwen3.6-27b"
    timeout_seconds: float = 240.0
    temperature: float = 0.4
    max_tokens: int | None = None


class LMStudioReplyProvider:
    def __init__(self, cfg: LMStudioConfig):
        self.cfg = cfg

    def generate(self, *, sender: str, body: str, category: str) -> str:
        base_url = self.cfg.base_url.rstrip("/")
        prompt = self._build_prompt(sender=sender, body=body, category=category)
        payload = {
            "model": self.cfg.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are drafting one short LiteHarness Nudge reply as Sentinel. "
                        "Reply only to the existing agent. Do not mention policies, do not "
                        "spawn agents, do not approve destructive actions, and do not include "
                        "shell commands. Keep it direct and under two sentences."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self.cfg.temperature,
            "stream": False,
        }
        if self.cfg.max_tokens is not None and self.cfg.max_tokens > 0:
            payload["max_tokens"] = self.cfg.max_tokens
        req = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.cfg.timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        message = data.get("choices", [{}])[0].get("message", {})
        # Keep thinking enabled, but never send reasoning_content back to agents.
        return self._final_content_only(message.get("content") or "")

    def _final_content_only(self, content: str) -> str:
        text = content.strip()
        if "</think>" in text.lower():
            text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1].strip()
        return text

    def _build_prompt(self, *, sender: str, body: str, category: str) -> str:
        return "\n".join(
            [
                f"Sender agent: {sender}",
                f"Matched category: {category}",
                "",
                "Agent message:",
                body,
                "",
                "Draft the exact reply text to send back through LiteHarness.",
            ]
        )


class NudgeBot:
    def __init__(self, config_path: str, lmstudio: LMStudioConfig | None = None):
        self.config_path = Path(config_path).expanduser()
        self.lmstudio = lmstudio or LMStudioConfig()
        self.lmstudio_provider = LMStudioReplyProvider(self.lmstudio) if self.lmstudio.enabled else None
        self.sentinel_id: str = ""
        self.target_agents: list[str] = []  # empty = all agents
        self.rules: list[NudgeRule] = []
        self.escalation_keywords: list[str] = []
        self.default_responses: list[str] = ["Copy. Continue working."]
        self.log_file: Path = config.get_root() / "nudge-bot.log"
        self.seen_ids: set[str] = set()
        self._config_mtime: float = 0
        self._load_config()
        self._setup_logging()

    def _setup_logging(self):
        handler = logging.FileHandler(str(self.log_file), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
        logger.addHandler(logging.StreamHandler())
        logger.setLevel(logging.INFO)

    def _load_config(self):
        import yaml

        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.sentinel_id = raw.get("sentinel_id", "")
        self.target_agents = raw.get("target_agents", [])
        self.escalation_keywords = [k.lower() for k in raw.get("escalation_keywords", [])]
        self.default_responses = raw.get("default_responses", ["Copy. Continue working."])
        self.log_file = Path(os.path.expanduser(raw.get("log_file", str(config.get_root() / "nudge-bot.log"))))

        self.rules = []
        for r in raw.get("rules", []):
            try:
                self.rules.append(NudgeRule(
                    name=r["name"],
                    pattern=re.compile(r["pattern"], re.IGNORECASE),
                    responses=r["responses"],
                ))
            except (KeyError, re.error):
                continue

        self._config_mtime = self.config_path.stat().st_mtime
        mode = f"targeting {len(self.target_agents)} agents" if self.target_agents else "all agents"
        logger.info("Config loaded: %d rules, %d escalation keywords, %s, sentinel=%s",
                     len(self.rules), len(self.escalation_keywords), mode, self.sentinel_id[:8])

    def _check_config_reload(self):
        try:
            mtime = self.config_path.stat().st_mtime
            if mtime > self._config_mtime:
                self._load_config()
        except OSError:
            pass

    def _is_escalation(self, body: str) -> bool:
        lower = body.lower()
        return any(kw in lower for kw in self.escalation_keywords)

    def _match_rule(self, body: str) -> NudgeRule | None:
        for rule in self.rules:
            if rule.pattern.search(body):
                return rule
        return None

    def _pick_response(self, rule: NudgeRule | None) -> str:
        pool = rule.responses if rule else self.default_responses
        return random.choice(pool)

    def _is_safe_lmstudio_reply(self, body: str) -> bool:
        text = body.strip()
        if not text or len(text) > 500:
            return False
        lower = text.lower()
        blocked = [
            "rm -rf",
            "git reset --hard",
            "push --force",
            "force push",
            "drop table",
            "drop database",
            "delete repo",
            "deploy to prod",
            "publish to npm",
            "spawn",
            "start a new agent",
            "launch a new agent",
        ]
        if any(term in lower for term in blocked):
            return False
        if "```" in text:
            return False
        if re.search(r"(?im)^\s*(git|rm|del|remove-item|curl|powershell|python|npm|bun|uv)\b", text):
            return False
        return True

    def _generate_response(self, *, rule: NudgeRule | None, sender: str, body: str) -> tuple[str, str]:
        fallback = self._pick_response(rule)
        if not self.lmstudio_provider:
            return fallback, "template"

        category = rule.name if rule else "default"
        try:
            generated = self.lmstudio_provider.generate(sender=sender, body=body, category=category)
        except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
            logger.warning("LM Studio reply failed for %s (%s): %s", sender[:8], category, e)
            return fallback, "template-fallback"
        except Exception as e:
            logger.warning("LM Studio reply failed for %s (%s): %s", sender[:8], category, e)
            return fallback, "template-fallback"

        if not self._is_safe_lmstudio_reply(generated):
            logger.warning("LM Studio reply rejected for %s (%s): %s", sender[:8], category, generated[:120])
            return fallback, "template-fallback"

        return generated, "lmstudio"

    def _is_codex_agent(self, agent_id: str) -> bool:
        try:
            path = config.get_root() / "agents" / f"{agent_id}.json"
            presence = json.loads(path.read_text(encoding="utf-8"))
            cli = presence.get("cli", "")
            return cli.startswith("codex-") and cli != "codex-desktop"
        except (OSError, json.JSONDecodeError):
            return False

    def _send_reply(self, to_agent: str, body: str):
        inbox.send(from_agent=self.sentinel_id, to_agent=to_agent, body=body)

    def run(self):
        from .hooks import _create_watcher

        logger.info("Nudge bot online — watching inbox as Sentinel (%s)", self.sentinel_id[:8])
        mode = "lmstudio" if self.lmstudio.enabled else "template"
        print(f"[NUDGE-BOT] Online. Impersonating Sentinel ({self.sentinel_id[:8]}). Config: {self.config_path}. Mode: {mode}", flush=True)

        watcher = _create_watcher(str(inbox.INBOX_ROOT))

        while True:
            changed = True
            try:
                changed = bool(watcher.wait(timeout=5.0))
                self._check_config_reload()

                if not inbox.INBOX_NEW.exists():
                    continue

                for f in sorted(inbox.INBOX_NEW.iterdir()):
                    if f.suffix != ".json":
                        continue
                    try:
                        msg = json.loads(f.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        continue

                    msg_id = msg.get("id", "")
                    if msg_id in self.seen_ids:
                        continue

                    to = msg.get("to", "")
                    sender = msg.get("from", "")

                    # Only handle messages addressed to Sentinel or broadcast
                    if to != self.sentinel_id and to != "broadcast":
                        continue

                    # Never reply to self
                    if sender == self.sentinel_id:
                        self.seen_ids.add(msg_id)
                        continue

                    # Target filter: if target_agents is set, only reply to listed agents
                    if self.target_agents and sender not in self.target_agents:
                        continue

                    self.seen_ids.add(msg_id)
                    body = msg.get("body", "")

                    # Escalation check
                    if self._is_escalation(body):
                        logger.warning("ESCALATION BLOCKED: matched keyword in message from %s: %s",
                                       sender[:8], body[:80])
                        # Claim message but don't reply
                        self._move_to_done(f)
                        continue

                    rule = self._match_rule(body)
                    category = rule.name if rule else "default"
                    response, source = self._generate_response(rule=rule, sender=sender, body=body)

                    self._send_reply(sender, response)
                    logger.info("REPLY to %s (%s/%s): %s", sender[:8], category, source, response)

                    self._move_to_done(f)

            except KeyboardInterrupt:
                logger.info("Shutting down")
                print("[NUDGE-BOT] Shutdown.", flush=True)
                break
            except Exception as e:
                logger.error("Error in watch loop: %s", e)

            if not changed:
                time.sleep(2)

    def _move_to_done(self, f: Path):
        inbox.INBOX_DONE.mkdir(parents=True, exist_ok=True)
        try:
            f.rename(inbox.INBOX_DONE / f.name)
        except OSError:
            pass
