"""Status, reset, and harvest commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from paude.cli.app import app


@app.command("status")
def status_cmd(
    session: Annotated[
        str | None,
        typer.Argument(help="Session name (all sessions if not specified)"),
    ] = None,
    openshift_context: Annotated[
        str | None,
        typer.Option(
            "--openshift-context",
            help="Kubeconfig context for OpenShift.",
        ),
    ] = None,
    openshift_namespace: Annotated[
        str | None,
        typer.Option(
            "--openshift-namespace",
            help="OpenShift namespace (default: current context namespace).",
        ),
    ] = None,
) -> None:
    """Show enriched status for all sessions."""
    from paude.workflow import status_sessions

    status_sessions(
        session_name=session,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
    )


@app.command("reset")
def reset_cmd(
    session: Annotated[str, typer.Argument(help="Session name to reset.")],
    branch: Annotated[
        str,
        typer.Option(
            "--branch",
            "-b",
            help="Branch to reset to (default: main).",
        ),
    ] = "main",
    force: Annotated[
        bool,
        typer.Option("--force", help="Skip unmerged work check."),
    ] = False,
    keep_conversation: Annotated[
        bool,
        typer.Option(
            "--keep-conversation",
            help="Keep Claude conversation history.",
        ),
    ] = False,
    openshift_context: Annotated[
        str | None,
        typer.Option(
            "--openshift-context",
            help="Kubeconfig context for OpenShift.",
        ),
    ] = None,
    openshift_namespace: Annotated[
        str | None,
        typer.Option(
            "--openshift-namespace",
            help="OpenShift namespace (default: current context namespace).",
        ),
    ] = None,
) -> None:
    """Reset a session's workspace for a new task."""
    from paude.workflow import reset_session

    reset_session(
        session_name=session,
        branch=branch,
        force=force,
        keep_conversation=keep_conversation,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
    )


@app.command("tail")
def tail_cmd(
    session: Annotated[str, typer.Argument(help="Session name to tail.")],
    lines: Annotated[
        int,
        typer.Option(
            "--lines",
            "-n",
            help="Number of lines to print from the end of the tmux buffer.",
        ),
    ] = 50,
    follow: Annotated[
        bool,
        typer.Option(
            "--follow",
            "-f",
            help="Follow output, printing new lines as they appear.",
        ),
    ] = False,
    interval: Annotated[
        float,
        typer.Option(
            "--interval",
            "-i",
            help="Poll interval in seconds when following (default: 3).",
        ),
    ] = 3.0,
    openshift_context: Annotated[
        str | None,
        typer.Option(
            "--openshift-context",
            help="Kubeconfig context for OpenShift.",
        ),
    ] = None,
    openshift_namespace: Annotated[
        str | None,
        typer.Option(
            "--openshift-namespace",
            help="OpenShift namespace (default: current context namespace).",
        ),
    ] = None,
) -> None:
    """Print the last N lines of the agent's tmux pane output.

    Reads the tmux scrollback buffer via exec — no need to attach.
    Use -f / --follow to stream new output as it appears.

    \\b
    Examples:

        # Print last 50 lines (default)
        paude tail my-session

        # Print last 100 lines
        paude tail my-session -n 100

        # Follow live output (Ctrl+C to stop)
        paude tail my-session -f

        # Follow with a faster poll interval
        paude tail my-session -f --interval 1
    """
    from paude.workflow import tail_session

    tail_session(
        session_name=session,
        lines=lines,
        follow=follow,
        interval=interval,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
    )


