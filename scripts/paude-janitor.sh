#!/usr/bin/env bash
# paude-janitor — sweep all stale Paude resources
#
# Run this periodically (or manually) instead of cleaning up after every run.
# Safe to run at any time; skips resources that are still active.
#
# Usage:
#   ./paude-janitor.sh          # dry run — prints what would be removed
#   ./paude-janitor.sh --apply  # actually remove everything stale
#
set -euo pipefail

DRY_RUN=true
if [[ "${1:-}" == "--apply" ]]; then
    DRY_RUN=false
fi

WORKSPACE="${PAUDE_JANITOR_WORKSPACE:-$HOME/gemini-workspace}"

log()   { echo "  $*"; }
run()   { if $DRY_RUN; then echo "  [dry] $*"; else eval "$@"; fi; }
header(){ echo; echo "=== $* ==="; }

header "Paude Janitor"
$DRY_RUN && echo "  (dry run — pass --apply to actually remove things)"

# ── 1. Stopped/dead paude containers ─────────────────────────────────────────
header "Stopped paude containers"
stopped=$(podman ps -a --filter label=app=paude --filter status=exited --filter status=dead -q 2>/dev/null || true)
if [[ -z "$stopped" ]]; then
    log "none"
else
    for cid in $stopped; do
        name=$(podman inspect --format '{{.Name}}' "$cid" 2>/dev/null | tr -d '/')
        log "remove container: $name ($cid)"
        run "podman rm -f '$cid'"
    done
fi

# ── 2. Orphaned paude volumes (no matching live container) ────────────────────
header "Orphaned paude volumes"
all_volumes=$(podman volume ls --filter label=app=paude -q 2>/dev/null || true)
live_containers=$(podman ps --filter label=app=paude --format '{{.Names}}' 2>/dev/null || true)

for vol in $all_volumes; do
    # Extract session name from volume name (strip paude- prefix and -workspace/-ca suffix)
    session_hint=$(echo "$vol" | sed 's/^paude-//; s/-workspace$//; s/-ca$//')
    if echo "$live_containers" | grep -q "$session_hint"; then
        log "skip (live): $vol"
    else
        log "remove volume: $vol"
        run "podman volume rm --force '$vol'"
    fi
done

if [[ -z "$all_volumes" ]]; then
    log "none"
fi

# ── 3. Orphaned paude networks ────────────────────────────────────────────────
header "Orphaned paude networks"
all_nets=$(podman network ls --filter label=app=paude -q 2>/dev/null || true)

for net in $all_nets; do
    net_name=$(podman network inspect --format '{{.Name}}' "$net" 2>/dev/null || echo "$net")
    # Check if any live container uses this network
    containers_on_net=$(podman ps --filter network="$net_name" -q 2>/dev/null || true)
    if [[ -n "$containers_on_net" ]]; then
        log "skip (in use): $net_name"
    else
        log "remove network: $net_name"
        run "podman network rm '$net_name'"
    fi
done

if [[ -z "$all_nets" ]]; then
    log "none"
fi

# ── 4. Stale git remotes in the workspace ────────────────────────────────────
header "Stale paude git remotes"
if [[ -d "$WORKSPACE/.git" ]] || [[ -f "$WORKSPACE/.git" ]]; then
    stale_remotes=$(git -C "$WORKSPACE" remote -v 2>/dev/null \
        | grep 'paude-' | awk '{print $1}' | sort -u || true)

    for remote in $stale_remotes; do
        # Extract container name from remote URL
        container=$(git -C "$WORKSPACE" remote get-url "$remote" 2>/dev/null \
            | sed 's/ext::podman exec -i //; s/ %S.*//' || true)
        if podman inspect "$container" &>/dev/null 2>&1; then
            log "skip (container alive): $remote"
        else
            log "remove remote: $remote"
            run "git -C '$WORKSPACE' remote remove '$remote'"
        fi
    done

    if [[ -z "${stale_remotes:-}" ]]; then
        log "none"
    fi
else
    log "workspace not found: $WORKSPACE"
fi

# ── 5. Stale entries in paude sessions registry ───────────────────────────────
header "Stale paude session registry entries"
SESSIONS_FILE="$HOME/.config/paude/sessions.json"
if [[ -f "$SESSIONS_FILE" ]]; then
    # Pass the filename as argv[1] BEFORE the heredoc — placing it after EOF
    # causes bash to treat it as a separate command to execute.
    stale_keys=$(python3 - "$SESSIONS_FILE" <<'EOF'
import json, subprocess, sys
path = sys.argv[1]
try:
    with open(path) as f:
        text = f.read().strip()
    data = json.loads(text) if text else {}
except (json.JSONDecodeError, OSError):
    data = {}
sessions = data.get('sessions', {})
stale = []
for name in sessions:
    result = subprocess.run(
        ['podman', 'inspect', f'paude-{name}'],
        capture_output=True
    )
    if result.returncode != 0:
        stale.append(name)
print('\n'.join(stale))
EOF
)

    if [[ -z "$stale_keys" ]]; then
        log "none"
    else
        for key in $stale_keys; do
            log "remove registry entry: $key"
            if ! $DRY_RUN; then
                python3 - "$SESSIONS_FILE" "$key" <<'PYEOF'
import json, sys, os, tempfile
path, key = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        text = f.read().strip()
    d = json.loads(text) if text else {}
except (json.JSONDecodeError, OSError):
    d = {}
d.get('sessions', {}).pop(key, None)
tmp = path + '.tmp'
with open(tmp, 'w') as f:
    json.dump(d, f, indent=2)
os.replace(tmp, path)
PYEOF
            fi
        done
    fi
else
    log "no sessions file found"
fi

# ── 6. Completed worktrees (merged branches) ──────────────────────────────────
header "Merged worktrees (task/ branches)"
if [[ -d "$WORKSPACE/worktrees" ]]; then
    for wt in "$WORKSPACE"/worktrees/*/; do
        [[ -d "$wt" ]] || continue
        slug=$(basename "$wt")
        branch=$(git -C "$wt" branch --show-current 2>/dev/null || echo "")
        if [[ -z "$branch" ]]; then
            log "skip (detached HEAD): $slug"
            continue
        fi
        # Check if branch is fully merged into main
        merged=$(git -C "$WORKSPACE" branch --merged main 2>/dev/null | grep -w "$branch" || true)
        if [[ -n "$merged" ]]; then
            log "remove merged worktree: $slug ($branch)"
            run "git -C '$WORKSPACE' worktree remove '$wt' --force"
            run "git -C '$WORKSPACE' branch -d '$branch'"
        else
            log "skip (not merged): $slug ($branch)"
        fi
    done
else
    log "no worktrees directory"
fi

header "Done"
$DRY_RUN && echo "  Run with --apply to execute removals."
