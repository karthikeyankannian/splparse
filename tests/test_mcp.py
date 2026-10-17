import asyncio
import json

import pytest

pytest.importorskip("mcp")
from splparse.mcp_server import mcp


def _call(name, **kw):
    return asyncio.run(mcp.call_tool(name, kw))


def _payload(res):
    """Normalise across MCP SDK versions (1.x tuple / list, 2.x CallToolResult)."""
    if isinstance(res, tuple):
        return res[1]
    if hasattr(res, "content"):
        return json.loads(res.content[0].text)
    return json.loads(res[0].text)


def test_tools_listed():
    async def names():
        return {t.name for t in await mcp.list_tools()}
    assert {"validate_spl", "lint_spl", "format_spl", "parse_spl", "analyze_spl", "list_spl_commands"} <= asyncio.run(names())


def test_validate_and_lint():
    r = _payload(_call("validate_spl", spl="index=web | stats count by host"))
    assert r["ok"] and r["commands"] == ["search", "stats"]
    r = _payload(_call("validate_spl", spl="index=web | stats count by"))
    assert not r["ok"] and r["line"] == 1
    r = _payload(_call("lint_spl", spl="*err | statz count"))
    assert not r["ok"] and {d["rule"] for d in r["diagnostics"]} >= {"leading-wildcard", "unknown-command"}


def test_format_and_analyze():
    r = _payload(_call("format_spl", spl="index=web | stats count by host | sort -count"))
    assert r["formatted"].splitlines()[1] == "| stats count BY host"
    r = _payload(_call("analyze_spl", spl="index=web sourcetype=x | lookup t host OUTPUT owner | join host [search index=cmdb]"))
    assert r["indexes"] == ["web"] and r["has_subsearch"] and "lookup" in r["commands"]
