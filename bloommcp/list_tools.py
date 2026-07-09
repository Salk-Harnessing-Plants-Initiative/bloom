"""Demo: list the tools served at each bloommcp endpoint.

Shows the section split — /mcp returns every tool (Lin's namespaced), while
/phenotyping_segmentation/mcp returns only that section's tools.

Run (bloommcp server must be up on :8811):
    .venv/bin/python list_tools.py
"""

import asyncio
import os

from fastmcp import Client

BASE = os.getenv("BLOOMMCP_URL", "http://localhost:8811")
KEY = os.getenv("BLOOMMCP_API_KEY", "devdemo")
PATHS = ["/mcp", "/phenotyping_segmentation/mcp"]


async def main() -> None:
    for path in PATHS:
        try:
            async with Client(f"{BASE}{path}", auth=KEY) as client:
                tools = sorted(t.name for t in await client.list_tools())
                print(f"\n{path}  ({len(tools)} tools)")
                for name in tools:
                    print("   ", name)
        except Exception as exc:
            print(f"\n{path}  -> ERROR: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
