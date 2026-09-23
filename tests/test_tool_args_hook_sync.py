"""Regression lockout: tool_execute_before hook mutations must reach tool execution.

Real defect (agent.py hook->execute args fork, dev-ticket-2026-09-12-secrets-cache-
poisoned-literal-placeholders): on the Responses dispatch path
(``Agent._execute_tool_request``), the Tool instance may be constructed with its
own copy of ``tool_args``.  A ``tool_execute_before`` extension mutates
``tool_args`` in place (the documented hook contract -- e.g. secret-placeholder
substitutions), so the extension layer saw the substituted dict while the tool
executed with the original literals: a fork between the caller's dict and
``tool.args``.

Fix under test: both dispatch paths (``_execute_tool_request`` and
``process_tools``) re-bind ``tool.args = tool_args`` immediately after the
hook call, before ``tool.execute(**tool_args)``, so the execute kwargs and
``tool.args`` can never fork.

These tests drive the dispatch methods directly on a duck-typed agent
(no LLM, no network, fully deterministic) and assert the hook mutation is
visible in BOTH the kwargs ``execute()`` receives AND ``tool.args`` at
execute time (identity check against the caller's dict).

Note: this runtime does not ship pytest-asyncio, so the async dispatch paths
are driven via ``asyncio.run()`` inside plain sync test functions.  Inert
value names are used deliberately -- the framework's own unmask-secrets
extension treats placeholder-looking literals as live substitutions.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import agent as agent_module
from helpers import extension as extension_module
from helpers import mcp_handler
from helpers.tool import Response

SENTINEL = "SUBSTITUTED_VALUE"


class _FakeLog:
    def log(self, **kwargs):
        return SimpleNamespace(**kwargs)


class _FakeContext:
    def __init__(self) -> None:
        self.log = _FakeLog()


class _FakeLoopData:
    def __init__(self) -> None:
        self.current_tool = None


class RecordingTool:
    """Minimal Tool double: constructed like helpers.tool.Tool with its own args."""

    instances: list["RecordingTool"] = []

    def __init__(self, agent, name, method, args, message, loop_data, **kwargs) -> None:
        self.agent = agent
        self.name = name
        self.method = method
        # May be a different object than the caller's dict -- exactly the
        # production fork when get_tool copies the args.
        self.args = args
        self.message = message
        self.loop_data = loop_data
        self.execute_kwargs: dict | None = None
        self.args_at_execute: dict | None = None
        RecordingTool.instances.append(self)

    async def before_execution(self, **kwargs) -> None:
        return None

    async def execute(self, **kwargs) -> Response:
        self.execute_kwargs = dict(kwargs)
        # Capture the instance attribute AT EXECUTE TIME -- this is what the
        # fix guarantees is the same (hook-mutated) dict the caller passed.
        self.args_at_execute = self.args
        return Response(message="ok", break_loop=False)

    async def after_execution(self, response) -> None:
        return None


def _install_in_place_hook(monkeypatch) -> None:
    """Fake tool_execute_before extension: mutates tool_args in place (hook contract)."""

    async def fake_call_extensions_async(extension_point, agent=None, **kwargs):
        if extension_point == "tool_execute_before":
            tool_args = kwargs.get("tool_args") or {}
            tool_args.clear()
            tool_args.update({"secret": SENTINEL})
        return None

    monkeypatch.setattr(
        extension_module, "call_extensions_async", fake_call_extensions_async
    )


def _stub_mcp_lookup(monkeypatch) -> None:
    """Replace MCPConfig so no tool is found via MCP (local get_tool path is used)."""
    fake_config = SimpleNamespace(
        get_instance=lambda: SimpleNamespace(
            get_tool=lambda agent, tool_name: None,
        )
    )
    monkeypatch.setattr(mcp_handler, "MCPConfig", fake_config)


class _SyncAgent:
    """Duck-typed agent exposing only what the dispatch paths touch."""

    def __init__(self) -> None:
        self.context = _FakeContext()
        self.loop_data = _FakeLoopData()
        self.agent_name = "test"

    async def handle_intervention(self) -> None:
        return None

    async def validate_tool_request(self, tool_request) -> None:
        return None

    def get_tool(self, name, method, args, message, loop_data, **kwargs):
        # Simulate the production fork: the Tool is constructed with a COPY of
        # the caller's tool_args, so tool.args starts as a different object.
        return RecordingTool(
            agent=self,
            name=name,
            method=method,
            args=dict(args),
            message=message,
            loop_data=loop_data,
        )


def test_responses_path_hook_mutation_reaches_execute_and_tool_args(monkeypatch) -> None:
    """_execute_tool_request: in-place hook mutation lands in execute kwargs AND tool.args."""
    RecordingTool.instances = []
    _stub_mcp_lookup(monkeypatch)
    _install_in_place_hook(monkeypatch)
    fake_agent = _SyncAgent()

    tool_args = {"secret": "ORIGINAL_VALUE"}
    result = asyncio.run(
        agent_module.Agent._execute_tool_request(
            fake_agent,
            tool_name="argstest",
            tool_args=tool_args,
            message="msg",
        )
    )

    assert result is None
    assert len(RecordingTool.instances) == 1
    tool = RecordingTool.instances[0]

    # 1) execute() received the hook-mutated kwargs.
    assert tool.execute_kwargs == {"secret": SENTINEL}
    # 2) tool.args AT EXECUTE TIME is the same object the hook mutated (the fix).
    assert tool.args_at_execute is tool_args
    assert tool.args_at_execute == {"secret": SENTINEL}


def test_process_tools_path_hook_mutation_reaches_execute_and_tool_args(monkeypatch) -> None:
    """process_tools: in-place hook mutation lands in execute kwargs AND tool.args."""
    RecordingTool.instances = []
    _stub_mcp_lookup(monkeypatch)
    _install_in_place_hook(monkeypatch)
    fake_agent = _SyncAgent()

    msg = '{"tool_name": "argstest", "tool_args": {"secret": "ORIGINAL_VALUE"}}'
    result = asyncio.run(agent_module.Agent.process_tools(fake_agent, msg))

    assert result is None
    assert len(RecordingTool.instances) == 1
    tool = RecordingTool.instances[0]

    # 1) execute() received the hook-mutated kwargs.
    assert tool.execute_kwargs == {"secret": SENTINEL}
    # 2) tool.args AT EXECUTE TIME matches the hook-mutated dict (the fix).
    assert tool.args_at_execute == {"secret": SENTINEL}
