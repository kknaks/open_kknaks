"""DLQ management CLI commands."""

import asyncio

import typer

dlq_app = typer.Typer(no_args_is_help=True)


@dlq_app.command("list")
def list_dlq(
    queue_name: str = typer.Argument(..., help="Queue name"),
    limit: int = typer.Option(100, "--limit"),
    broker_url: str = typer.Option("redis://localhost:6379", "--broker"),
    namespace: str = typer.Option("open_kknaks", "--namespace"),
) -> None:
    """List tasks in the Dead Letter Queue."""

    async def _run() -> None:
        from open_kknaks.broker.redis import RedisBroker

        broker = RedisBroker(url=broker_url, namespace=namespace)
        await broker.connect()
        try:
            tasks = await broker.list_dlq(queue_name, limit=limit)
            if not tasks:
                typer.echo("DLQ is empty")
                return
            for task in tasks:
                typer.echo(f"{task.id}  {task.status:10s}  {task.prompt[:60]}")
        finally:
            await broker.close()

    asyncio.run(_run())


@dlq_app.command("retry")
def retry(
    queue_name: str = typer.Argument(..., help="Queue name"),
    task_id: str | None = typer.Option(None, "--task-id", help="Specific task to retry"),
    retry_all: bool = typer.Option(False, "--all", help="Retry all DLQ tasks"),
    broker_url: str = typer.Option("redis://localhost:6379", "--broker"),
    namespace: str = typer.Option("open_kknaks", "--namespace"),
) -> None:
    """Retry task(s) from the DLQ."""

    async def _run() -> None:
        from open_kknaks.broker.redis import RedisBroker

        broker = RedisBroker(url=broker_url, namespace=namespace)
        await broker.connect()
        try:
            if retry_all:
                tasks = await broker.list_dlq(queue_name, limit=10000)
                for t in tasks:
                    await broker.retry_from_dlq(queue_name, t.id)
                typer.echo(f"Retried {len(tasks)} tasks")
            elif task_id:
                await broker.retry_from_dlq(queue_name, task_id)
                typer.echo(f"Retried {task_id}")
            else:
                typer.echo("Specify --task-id or --all")
        finally:
            await broker.close()

    asyncio.run(_run())


@dlq_app.command("purge")
def purge(
    queue_name: str = typer.Argument(..., help="Queue name"),
    broker_url: str = typer.Option("redis://localhost:6379", "--broker"),
    namespace: str = typer.Option("open_kknaks", "--namespace"),
) -> None:
    """Purge all tasks from the DLQ."""
    if not typer.confirm(f"Purge DLQ for '{queue_name}'?"):
        raise typer.Abort()

    async def _run() -> None:
        from open_kknaks.broker.redis import RedisBroker

        broker = RedisBroker(url=broker_url, namespace=namespace)
        await broker.connect()
        try:
            await broker.purge_dlq(queue_name)
            typer.echo(f"Purged DLQ for {queue_name}")
        finally:
            await broker.close()

    asyncio.run(_run())
