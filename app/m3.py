"""M3 AI Layer — Anthropic-compatible chat client.

Endpoint: https://api.minimax.io/anthropic
Uses Messages API format.
"""
import json
import os
from typing import List, Dict, Any, Optional, AsyncGenerator
import httpx

API_URL = "https://api.minimax.io/anthropic"
DEFAULT_MODEL = os.environ.get("M3_MODEL", "MiniMax-M3")

# System prompts per task type
SYSTEM_PROMPTS = {
    "insights": (
        "You are MTa Assistant — an AI for an email delivery platform. "
        "You answer operator questions in natural language. Be concise, "
        "use real numbers from the context, and suggest specific actions. "
        "If data is missing, say so explicitly. Never invent metrics."
    ),
    "analyze-queue": (
        "You are an email-delivery analyst. Given a queue's metrics "
        "(depth, throughput, bounce rate, deferred count), diagnose "
        "the likely cause and recommend actions. Cite specific numbers."
    ),
    "explain-config": (
        "You translate technical TOML/Lua config files into plain English "
        "that an operator can act on. Group by section, highlight any "
        "values that look risky or unusual."
    ),
    "suggest-actions": (
        "You are a senior SRE for an email platform. Given the current "
        "engine state (memory, queues, bounces, suspends), suggest the "
        "TOP 3 actions in priority order with concrete commands or API "
        "calls. Be specific. No fluff."
    ),
}


class M3:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("M3_API_KEY", "")
        self._client = httpx.AsyncClient(timeout=30)

    async def close(self):
        await self._client.aclose()

    async def chat(self, task: str, user_message: str,
                   context: Optional[Dict[str, Any]] = None,
                   max_tokens: int = 1024,
                   stream: bool = False) -> Dict[str, Any]:
        system = SYSTEM_PROMPTS.get(task, SYSTEM_PROMPTS["insights"])
        msg = user_message
        if context:
            msg = (f"{user_message}\n\n---\nContext (JSON):\n" +
                   json.dumps(context, indent=2, default=str)[:6000])
        body = {
            "model": DEFAULT_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": msg}],
        }
        if not self.api_key:
            # Fallback: rule-based answer from context
            return self._fallback(task, context, user_message)
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        try:
            r = await self._client.post(API_URL + "/v1/messages",
                                        json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")
            return {"ok": True, "text": text,
                    "model": data.get("model"),
                    "usage": data.get("usage", {}),
                    "task": task, "streamed": False}
        except Exception as e:
            return self._fallback(task, context, user_message, error=str(e))

    async def stream_chat(self, task: str, user_message: str,
                          context: Optional[Dict[str, Any]] = None
                          ) -> AsyncGenerator[str, None]:
        """Yield text chunks. Falls back to single yield on failure."""
        result = await self.chat(task, user_message, context)
        if result.get("ok") and result.get("text"):
            for line in result["text"].splitlines(keepends=True):
                yield line
        else:
            yield result.get("text", "(no response)")

    def _fallback(self, task: str, context: Optional[Dict[str, Any]],
                  user_message: str, error: str = None) -> Dict[str, Any]:
        """Rule-based fallback when API key missing or call fails.
        Returns a useful answer from context only."""
        ctx = context or {}
        lines = []
        lines.append(f"**[offline answer — M3 API not configured]**")
        if error:
            lines.append(f"_Note: {error}_")
        lines.append("")
        if task == "analyze-queue" and isinstance(ctx, dict):
            q = ctx.get("queue", "?")
            depth = ctx.get("depth", "?")
            delivered = ctx.get("delivered", "?")
            deferred = ctx.get("deferred", "?")
            bounced = ctx.get("bounced", "?")
            lines.append(f"Queue **{q}** has {depth} messages ready, "
                         f"{delivered} delivered, {deferred} deferred, "
                         f"{bounced} bounced.")
            if isinstance(deferred, int) and deferred > 50:
                lines.append("→ High defer count: check destination MX records "
                             "and rate limits with `/api/engine/ready-q-states`.")
            if isinstance(bounced, int) and bounced > 10:
                lines.append("→ Bounce spike: verify SPF/DKIM/DMARC with "
                             "`/api/cf/verify` and check reputation with "
                             "`/api/reputation/check-all`.")
        elif task == "suggest-actions" and isinstance(ctx.get("metrics"), dict):
            m = ctx["metrics"]
            mem = m.get("memory", {})
            used_pct = mem.get("used_pct", "?")
            lines.append(f"Memory used: **{used_pct}%**")
            if isinstance(used_pct, (int, float)) and used_pct > 80:
                lines.append("→ ACTION: Restart the mail engine to free memory "
                             "and trigger a `bump-config`.")
            lines.append("→ ACTION: Review suspended queues at "
                         "`/api/engine/suspends/ready` and unsuspend if temporary.")
            lines.append("→ ACTION: Verify SPF/DKIM/DMARC and IP reputation.")
        elif task == "explain-config" and ctx.get("content"):
            content = ctx["content"][:2000]
            lines.append("Config summary:")
            for ln in content.splitlines():
                if ln.strip() and not ln.strip().startswith("#"):
                    lines.append(f"  - `{ln.strip()}`")
        else:
            lines.append("I don't have M3 API access right now. "
                         "Provide a query with engine context to get a "
                         "rule-based analysis. To enable full AI, set "
                         "the `M3_API_KEY` env var.")
        return {"ok": True, "text": "\n".join(lines), "model": "fallback",
                "task": task, "streamed": False,
                "fallback": True}


# Golden Q&A pairs for tests
GOLDEN_QA = [
    {
        "task": "insights",
        "query": "Why are messages stuck?",
        "must_contain": ["queue", "stuck"],
        "context": {"ready_q_states": [{"queue": "outbound", "count": 1500}]},
    },
    {
        "task": "analyze-queue",
        "query": "Analyze outbound",
        "must_contain": ["outbound"],
        "context": {"queue": "outbound", "depth": 200, "delivered": 1000,
                    "deferred": 50, "bounced": 5},
    },
    {
        "task": "suggest-actions",
        "query": "What should I do now?",
        "must_contain": ["ACTION"],
        "context": {"metrics": {"memory": {"used_pct": 85}}},
    },
    {
        "task": "explain-config",
        "query": "Explain this config",
        "must_contain": ["Config"],
        "context": {"content": "# queues.toml\n[main]\nmax_threads=4\n"},
    },
    {"task": "insights", "query": "Hello", "must_contain": [], "context": {}},
    {"task": "insights", "query": "Bounce rate?", "must_contain": ["bounce"],
     "context": {"bounces": 12}},
    {"task": "analyze-queue", "query": "Analyze null",
     "must_contain": [], "context": None},
    {"task": "suggest-actions", "query": "Anything broken?",
     "must_contain": ["ACTION"], "context": {"metrics": {"memory": {"used_pct": 30}}}},
    {"task": "explain-config", "query": "Explain empty",
     "must_contain": [], "context": {"content": ""}},
    {"task": "insights", "query": "What is the engine?",
     "must_contain": ["engine"], "context": {"machine": {"version": "2026.06.23"}}},
]