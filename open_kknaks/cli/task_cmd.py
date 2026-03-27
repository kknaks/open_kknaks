"""Task CLI commands."""

import asyncio

import typer

task_app = typer.Typer(no_args_is_help=True)


@task_app.command("status")
def status(
    task_id: str = typer.Argument(..., help="Task ID"),
    broker_url: str = typer.Option("redis://localhost:6379", "--broker"),
    namespace: str = typer.Option("open_kknaks", "--namespace"),
) -> None:
    """Get task status."""

    async def _run() -> None:
        from open_kknaks.broker.redis import RedisBroker

        broker = RedisBroker(url=broker_url, namespace=namespace)
        await broker.connect()
        try:
            task = await broker.get_task(task_id)
            if task:
                typer.echo(f"Status: {task.status}")
                typer.echo(f"Queue: {task.queue}")
                typer.echo(f"Priority: {task.priority}")
                if task.error:
                    typer.echo(f"Error: {task.error}")
                if task.usage:
                    typer.echo(f"Cost: ${task.usage.cost_usd:.4f}")
            else:
                typer.echo("Task not found", err=True)
                raise typer.Exit(1)
        finally:
            await broker.close()

    asyncio.run(_run())


@task_app.command("result")
def result(
    task_id: str = typer.Argument(..., help="Task ID"),
    wait: bool = typer.Option(False, "--wait", help="Wait for completion"),
    timeout: int = typer.Option(600, "--timeout"),
    broker_url: str = typer.Option("redis://localhost:6379", "--broker"),
    namespace: str = typer.Option("open_kknaks", "--namespace"),
) -> None:
    """Get task result."""

    async def _run() -> None:
        from open_kknaks.broker.redis import RedisBroker
        from open_kknaks.client import ClaudeClient

        broker = RedisBroker(url=broker_url, namespace=namespace)
        await broker.connect()
        try:
            if wait:
                client = ClaudeClient(broker=broker)
                task = await client.result(task_id, timeout=float(timeout))
            else:
                task = await broker.get_task(task_id)

            if task and task.result:
                typer.echo(task.result)
            elif task:
                typer.echo(f"No result yet (status: {task.status})")
            else:
                typer.echo("Task not found", err=True)
                raise typer.Exit(1)
        finally:
            await broker.close()

    asyncio.run(_run())


@task_app.command("cancel")
def cancel(
    task_id: str = typer.Argument(..., help="Task ID"),
    broker_url: str = typer.Option("redis://localhost:6379", "--broker"),
    namespace: str = typer.Option("open_kknaks", "--namespace"),
) -> None:
    """Cancel a task."""

    async def _run() -> None:
        from open_kknaks.broker.redis import RedisBroker
        from open_kknaks.client import ClaudeClient

        broker = RedisBroker(url=broker_url, namespace=namespace)
        await broker.connect()
        try:
            client = ClaudeClient(broker=broker)
            if await client.cancel(task_id):
                typer.echo(f"Cancelled {task_id}")
            else:
                typer.echo("Task not found", err=True)
                raise typer.Exit(1)
        finally:
            await broker.close()

    asyncio.run(_run())
