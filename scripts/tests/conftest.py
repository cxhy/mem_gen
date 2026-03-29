"""Shared fixtures for physical_wrapper_gen tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add scripts/ to sys.path so we can import config_io, physical_wrapper_gen, etc.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config_io import InterfaceType, SubTypeInfo

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "physical_wrapper"


@pytest.fixture(scope="session")
def vendor_port_map_data() -> dict:
    with open(FIXTURES_DIR / "vendor_port_map.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def interface_types(vendor_port_map_data) -> dict[str, InterfaceType]:
    """Parse all interface_types from fixture JSON into dataclasses."""
    result = {}
    for type_name, type_def in vendor_port_map_data["interface_types"].items():
        sub_types = tuple(
            SubTypeInfo(
                names=tuple(st["names"]),
                const_ports=dict(st.get("const_ports", {})),
                output_ports=tuple(st.get("output_ports", [])),
            )
            for st in type_def.get("sub_types", [])
        )
        result[type_name] = InterfaceType(
            base_type=type_def["base_type"],
            has_mask=type_def.get("has_mask", False),
            is_async=type_def.get("async", False),
            port_map=dict(type_def["port_map"]),
            sub_types=sub_types,
        )
    return result
