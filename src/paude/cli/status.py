"""Status, reset, and harvest commands."""

from __future__ import annotations

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
