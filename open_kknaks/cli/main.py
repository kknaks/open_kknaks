"""CLI entry point using typer."""

import typer

from open_kknaks.cli.dlq_cmd import dlq_app
from open_kknaks.cli.queue_cmd import queue_app
from open_kknaks.cli.task_cmd import task_app
from open_kknaks.cli.worker_cmd import worker_app

app = typer.Typer(
    name="open-kknaks",
    help="PTY-based Claude Code task queue",
    no_args_is_help=True,
)

app.add_typer(worker_app, name="worker", help="Worker commands")
app.add_typer(queue_app, name="queue", help="Queue management")
app.add_typer(dlq_app, name="dlq", help="Dead Letter Queue")
app.add_typer(task_app, name="task", help="Task operations")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
