"""MCP server for open_kknaks task queue."""

import asyncio
import os

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.mcp.server import create_server

DEFAULT_BROKER_URL = "redis://localhost:6379"


def run() -> None:
    """Entry point for the open-kknaks-mcp console script.

    Reads REDIS_URL and NAMESPACE from environment variables.
    """
    broker_url = os.environ.get("REDIS_URL", DEFAULT_BROKER_URL)
    namespace = os.environ.get("NAMESPACE", "open_kknaks")

    async def _main() -> None:
        from mcp.server.stdio import stdio_server

        broker = RedisBroker(url=broker_url, namespace=namespace)
        await broker.connect()

        server = create_server(broker)

        try:
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        finally:
            await broker.close()

    asyncio.run(_main())
