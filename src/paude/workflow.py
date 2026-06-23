"""Workflow commands for paude sessions."""

from __future__ import annotations

import fnmatch
import shlex
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import typer

from paude.backends.base import Backend, Session
from paude.backends.shared import (
    engine_binary_for_backend,
    is_local_backend,
    pod_name,
    resource_name,
)
from paude.constants import BASE_REF_NAME, CONTAINER_HOME, CONTAINER_WORKSPACE

_PROTECTED_BRANCH_PATTERNS = frozenset(
    {
        "main",
        "master",
        "release",
        "release-*",
        "release/*",
    }
)


def _validate_harvest_branch(branch_name: str) -> None:
    """Raise typer.Exit if branch_name is a protected branch."""
    for pattern in _PROTECTED_BRANCH_PATTERNS:
        if fnmatch.fnmatch(branch_name, pattern):
            typer.echo(
                f"Error: Cannot harvest to protected branch '{branch_name}'.",
                err=True,
            )
            raise typer.Exit(1)


def _find_backend_and_session(
    session_name: str,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
    connect_timeout: int | None = None,
) -> tuple[str, Backend, Session]:
    """Find the backend and session. Raises typer.Exit if not found."""
    from paude.cli import find_session_backend

    result = find_session_backend(
        session_name,
        openshift_context,
        openshift_namespace,
        connect_timeout=connect_timeout,
    )
    if result is None:
        typer.echo(f"Error: Session '{session_name}' not found.", err=True)
        raise typer.Exit(1)

    backend_type, backend = result[0], result[1]
    session = backend.get_session(session_name)
    if session is None:
        typer.echo(f"Error: Session '{session_name}' not found.", err=True)
        raise typer.Exit(1)

    return backend_type, backend, session


def _ensure_remote_exists(
    session_name: str,
    backend_type: str,
    backend: Backend,
    workspace: Path,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> str:
    """Ensure a paude git remote exists, auto-adding if needed."""
    from paude.git_remote import (
        build_openshift_remote_url,
        build_podman_remote_url,
        enable_ext_protocol,
        git_remote_add,
        initialize_container_workspace,
        is_ext_protocol_allowed,
        list_paude_remotes,
        openshift_exec_builder,
        podman_exec_builder,
    )

    remote_name = resource_name(session_name)

    for name, _url in list_paude_remotes():
        if name == remote_name:
            return remote_name

    typer.echo(f"Adding git remote '{remote_name}'...", err=True)

    if not is_ext_protocol_allowed():
        if not enable_ext_protocol():
            typer.echo("Error: Failed to enable git ext:: protocol.", err=True)
            raise typer.Exit(1)

    cname = resource_name(session_name)

    if not is_local_backend(backend_type):
        from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig

        os_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )
        try:
            os_backend = OpenShiftBackend(config=os_config)
            namespace = os_backend.namespace
        except Exception:
            namespace = openshift_namespace or "default"

        pname = pod_name(session_name)
        exec_builder = openshift_exec_builder(pname, namespace, openshift_context)
        initialize_container_workspace(exec_builder)
        remote_url = build_openshift_remote_url(
            pname, namespace, context=openshift_context
        )
    else:
        engine = engine_binary_for_backend(backend_type)
        exec_builder = podman_exec_builder(cname, engine)
        initialize_container_workspace(exec_builder)
        remote_url = build_podman_remote_url(cname, engine=engine)

    if not git_remote_add(remote_name, remote_url):
        typer.echo(f"Error: Failed to add remote '{remote_name}'.", err=True)
        raise typer.Exit(1)

    return remote_name


def _get_container_branch(backend: Backend, session_name: str) -> str:
    """Query the current branch inside a session's container."""
    rc, stdout, stderr = backend.exec_in_session(
        session_name,
        "git -C /pvc/workspace rev-parse --abbrev-ref HEAD",
    )
    if rc != 0:
        typer.echo(
            f"Error: Failed to get branch from container: {stderr.strip()}",
            err=True,
        )
        raise typer.Exit(1)
    return stdout.strip()


