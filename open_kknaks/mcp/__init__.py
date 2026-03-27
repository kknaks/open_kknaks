"""MCP server for open_kknaks — schema/documentation only."""

import asyncio

from open_kknaks.mcp.server import create_server


def run() -> None:
    """Entry point for the open-kknaks-mcp console script.

    Exposes tool schemas and documentation. No Redis connection required.
    """

    async def _main() -> None:
        from mcp.server.stdio import stdio_server

        server = create_server()

        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_main())
