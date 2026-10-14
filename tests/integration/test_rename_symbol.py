"""Integration test for rename_symbol tool."""

import json
import os
import sys
import tempfile

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _resolve_binary_name(session: ClientSession) -> str:
    binaries_result = await session.call_tool("list_project_binaries", {})
    binaries_payload = json.loads(binaries_result.content[0].text)
    programs = binaries_payload.get("programs") or []
    if len(programs) != 1:
        raise AssertionError(f"Expected exactly one imported binary, got {programs}")
    return programs[0]["name"]


@pytest.fixture(scope="module")
def symbol_test_binary():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".c", delete=False) as f:
        f.write(
            """
#include <stdio.h>

int global_data = 42;

__attribute__((noinline)) int function_one(int count) {
    return count + global_data;
}

int main(void) {
    return function_one(3);
}
"""
        )
        c_file = f.name

    bin_file = c_file.replace(".c", "")
    os.system(f"gcc -g -O0 -o {bin_file} {c_file}")

    yield bin_file

    os.unlink(c_file)
    os.unlink(bin_file)


@pytest.fixture(scope="module")
def symbol_server_params(symbol_test_binary, ghidra_env, isolated_project_root):
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "pyghidra_mcp",
            "--project-path",
            str(isolated_project_root / "symbol_server_params"),
            "--project-name",
            "symbol_server_params_project",
            "--wait-for-analysis",
            symbol_test_binary,
        ],
        env=ghidra_env,
    )


@pytest.mark.asyncio
async def test_rename_symbol_tool(symbol_server_params, symbol_test_binary):
    async with stdio_client(symbol_server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            binary_name = await _resolve_binary_name(session)

            # Find a data symbol (DAT_* or the global)
            symbols_result = await session.call_tool(
                "search_symbols_by_name",
                {
                    "binary_name": binary_name,
                    "query": "global_data|DAT_",
                    "functions_only": False,
                    "limit": 10,
                },
            )
            symbols_payload = json.loads(symbols_result.content[0].text)
            symbols = symbols_payload.get("symbols") or []
            data_symbol = None
            for sym in symbols:
                if isinstance(sym, dict) and (
                    "DAT_" in str(sym.get("name", "")) or "global_data" in str(sym.get("name", ""))
                ):
                    data_symbol = sym
                    break

            if data_symbol is None:
                # Fallback: search for any non-function symbol
                symbols_result = await session.call_tool(
                    "search_symbols_by_name",
                    {
                        "binary_name": binary_name,
                        "query": ".*",
                        "functions_only": False,
                        "limit": 50,
                    },
                )
                symbols_payload = json.loads(symbols_result.content[0].text)
                symbols = symbols_payload.get("symbols") or []
                for sym in symbols:
                    if isinstance(sym, dict) and str(sym.get("type", "")).lower() != "function":
                        data_symbol = sym
                        break

            assert data_symbol is not None, "No data symbol found to rename"
            original_name = data_symbol["name"]
            address = data_symbol["address"]

            new_name = f"{original_name}_renamed"
            rename_result = await session.call_tool(
                "rename_symbol",
                {
                    "binary_name": binary_name,
                    "name_or_address": original_name,
                    "new_name": new_name,
                },
            )
            rename_payload = json.loads(rename_result.content[0].text)
            assert rename_payload["old_name"] == original_name
            assert rename_payload["new_name"] == new_name
            assert rename_payload["address"] == address

            # Verify the rename stuck by searching for the new name
            verify_result = await session.call_tool(
                "search_symbols_by_name",
                {
                    "binary_name": binary_name,
                    "query": new_name,
                    "functions_only": False,
                },
            )
            verify_payload = json.loads(verify_result.content[0].text)
            verify_symbols = verify_payload.get("symbols") or []
            assert any(isinstance(s, dict) and s.get("name") == new_name for s in verify_symbols), (
                "Renamed symbol not found"
            )

            # Rename back
            rename_back = await session.call_tool(
                "rename_symbol",
                {
                    "binary_name": binary_name,
                    "name_or_address": new_name,
                    "new_name": original_name,
                },
            )
            rename_back_payload = json.loads(rename_back.content[0].text)
            assert rename_back_payload["old_name"] == new_name
            assert rename_back_payload["new_name"] == original_name