def harvest_session(
    session_name: str,
    branch_name: str,
    create_pr: bool = False,
    pr_title: str | None = None,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> None:
    """Harvest changes from a running session into a local branch."""
    from paude.git_remote import git_diff_stat, git_fetch_from_remote

    _validate_harvest_branch(branch_name)

    from paude.cli.helpers import record_session_access

    record_session_access(session_name)

    backend_type, backend, session = _find_backend_and_session(
        session_name, openshift_context, openshift_namespace
    )

    workspace = session.workspace
    if not (workspace / ".git").exists():
        typer.echo(
            f"Error: Workspace '{workspace}' is not a git repository "
            f"(missing .git directory or file).",
            err=True,
        )
        raise typer.Exit(1)

    remote_name = _ensure_remote_exists(
        session_name,
        backend_type,
        backend,
        workspace,
        openshift_context,
        openshift_namespace,
    )

    container_branch = _get_container_branch(backend, session_name)
    typer.echo(f"Container is on branch '{container_branch}'.", err=True)

    typer.echo(f"Fetching from '{remote_name}'...", err=True)
    if not git_fetch_from_remote(remote_name, cwd=workspace):
        typer.echo("Error: Failed to fetch from remote.", err=True)
        raise typer.Exit(1)

    remote_ref = f"{remote_name}/{container_branch}"
    typer.echo(f"Resetting '{branch_name}' to '{remote_ref}'...", err=True)
    result = subprocess.run(
        ["git", "checkout", "-B", branch_name, remote_ref],
        capture_output=True,
        text=True,
        cwd=workspace,
    )
    if result.returncode != 0:
        typer.echo(
            f"Error: Failed to reset branch: {result.stderr.strip()}",
            err=True,
        )
        raise typer.Exit(1)

    stat = git_diff_stat("main", branch_name, cwd=workspace)
    if stat:
        typer.echo("")
        typer.echo(stat)

    typer.echo(f"Harvested changes to branch '{branch_name}'.", err=True)

    if create_pr:
        # Fetch origin so --force-with-lease has current ref info
        subprocess.run(
            ["git", "fetch", "origin"],
            capture_output=True,
            cwd=workspace,
        )
        typer.echo(f"Pushing '{branch_name}' to origin...", err=True)
        push_result = subprocess.run(
            ["git", "push", "--force-with-lease", "-u", "origin", branch_name],
            cwd=workspace,
        )
        if push_result.returncode != 0:
            typer.echo("Error: Failed to push branch to origin.", err=True)
            raise typer.Exit(1)

        # Check if an open PR already exists for this branch
        view_result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch_name,
                "--state",
                "open",
                "--json",
                "url",
                "-q",
                ".[0].url",
            ],
            capture_output=True,
            text=True,
            cwd=workspace,
        )
        if view_result.returncode == 0 and view_result.stdout.strip():
            pr_url = view_result.stdout.strip()
            typer.echo(f"PR already exists and updated: {pr_url}", err=True)
        else:
            typer.echo("Creating PR...", err=True)
            pr_cmd = ["gh", "pr", "create", "--head", branch_name]
            if pr_title:
                pr_cmd += ["--title", pr_title]
            pr_result = subprocess.run(pr_cmd, cwd=workspace)
            if pr_result.returncode != 0:
                typer.echo("Error: Failed to create PR.", err=True)
                raise typer.Exit(1)


