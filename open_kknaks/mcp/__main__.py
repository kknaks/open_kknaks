"""MCP server entry point: python -m open_kknaks.mcp"""

import asyncio

from mcp.server.stdio import stdio_server

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.mcp.server import create_server

DEFAULT_BROKER_URL = "redis://localhost:6379"


async def main(broker_url: str = DEFAULT_BROKER_URL) -> None:
    """Run the MCP server with stdio transport."""
    broker = RedisBroker(url=broker_url)
    await broker.connect()

    server = create_server(broker)

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())
