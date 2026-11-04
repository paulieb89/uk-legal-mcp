"""Ensure the FastMCP upgrade does not silently change the Legal MCP surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.capture_gateway_surface import capture


FIXTURE = Path("tests/fixtures/gateway_surface_v3.json")


_V3_PROMPT_ARGUMENT_PREFIX = "Provide as a JSON string matching the following schema: "
_V4_PROMPT_ARGUMENT_PREFIX = "Provide a value matching the following JSON schema: "
_V4_GENERATED_TOOL_TITLES = {
    "get_prompt": "Get Prompt",
    "list_prompts": "List Prompts",
    "list_resources": "List Resources",
    "read_resource": "Read Resource",
}
_V4_WRAPPER_DESCRIPTION = "Generic wrapper for non-object return types."


def _resource_key(resource: dict) -> str:
    return resource.get("uri") or resource["uriTemplate"]


def _normalise_reviewed_fastmcp_v4_deltas(expected: dict, actual: dict) -> None:
    """Apply only the reviewed FastMCP 4.1.3 serialization deltas.

    The fixture remains the v3 contract.  Each normalization asserts the exact
    v4-generated replacement before changing the v3 side, so a Legal-owned
    description, annotation, schema, or title cannot disappear silently.
    """
    actual_prompts = {prompt["name"]: prompt for prompt in actual["prompts"]}
    for prompt in expected["prompts"]:
        actual_prompt = actual_prompts[prompt["name"]]
        for expected_argument, actual_argument in zip(
            prompt.get("arguments", []), actual_prompt.get("arguments", []), strict=True
        ):
            description = expected_argument.get("description", "")
            if description.startswith(_V3_PROMPT_ARGUMENT_PREFIX):
                schema = description.removeprefix(_V3_PROMPT_ARGUMENT_PREFIX)
                assert actual_argument.get("description") == (
                    f"{_V4_PROMPT_ARGUMENT_PREFIX}{schema}. "
                    "Encode non-string values as JSON."
                )
                expected_argument["description"] = actual_argument["description"]

    actual_resources = {
        _resource_key(resource): resource
        for resource in [*actual["resources"], *actual["resource_templates"]]
    }
    for resource in [*expected["resources"], *expected["resource_templates"]]:
        actual_resource = actual_resources[_resource_key(resource)]
        expected_annotations = resource.get("annotations", {})
        expected_without_v3_only_hints = {
            key: value
            for key, value in expected_annotations.items()
            if key not in {"readOnlyHint", "idempotentHint"}
        }
        assert actual_resource.get("annotations", {}) == expected_without_v3_only_hints
        resource["annotations"] = expected_without_v3_only_hints

    actual_tools = {tool["name"]: tool for tool in actual["tools"]}
    for tool in expected["tools"]:
        actual_tool = actual_tools[tool["name"]]
        # Tool annotations are Legal-owned MCP contract, not a v4 serialization change.
        assert actual_tool.get("annotations") == tool.get("annotations")

        expected_title = _V4_GENERATED_TOOL_TITLES.get(tool["name"])
        if expected_title:
            assert actual_tool.get("title") == expected_title
            actual_tool.pop("title")

        if tool["name"] in _V4_GENERATED_TOOL_TITLES:
            assert tool["outputSchema"].get("description") == _V4_WRAPPER_DESCRIPTION
            assert "description" not in actual_tool["outputSchema"]
            tool["outputSchema"].pop("description")


@pytest.mark.asyncio
async def test_gateway_surface_matches_reviewed_v3_fixture():
    expected = json.loads(FIXTURE.read_text())
    actual = await capture()

    # The server card intentionally tracks the package release.  Everything else
    # must match the v3 fixture after the small, asserted FastMCP 4.1.3 deltas.
    expected["routes"]["/.well-known/mcp/server-card.json"]["json"]["serverInfo"]["version"] = (
        actual["routes"]["/.well-known/mcp/server-card.json"]["json"]["serverInfo"]["version"]
    )
    _normalise_reviewed_fastmcp_v4_deltas(expected, actual)

    assert actual == expected


@pytest.mark.asyncio
async def test_every_tool_remains_explicitly_read_only():
    """Protect the Legal-owned tool safety annotation from framework drift."""
    surface = await capture()
    assert all(
        tool.get("annotations", {}).get("readOnlyHint") is True
        for tool in surface["tools"]
    )