def status_sessions(
    session_name: str | None = None,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> None:
    """Display enriched status for all sessions, or a single named session."""
    from paude.session_status import (
        SessionActivity,
        WorkSummary,
        format_work_summary,
        get_session_enrichment,
    )

    if session_name:
        from paude.cli.helpers import record_session_access
        from paude.transport.ssh import SSH_STATUS_TIMEOUT

        record_session_access(session_name)

        _btype, found_backend, found_session = _find_backend_and_session(
            session_name,
            openshift_context,
            openshift_namespace,
            connect_timeout=SSH_STATUS_TIMEOUT,
        )
        all_merged = [found_session]
        backend_by_name: dict[str, Backend] = {found_session.name: found_backend}
    else:
        from paude.registry import SessionRegistry, merge_registry_with_live
        from paude.session_discovery import collect_all_sessions

        live_results, reachable_backends = collect_all_sessions(
            openshift_context=openshift_context,
            openshift_namespace=openshift_namespace,
        )
        registry = SessionRegistry()
        live_sessions = [s for s, _b in live_results]
        all_merged = merge_registry_with_live(
            registry, live_sessions, reachable_backends
        )
        backend_by_name = {s.name: b for s, b in live_results}

    if not all_merged:
        typer.echo("No sessions found.")
        return

    # Separate running sessions (enrichable) from others
    running_rows: list[
        tuple[Session, str, SessionActivity | None, WorkSummary | None]
    ] = []
    other_rows: list[tuple[Session, str]] = []

    # Enrich running sessions concurrently
    with ThreadPoolExecutor(max_workers=8) as pool:
        enrichment_futures = []
        for session in all_merged:
            if session.status not in ("running", "degraded"):
                other_rows.append((session, session.backend_type))
                continue
            backend = backend_by_name.get(session.name)
            if not backend:
                running_rows.append((session, session.backend_type, None, None))
                continue
            enrichment_futures.append(
                (
                    session,
                    pool.submit(
                        get_session_enrichment,
                        backend,
                        session.name,
                        agent_name=session.agent,
                    ),
                )
            )

        for session, fut in enrichment_futures:
            try:
                activity, summary = fut.result()
            except Exception:  # noqa: S110
                activity, summary = None, None
            running_rows.append((session, session.backend_type, activity, summary))

    def _sort_key(
        r: tuple[Session, str, SessionActivity | None, WorkSummary | None],
    ) -> float:
        activity = r[2]
        if activity and activity.elapsed_seconds is not None:
            return float(activity.elapsed_seconds)
        return float("inf")

    running_rows.sort(key=_sort_key)

    fixed_width = 20 + 15 + 10 + 10 + 10 + 5  # columns + spaces before SUMMARY
    term_width = shutil.get_terminal_size((80, 24)).columns
    summary_width = max(30, term_width - fixed_width)

    cols = (
        f"{'SESSION':<20} {'PROJECT':<15} {'BACKEND':<10} "
        f"{'ACTIVITY':<10} {'STATE':<10} {'SUMMARY'}"
    )
    typer.echo(cols)
    typer.echo("-" * len(cols))

    for session, backend_type, activity, summary in running_rows:
        project = session.workspace.name if session.workspace else ""
        act_str = activity.last_activity if activity else ""
        state_str = activity.state if activity else ""
        summary_str = format_work_summary(summary, max_width=summary_width)

        typer.echo(
            f"{session.name:<20} {project:<15} {backend_type:<10} "
            f"{act_str:<10} {state_str:<10} {summary_str}"
        )

    for session, backend_type in other_rows:
        project = session.workspace.name if session.workspace else ""
        typer.echo(
            f"{session.name:<20} {project:<15} {backend_type:<10} "
            f"{'':<10} {session.status:<10}"
        )


def reset_session(
    session_name: str,
    branch: str = "main",
    force: bool = False,
    keep_conversation: bool = False,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> None:
    """Reset a session's workspace for a new task."""
    from paude.cli.helpers import record_session_access

    record_session_access(session_name)

    _backend_type, backend, session = _find_backend_and_session(
        session_name, openshift_context, openshift_namespace
    )

    if session.status != "running":
        typer.echo(
            f"Error: Session '{session_name}' is not running. "
            f"Use 'paude start {session_name}' first.",
            err=True,
        )
        raise typer.Exit(1)

    # Re-resolve origin from the host repo's branch tracking remote
    from paude.git_remote import resolve_origin_cmd

    set_origin_cmd = resolve_origin_cmd(cwd=session.workspace)
    if set_origin_cmd:
        backend.exec_in_session(session_name, set_origin_cmd)

    if not force:
        _check_unmerged_work(backend, session_name, branch)

    typer.echo(f"Resetting workspace to '{branch}'...", err=True)
    quoted_branch = shlex.quote(branch)
    ws = CONTAINER_WORKSPACE
    reset_cmd = (
        f"git -C {ws} fetch origin 2>/dev/null; "
        f"git -C {ws} checkout {quoted_branch} 2>/dev/null; "
        f"git -C {ws} reset --hard origin/{quoted_branch} "
        f"2>/dev/null || "
        f"git -C {ws} reset --hard HEAD; "
        f"git -C {ws} clean -fdx && "
        f"git -C {ws} update-ref {BASE_REF_NAME} HEAD"
    )
    rc, _stdout, stderr = backend.exec_in_session(session_name, reset_cmd)
    if rc != 0:
        typer.echo(
            f"Error: Failed to reset workspace: {stderr.strip()}",
            err=True,
        )
        raise typer.Exit(1)

    if not keep_conversation:
        from paude.agents import get_agent

        agent = get_agent(session.agent, provider=session.provider)
        agent_cfg = agent.config
        config_dir = f"{CONTAINER_HOME}/{agent_cfg.config_dir_name}"

        typer.echo(
            "Clearing conversation history and sending clear command...",
            err=True,
        )
        # Delete conversation history but preserve per-project settings
        # (settings.local.json, CLAUDE.md), then send clear command to agent
        clear_cmd = (
            f"find {config_dir}/projects/ "
            r"\( -name '*.jsonl' -o -name 'sessions-index.json' \) "
            "-delete 2>/dev/null; "
            f"find {config_dir}/projects/ -mindepth 2 -maxdepth 2 -type d "
            "-exec rm -rf {} + 2>/dev/null; "
            f"rm -rf {config_dir}/todos/; "
        )
        if agent_cfg.clear_command:
            clear_cmd += (
                f"tmux send-keys -t {agent_cfg.session_name}"
                f' -l "{agent_cfg.clear_command}"; '
                f"sleep 0.1; "
                f"tmux send-keys -t {agent_cfg.session_name} Enter"
            )
        backend.exec_in_session(session_name, clear_cmd)

    typer.echo(f"Session '{session_name}' reset to '{branch}'.", err=True)


def _check_unmerged_work(
    backend: Backend,
    session_name: str,
    branch: str = "main",
) -> None:
    """Check if session has unmerged work and warn the user."""
    # Fetch origin and check if HEAD is an ancestor of origin/<branch>
    rc, _, _ = backend.exec_in_session(
        session_name,
        "git -C /pvc/workspace fetch origin 2>/dev/null"
        f" && git -C /pvc/workspace merge-base --is-ancestor HEAD origin/{branch}",
    )
    if rc == 0:
        # HEAD is already in origin/main — nothing unmerged
        return

    # There's diverged work — get latest commit for the warning message
    rc, stdout, _ = backend.exec_in_session(
        session_name,
        "git -C /pvc/workspace log --oneline -1 HEAD",
    )
    latest = stdout.strip() if rc == 0 else "unknown"
    typer.echo("Warning: Session has work that may not be harvested.", err=True)
    typer.echo(f"  Latest commit: {latest}", err=True)
    typer.echo(
        "  Use --force to skip this check, or 'paude harvest' first.",
        err=True,
    )
    raise typer.Exit(1)


_EVENTS_FILE = ".paude-events.jsonl"


def _parse_stream_event(line: str) -> str:
    """Convert a Claude stream-json event line to a human-readable summary.

    Returns an empty string for events that are not worth printing (e.g.
    internal system events). Returns the raw line if it cannot be parsed.
    """
    import json

    try:
        event = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return line  # not JSON — pass through as-is (e.g. [paude] agent exited)

    etype = event.get("type", "")

    if etype == "assistant":
        # Agent message — extract text content blocks
        content = event.get("message", {}).get("content", [])
        parts = []
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", "").strip())
                elif block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    # Summarise common tool calls concisely
                    if name in ("Write", "StrReplace", "EditNotebook"):
                        path = inp.get("path", inp.get("target_notebook", "?"))
                        parts.append(f"[tool:{name}] {path}")
                    elif name in ("Read", "Glob", "Grep"):
                        target = inp.get("path", inp.get("glob_pattern", inp.get("pattern", "?")))
                        parts.append(f"[tool:{name}] {target}")
                    elif name == "Shell":
                        cmd = inp.get("command", "?")[:80]
                        parts.append(f"[tool:Shell] {cmd}")
                    else:
                        parts.append(f"[tool:{name}]")
        return "\n".join(p for p in parts if p)

    if etype == "result":
        cost = event.get("cost_usd")
        turns = event.get("num_turns")
        cost_str = f"  cost=${cost:.4f}" if cost else ""
        turns_str = f"  turns={turns}" if turns else ""
        return f"[result] subtype={event.get('subtype', '?')}{turns_str}{cost_str}"

    if etype == "system" and event.get("subtype") == "init":
        model = event.get("model", "?")
        return f"[init] model={model}"

    # Skip noisy internal events silently
    return ""


def tail_session(
    session_name: str,
    lines: int = 50,
    follow: bool = False,
    interval: float = 3.0,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> None:
    """Print the last N lines of the agent's output from a running session.

    Prefers ``.paude-events.jsonl`` (structured Claude stream-json output,
    available in headless Claude sessions) and falls back to ``tmux
    capture-pane`` for interactive or non-Claude sessions.

    In follow mode (``-f``), polls at ``interval`` seconds and prints new
    lines as they appear, similar to ``tail -f``. Exits cleanly on Ctrl+C.
    """
    from paude.cli.helpers import record_session_access

    record_session_access(session_name)

    _backend_type, backend, _session = _find_backend_and_session(
        session_name, openshift_context, openshift_namespace
    )

    def _events_file_exists() -> bool:
        rc, _, _ = backend.exec_in_session(
            session_name,
            f"test -s ${{PAUDE_WORKSPACE:-/workspace}}/{_EVENTS_FILE}",
        )
        return rc == 0

    def _read_events(max_lines: int = 2000) -> list[str]:
        rc, stdout, _ = backend.exec_in_session(
            session_name,
            f"tail -n {max_lines} ${{PAUDE_WORKSPACE:-/workspace}}/{_EVENTS_FILE} 2>/dev/null",
        )
        if rc != 0 or not stdout.strip():
            return []
        parsed = []
        for raw in stdout.splitlines():
            rendered = _parse_stream_event(raw)
            if rendered:
                parsed.append(rendered)
        return parsed

    def _capture_tmux(max_lines: int = 2000) -> list[str]:
        rc, stdout, _ = backend.exec_in_session(
            session_name,
            f"tmux capture-pane -p -S -{max_lines} 2>/dev/null",
        )
        if rc != 0 or not stdout.strip():
            return []
        return stdout.splitlines()

    use_events = _events_file_exists()
    source_label = "stream-json events" if use_events else "tmux buffer"
    _capture = _read_events if use_events else _capture_tmux

    if not follow:
        all_lines = _capture(max(lines * 2, 500))
        if not all_lines:
            typer.echo(
                "No output found — session may be stopped, stale, or tmux is not running.",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo("\n".join(all_lines[-lines:]))
        return

    # Follow mode: print initial tail, then stream new lines as they appear.
    typer.echo(
        f"Tailing '{session_name}' via {source_label} (interval: {interval}s) — Ctrl+C to stop",
        err=True,
    )
    seen: list[str] = []
    first = True
    try:
        while True:
            current = _capture()
            if first:
                to_print = current[-lines:] if current else []
                if to_print:
                    typer.echo("\n".join(to_print))
                seen = current
                first = False
            else:
                if len(current) > len(seen):
                    # Normal case: output grew — print only the new lines.
                    typer.echo("\n".join(current[len(seen) :]))
                    seen = current
                elif current != seen:
                    # Content changed in-place — reprint the tail.
                    typer.echo("---")
                    typer.echo("\n".join(current[-lines:]))
                    seen = current
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("", err=True)


def _send_notification(title: str, message: str, enabled: bool) -> None:
    """Send a desktop notification if enabled and notify-send is available."""
    if not enabled:
        return
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", title, message], capture_output=True)


def wait_session(
    session_name: str,
    interval: int = 30,
    timeout_minutes: int = 60,
    on_idle: str | None = None,
    send_notify: bool = True,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> None:
    """Poll a session until it reaches Idle state, then optionally run a command.

    Prints a live status line showing elapsed time and current state.
    Sends a desktop notification (via notify-send) when Idle, if available.

    Exit codes:
        0 — session reached Idle state
        1 — timed out before reaching Idle
    """
    from paude.cli.helpers import record_session_access
    from paude.session_status import get_session_enrichment

    record_session_access(session_name)

    _backend_type, backend, session = _find_backend_and_session(
        session_name, openshift_context, openshift_namespace
    )
    agent_name = session.agent or "claude"

    timeout_sec = timeout_minutes * 60 if timeout_minutes > 0 else None
    start = time.time()

    typer.echo(
        f"Watching '{session_name}' "
        f"(interval: {interval}s"
        + (f", timeout: {timeout_minutes}m" if timeout_sec else "")
        + ") — Ctrl+C to stop",
        err=True,
    )

    while True:
        elapsed = int(time.time() - start)

        if timeout_sec and elapsed >= timeout_sec:
            typer.echo("")
            msg = f"Timed out after {timeout_minutes}m waiting for '{session_name}' to become Idle"
            typer.echo(msg, err=True)
            _send_notification("Paude: Timeout", msg, send_notify)
            raise typer.Exit(1)

        try:
            activity, summary = get_session_enrichment(
                backend, session_name, agent_name=agent_name
            )
        except Exception as exc:  # noqa: BLE001
            typer.echo(
                f"\rWarning: could not read session state ({exc}), retrying...",
                err=True,
            )
            time.sleep(interval)
            continue

        elapsed_str = (
            f"{elapsed // 60}m{elapsed % 60:02d}s" if elapsed >= 60 else f"{elapsed}s"
        )
        commits_info = (
            f" (+{summary.commits_ahead} commit(s))"
            if summary and summary.commits_ahead > 0
            else ""
        )
        typer.echo(
            f"\r[{elapsed_str}] {activity.state}{commits_info}   ",
            nl=False,
            err=True,
        )

        if activity.state == "Idle":
            typer.echo("", err=True)  # newline after the live \r line
            if summary and summary.commits_ahead > 0:
                msg = (
                    f"Session '{session_name}' is Idle — "
                    f"{summary.commits_ahead} commit(s) ready to harvest"
                )
            else:
                msg = (
                    f"Session '{session_name}' is Idle — "
                    "no new commits (agent may have stalled; connect and check)"
                )
            typer.echo(msg)
            _send_notification("Paude: Idle", msg, send_notify)

            if on_idle:
                typer.echo(f"Running: {on_idle}")
                result = subprocess.run(on_idle, shell=True)  # noqa: S602
                raise typer.Exit(result.returncode)
            return

        time.sleep(interval)


# ---------------------------------------------------------------------------
# Task YAML + claim evaluation
# ---------------------------------------------------------------------------


def _load_task(task_file: Path) -> dict:
    """Load and minimally validate a task YAML file."""
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        typer.echo("Error: PyYAML is required for task files (pip install pyyaml).", err=True)
        raise typer.Exit(1)

    with task_file.open() as fh:
        data = yaml.safe_load(fh)

    for field in ("id", "spec_file"):
        if field not in data:
            typer.echo(f"Error: task file missing required field '{field}'.", err=True)
            raise typer.Exit(1)

    return data


def _check_file_exists(path: Path) -> tuple[bool, str]:
    return path.exists(), str(path)


def _check_file_contains(path: Path, text: str) -> tuple[bool, str]:
    if not path.exists():
        return False, f"{path} does not exist"
    content = path.read_text(errors="replace")
    found = text in content
    return found, f"'{text}' {'found' if found else 'not found'} in {path}"


def _check_git_committed(workspace: Path) -> tuple[bool, str]:
    """Return True if at least one commit exists beyond the paude base ref."""
    result = subprocess.run(
        ["git", "log", "refs/paude/base..HEAD", "--oneline"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    commits = [line for line in result.stdout.splitlines() if line.strip()]
    if commits:
        return True, f"{len(commits)} commit(s) found: {commits[0]}"
    # Fall back to checking for any commit if base ref doesn't exist
    result2 = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    if result2.stdout.strip():
        return True, f"commit found: {result2.stdout.strip()}"
    return False, "no commits found (agent may not have committed)"


def evaluate_claims(
    task: dict,
    workspace: Path,
    session_name: str,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> list[dict]:
    """Evaluate the claims in a task definition against the harvested workspace.

    Returns a list of result dicts with keys: id, check, result (pass/fail), detail.
    """
    claims = task.get("claims", [])
    if not claims:
        typer.echo("No claims defined in task — skipping verification.", err=True)
        return []

    results = []
    for claim in claims:
        cid = claim.get("id", "?")
        check = claim.get("check", "")
        detail = ""
        passed = False
        agent_response: str | None = None

        if check == "git_committed":
            passed, detail = _check_git_committed(workspace)

        elif check == "file_exists":
            path = workspace / claim["path"]
            passed, detail = _check_file_exists(path)

        elif check == "file_contains":
            path = workspace / claim["path"]
            passed, detail = _check_file_contains(path, claim.get("contains", ""))

        elif check == "agent_review":
            prompt = claim.get("prompt", "")
            if not prompt:
                passed, detail = False, "agent_review claim has no prompt"
            else:
                passed, detail, agent_response = _run_agent_review(
                    prompt,
                    workspace,
                    session_name,
                    openshift_context=openshift_context,
                    openshift_namespace=openshift_namespace,
                )
        else:
            passed, detail = False, f"unknown check type '{check}'"

        row: dict = {
            "id": cid,
            "check": check,
            "result": "pass" if passed else "fail",
            "detail": detail,
        }
        if agent_response is not None:
            row["agent_response"] = agent_response

        icon = "✓" if passed else "✗"
        typer.echo(f"  {icon} {cid} [{check}]: {detail}")
        results.append(row)

    return results


def _run_agent_review(
    prompt: str,
    workspace: Path,
    parent_session: str,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> tuple[bool, str, str]:
    """Run a short Claude session to evaluate a single agent_review claim.

    Spins up a throwaway paude session with a focused yes/no prompt, reads
    the answer from .paude-events.jsonl or AGENT-NOTES.md, then cleans up.

    Returns (passed, detail, raw_response).
    """
    import os as _os

    review_session = f"{parent_session}-vfy-{int(time.time()) % 100000}"
    review_branch = f"paude-review-{review_session}"

    full_prompt = (
        f"{prompt.strip()}\n\n"
        "Answer with exactly one of: YES or NO, followed by a brief explanation "
        "on the same line. Example: YES — the file contains the required content.\n"
        "After answering, run: git add -A && git commit -m 'verify: claim check'"
    )

    spec_file = workspace / ".paude-review-prompt.txt"
    spec_file.write_text(full_prompt)

    typer.echo(
        f"  [agent_review] launching '{review_session}'...",
        err=True,
    )

    env = {**_os.environ, "PAUDE_DEV": "1"}
    raw_answer = ""

    try:
        create = subprocess.run(
            ["paude", "create", review_session, "--git", "--yolo",
             "--prompt-file", str(spec_file)],
            cwd=str(workspace),
            env=env,
        )
        if create.returncode != 0:
            return False, f"review session create failed", ""

        subprocess.run(
            ["paude", "wait", review_session, "--timeout", "10"],
            cwd=str(workspace),
        )

        subprocess.run(
            ["paude", "harvest", review_session, "-b", review_branch],
            cwd=str(workspace),
        )

        # Read the answer from events file or AGENT-NOTES
        events_path = workspace / _EVENTS_FILE
        if events_path.exists():
            for line in reversed(events_path.read_text().splitlines()):
                rendered = _parse_stream_event(line)
                if rendered and not rendered.startswith("["):
                    raw_answer = rendered
                    break

        if not raw_answer:
            notes = workspace / "AGENT-NOTES.md"
            if notes.exists():
                raw_answer = notes.read_text()[:500]

        upper = raw_answer.upper()
        if upper.startswith("YES"):
            return True, f"agent review: {raw_answer[:150]}", raw_answer
        elif upper.startswith("NO"):
            return False, f"agent review: {raw_answer[:150]}", raw_answer
        else:
            return False, f"agent review: unclear — {raw_answer[:150]}", raw_answer

    finally:
        spec_file.unlink(missing_ok=True)
        subprocess.run(["paude", "delete", review_session, "--confirm"], capture_output=True)
        subprocess.run(["git", "branch", "-D", review_branch],
                       cwd=str(workspace), capture_output=True)


def run_task(
    task_file: Path,
    session_name: str | None = None,
    result_file: Path | None = None,
    harvest_branch: str | None = None,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> None:
    """Execute a task YAML end-to-end: create → wait → harvest → evaluate claims.

    Writes a JSON result file if ``result_file`` is provided.
    Exit code 0 = all claims pass. Exit code 1 = claims failed. Exit code 2 = execution error.
    """
    import json as _json
    import os as _os

    task = _load_task(task_file)
    task_id = task["id"]

    spec_path = task_file.parent / task["spec_file"]
    if not spec_path.exists():
        typer.echo(f"Error: spec_file '{spec_path}' not found.", err=True)
        raise typer.Exit(2)

    # Use a timestamp suffix so concurrent or retried runs never collide.
    # Cleanup is deferred to the janitor — callers don't need to delete
    # sessions before re-running the same task.
    import datetime as _dt
    run_ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    session = session_name or f"task-{task_id}-{run_ts}"
    branch = harvest_branch or f"task/{task_id}-{run_ts}"
    workspace = Path.cwd()

    typer.echo(f"[run] task={task_id}  session={session}  branch={branch}")
    start = time.time()

    env = {**_os.environ, "PAUDE_DEV": "1"}

    # Create session
    typer.echo("[run] creating session...")
    create_result = subprocess.run(
        ["paude", "create", session, "--git", "--yolo",
         "--prompt-file", str(spec_path)],
        cwd=str(workspace),
        env=env,
    )
    if create_result.returncode != 0:
        typer.echo(f"[run] session create failed", err=True)
        raise typer.Exit(2)

    # Wait for idle
    timeout = task.get("timeout_minutes", 60)
    typer.echo(f"[run] waiting for idle (timeout: {timeout}m)...")
    try:
        wait_session(
            session_name=session,
            timeout_minutes=timeout,
            send_notify=False,
            openshift_context=openshift_context,
            openshift_namespace=openshift_namespace,
        )
    except SystemExit as exc:
        if exc.code != 0:
            typer.echo("[run] wait timed out — harvesting anyway", err=True)

    # Harvest
    typer.echo(f"[run] harvesting to '{branch}'...")
    harvest_result = subprocess.run(
        ["paude", "harvest", session, "-b", branch],
        cwd=str(workspace),
    )
    if harvest_result.returncode != 0:
        typer.echo("[run] harvest failed", err=True)
        raise typer.Exit(2)

    # Evaluate claims
    typer.echo("[run] evaluating claims...")
    claim_results = evaluate_claims(
        task, workspace, session,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
    )

    elapsed = int(time.time() - start)
    all_passed = all(r["result"] == "pass" for r in claim_results)
    overall = "pass" if all_passed else "fail"
    passed_count = sum(1 for r in claim_results if r["result"] == "pass")

    typer.echo(
        f"\n[run] result={overall}  elapsed={elapsed}s  "
        f"claims={passed_count}/{len(claim_results)} passed"
    )

    output = {
        "task_id": task_id,
        "session": session,
        "harvest_branch": branch,
        "status": overall,
        "elapsed_seconds": elapsed,
        "claims": claim_results,
    }
    if result_file:
        result_file.write_text(_json.dumps(output, indent=2))
        typer.echo(f"[run] result written to {result_file}")

    raise typer.Exit(0 if all_passed else 1)
