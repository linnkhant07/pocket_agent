"""Minimal MCP stdio server (newline-delimited JSON-RPC) for the same tools.

Stdout is reserved for protocol messages. Logs go to stderr.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from pocket_agent import __version__
from pocket_agent.tools import TOOL_NAMES, ToolContext, execute, tool_catalog

PROTOCOL = "2024-11-05"


def handle_message(message: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method and method.startswith("notifications/"):
        return None
    ctx = ctx or ToolContext()
    try:
        result = _dispatch(method, message.get("params") or {}, ctx)
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _dispatch(method: str | None, params: dict[str, Any], ctx: ToolContext) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "pocket-agent", "version": __version__},
            "instructions": "Local recruiting tools. They never submit applications.",
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        tools = []
        for spec in tool_catalog():
            tools.append(
                {
                    "name": spec["name"],
                    "description": spec["description"],
                    "inputSchema": spec["input_schema"],
                }
            )
        return {"tools": tools}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in TOOL_NAMES:
            raise ValueError(f"unknown tool {name}")
        payload = execute(name, arguments, ctx)
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "isError": not bool(payload.get("ok", True)),
        }
    raise ValueError(f"unsupported method {method}")


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    reader = stdin or sys.stdin
    writer = stdout or sys.stdout
    ctx = ToolContext()
    for line in reader:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            writer.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}) + "\n")
            writer.flush()
            continue
        response = handle_message(message, ctx)
        if response is None:
            continue
        writer.write(json.dumps(response) + "\n")
        writer.flush()
    return 0
