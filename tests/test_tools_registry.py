from __future__ import annotations

import pytest

from runtime.tools.registry import (
    UnknownToolError,
    get_tool,
    get_tool_metadata,
    tools_for_capability,
)


def test_lookup_capability_has_two_competing_tools():
    tools = tools_for_capability("lookup")
    names = {t.name for t in tools}
    assert names == {"lookup_fast", "lookup_careful"}


def test_single_option_capabilities_have_exactly_one_tool():
    assert len(tools_for_capability("echo")) == 1
    assert len(tools_for_capability("delay")) == 1


def test_get_tool_metadata_matches_get_tool():
    metadata = get_tool_metadata("lookup_fast")
    assert metadata.fn is get_tool("lookup_fast")


def test_unknown_tool_name_raises():
    with pytest.raises(UnknownToolError):
        get_tool("does_not_exist")
