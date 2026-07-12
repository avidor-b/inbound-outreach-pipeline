"""Static sanity checks on graph/tools.py to catch an entire class of
streaming-adapter bugs we've hit in production.

These are cheap AST-walk tests (no fixtures, no async) that trip the
moment someone wires a tool in a way that only shows up at runtime on
the SSE path. The production symptom they guard against is
`AttributeError: 'str' object has no attribute 'tool_call_id'` inside
ag_ui_langgraph when a tool's body recursively invokes another @tool
via `.ainvoke(...)` — each nested ainvoke bubbles an `OnToolEnd` event
whose output is a raw string, which the adapter can't convert to a
ToolCallResult.
"""
from __future__ import annotations

import ast
from pathlib import Path


_TOOLS_FILE = (
    Path(__file__).resolve().parent.parent
    / "src" / "outreach_pipeline" / "graph" / "tools.py"
)


def _tool_names(tree: ast.AST) -> set[str]:
    """Return the names of every top-level function decorated with @tool."""
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # Matches both `@tool` and `@tool(...)`.
            call = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(call, ast.Name) and call.id == "tool":
                names.add(node.name)
                break
    return names


def _ainvoke_call_targets(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, target) pairs for every `<name>.ainvoke(` call.

    Only direct attribute access on a Name is returned — nested
    expressions like `obj.subgraph.ainvoke(...)` fall out, which is the
    right call because those aren't @tool invocations.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, ast.Attribute) or f.attr != "ainvoke":
            continue
        if not isinstance(f.value, ast.Name):
            continue
        out.append((node.lineno, f.value.id))
    return out


def test_no_tool_recursively_invokes_another_tool_via_ainvoke():
    """Rule: inside graph/tools.py, no tool's body may call
    `<other_tool>.ainvoke(...)`. Extract a plain helper and call that
    from both tools — see _get_submissions_one / _get_submissions_bulk_payload
    for the pattern.

    This guards the exact regression that slipped through pytest-only
    unit tests: `get_submissions_bulk` was calling
    `get_submissions.ainvoke(...)` in a loop, and the SSE adapter
    crashed once it saw the nested `OnToolEnd` events.
    """
    tree = ast.parse(_TOOLS_FILE.read_text())
    tool_names = _tool_names(tree)
    assert tool_names, "sanity: expected @tool functions in tools.py"

    violations: list[str] = []
    for lineno, target in _ainvoke_call_targets(tree):
        if target in tool_names:
            violations.append(f"{_TOOLS_FILE.name}:{lineno} calls {target}.ainvoke()")

    assert not violations, (
        "Tools in graph/tools.py must not invoke each other via "
        "`.ainvoke()` — nested OnToolEnd events crash the ag_ui_langgraph "
        "streaming adapter (AttributeError on tool_call_id). Extract a "
        "plain async helper and call that from both tools. Offenders:\n  "
        + "\n  ".join(violations)
    )
