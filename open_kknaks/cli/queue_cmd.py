"""Queue management CLI commands."""

import asyncio

import typer

queue_app = typer.Typer(no_args_is_help=True)


@queue_app.command("size")
def size(
    queue_name: str = typer.Argument(..., help="Queue name"),
    broker_url: str = typer.Option("redis://localhost:6379", "--broker"),
    namespace: str = typer.Option("open_kknaks", "--namespace"),
) -> None:
    """Get queue size."""

    async def _run() -> None:
        from open_kknaks.broker.redis import RedisBroker

        broker = RedisBroker(url=broker_url, namespace=namespace)
        await broker.connect()
        try:
            count = await broker.queue_size(queue_name)
            typer.echo(f"{queue_name}: {count} tasks")
        finally:
            await broker.close()

    asyncio.run(_run())
