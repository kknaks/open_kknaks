"""Worker CLI commands."""

import asyncio

import typer

worker_app = typer.Typer(no_args_is_help=True)


@worker_app.command("run")
def run(
    broker_url: str = typer.Option("redis://localhost:6379", "--broker", help="Redis broker URL"),
    namespace: str = typer.Option("open_kknaks", "--namespace"),
    queues: str = typer.Option("default", "--queues", help="Comma-separated queue names"),
    work_dir: str = typer.Option(".", "--work-dir"),
    model: str | None = typer.Option(None, "--model"),
    concurrency: int = typer.Option(4, "--concurrency"),
    shutdown_timeout: int = typer.Option(30, "--shutdown-timeout"),
) -> None:
    """Run a Claude Code worker."""

    async def _run() -> None:
        from open_kknaks.broker.redis import RedisBroker
        from open_kknaks.config import ClaudeConfig
        from open_kknaks.middleware.logging import LoggingMiddleware
        from open_kknaks.middleware.retries import RetriesMiddleware
        from open_kknaks.worker.worker import ClaudeWorker

        broker = RedisBroker(url=broker_url, namespace=namespace)
        await broker.connect()

        config = ClaudeConfig(work_dir=work_dir, model=model)
        middleware = [LoggingMiddleware(), RetriesMiddleware()]

        worker = ClaudeWorker(
            broker=broker,
            config=config,
            middleware=middleware,
            queues=queues.split(","),
            concurrency=concurrency,
            shutdown_timeout=float(shutdown_timeout),
        )

        try:
            await worker.run()
        finally:
            await broker.close()

    asyncio.run(_run())