@app.command("wait")
def wait_cmd(
    session: Annotated[str, typer.Argument(help="Session name to wait for.")],
    interval: Annotated[
        int,
        typer.Option(
            "--interval",
            "-i",
            help="Poll interval in seconds.",
        ),
    ] = 30,
    timeout: Annotated[
        int,
        typer.Option(
            "--timeout",
            "-t",
            help="Timeout in minutes. 0 disables the timeout.",
        ),
    ] = 60,
    on_idle: Annotated[
        str | None,
        typer.Option(
            "--on-idle",
            help=(
                "Shell command to run when the session reaches Idle state "
                "(e.g., 'paude harvest my-session -b review/branch')."
            ),
        ),
    ] = None,
    notify: Annotated[
        bool,
        typer.Option(
            "--notify/--no-notify",
            help="Send a desktop notification when Idle (requires notify-send).",
        ),
    ] = True,
    openshift_context: Annotated[
        str | None,
        typer.Option(
            "--openshift-context",
            help="Kubeconfig context for OpenShift.",
        ),
    ] = None,
    openshift_namespace: Annotated[
        str | None,
        typer.Option(
            "--openshift-namespace",
            help="OpenShift namespace (default: current context namespace).",
        ),
    ] = None,
) -> None:
    """Wait for a session to reach Idle state, then optionally run a command.

    Polls the session at the given interval and prints a live status line.
    When the session becomes Idle, sends a desktop notification (if
    notify-send is available) and runs --on-idle if provided.

    Exit code 0 = Idle reached. Exit code 1 = timed out.

    Examples:

    \\b
        # Wait and notify only
        paude wait my-session

        # Fire-and-forget: auto-harvest when done
        paude wait my-session --on-idle "paude harvest my-session -b review/branch"

        # Background the wait so the terminal is free
        paude wait my-session --on-idle "paude harvest my-session -b review/branch" &
    """
    from paude.workflow import wait_session

    wait_session(
        session_name=session,
        interval=interval,
        timeout_minutes=timeout,
        on_idle=on_idle,
        send_notify=notify,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
    )


@app.command("run")
def run_cmd(
    task_file: Annotated[
        Path,
        typer.Option(
            "--task-file",
            "-t",
            help="Path to a task YAML file (id, spec_file, claims).",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ],
    session: Annotated[
        str | None,
        typer.Option(
            "--session",
            "-s",
            help="Session name (defaults to task-<id>).",
        ),
    ] = None,
    result_file: Annotated[
        Path | None,
        typer.Option(
            "--result-file",
            "-r",
            help="Write JSON result to this file.",
        ),
    ] = None,
    branch: Annotated[
        str | None,
        typer.Option(
            "--branch",
            "-b",
            help="Harvest branch name (defaults to task/<id>).",
        ),
    ] = None,
    openshift_context: Annotated[
        str | None,
        typer.Option("--openshift-context", help="Kubeconfig context for OpenShift."),
    ] = None,
    openshift_namespace: Annotated[
        str | None,
        typer.Option("--openshift-namespace", help="OpenShift namespace."),
    ] = None,
) -> None:
    """Run a task YAML end-to-end: create → wait → harvest → evaluate claims.

    The task file must have at minimum an ``id`` and a ``spec_file`` (path
    relative to the task file). An optional ``claims`` list defines verifiable
    assertions checked after harvest.

    \\b
    Exit codes:
        0  All claims passed (or no claims defined)
        1  One or more claims failed
        2  Execution error (session create / harvest failed)

    \\b
    Example task.yaml:

        id: my-task
        spec_file: ../task-specs/my-task-spec.txt
        agent: claude
        timeout_minutes: 60
        claims:
          - id: T-001
            check: git_committed
          - id: T-002
            check: file_exists
            path: docs/output.md
          - id: T-003
            check: agent_review
            prompt: "Does docs/output.md cover the required topic? Answer YES or NO."

    \\b
    Examples:

        paude run --task-file .planning/paude-integration/tasks/my-task.yaml
        paude run -t tasks/my-task.yaml --result-file results/my-task.json
    """
    from paude.workflow import run_task

    run_task(
        task_file=task_file,
        session_name=session,
        result_file=result_file,
        harvest_branch=branch,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
    )


@app.command("harvest")
def harvest_cmd(
    session: Annotated[str, typer.Argument(help="Session name to harvest from.")],
    branch: Annotated[
        str,
        typer.Option("--branch", "-b", help="Local branch name to create."),
    ],
    pr: Annotated[
        bool,
        typer.Option("--pr", help="Create a PR after harvesting."),
    ] = False,
    pr_title: Annotated[
        str | None,
        typer.Option("--pr-title", help="PR title (defaults to branch name)."),
    ] = None,
    openshift_context: Annotated[
        str | None,
        typer.Option(
            "--openshift-context",
            help="Kubeconfig context for OpenShift.",
        ),
    ] = None,
    openshift_namespace: Annotated[
        str | None,
        typer.Option(
            "--openshift-namespace",
            help="OpenShift namespace (default: current context namespace).",
        ),
    ] = None,
) -> None:
    """Harvest changes from a running session into a local branch."""
    from paude.workflow import harvest_session

    harvest_session(
        session_name=session,
        branch_name=branch,
        create_pr=pr,
        pr_title=pr_title,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
    )
