# GitHub Copilot CLI — Setup and Test Guide

This guide walks through setting up and testing the GitHub Copilot CLI agent on a
new machine using this fork of paude. It is written to be executed step-by-step
by a coding agent or a human.

---

## Prerequisites

### 1. Container runtime

You need Podman **or** Docker. Verify one is available:

```bash
podman --version || docker --version
```

If neither is installed, install Podman:

```bash
# RHEL / Fedora / CentOS Stream
sudo dnf install -y podman

# Debian / Ubuntu
sudo apt-get install -y podman
```

### 2. Python 3.11+

```bash
python3 --version
```

If older than 3.11, install a newer version via your system package manager or
[pyenv](https://github.com/pyenv/pyenv).

### 3. uv

```bash
uv --version
```

If not installed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env   # or restart your shell
```

### 4. Git

```bash
git --version
```

---

## Install paude from the fork

Clone the fork and install in development mode. This picks up the Copilot agent
code directly from the source tree.

```bash
git clone https://github.com/hhellbusch/paude.git
cd paude
uv venv --python 3.12 --seed
source .venv/bin/activate
pip install -e .
```

Verify the install and confirm `copilot` appears in the agent list:

```bash
paude --help | grep -A5 "agent"
paude create --help | grep copilot
```

You should see `copilot` listed as a valid `--agent` value.

---

## Authentication

### GitHub token with Copilot access

The `copilot` binary authenticates via environment variable. You need a
**fine-grained personal access token** with the **Copilot Requests** permission.

1. Go to https://github.com/settings/personal-access-tokens/new
2. Under **Resource owner**, select your **personal account** (not an org — the
   Copilot Requests permission is only available on user-owned tokens).
3. Under **Repository access**, choose the level appropriate for your use case.
4. Under **Permissions → Account**, add **Copilot Requests**.
5. Generate and copy the token.

Export it in your shell (add to `~/.bashrc` or `~/.zshrc` to persist):

```bash
export COPILOT_GITHUB_TOKEN=github_pat_YOUR_TOKEN_HERE
```

The `copilot` binary checks `COPILOT_GITHUB_TOKEN`, then `GH_TOKEN`, then
`GITHUB_TOKEN` — in that order of precedence.

Verify the variable is set:

```bash
echo "token length: ${#COPILOT_GITHUB_TOKEN}"
```

Should print a non-zero length.

---

## First session

### Navigate to a test repository

```bash
# Use any git repository you want to test against, for example:
cd /path/to/your/project

# Confirm it is a git repository
git status
```

### Create and start the session

```bash
paude create --agent copilot --yolo --git copilot-test
```

What this does:

- `--agent copilot` — uses the GitHub Copilot CLI inside the container
- `--yolo` — passes `--allow-all-tools` to Copilot so it can act without manual
  tool approval at each step
- `--git` — pushes the current branch into the container and sets up the git
  remote in one step
- `copilot-test` — the session name

The first run builds a container image with Node.js 22 and the `copilot` binary
installed. This takes **3–5 minutes**. Subsequent runs start in seconds.

### Connect

```bash
paude connect copilot-test
```

You should see the Copilot CLI interactive interface in your terminal. The session
is running inside a container with network access scoped to GitHub and Copilot
API endpoints.

---

## Verification checklist

Inside the connected session, run these checks:

```bash
# Confirm the copilot binary is present
which copilot
copilot --version

# Confirm Node.js 22+ is installed
node --version   # must be >= 22.0.0

# Confirm the workspace is mounted and the git history is present
ls /workspace
git -C /workspace log --oneline -5

# Confirm authentication (this should NOT prompt for login)
# Just send a trivial prompt — if it responds without /login, auth is working:
# (type this into the copilot interactive prompt)
#   What files are in this project?
```

If Copilot responds without prompting for `/login`, authentication is working
correctly.

---

## Detach and pull changes

After the agent makes commits, detach from the session with **Ctrl+b d** (tmux
detach), then pull the commits locally:

```bash
# Back on the host — pull what the agent committed
git pull paude-copilot-test <branch-name>
```

---

## Troubleshooting

### Node.js version is < 22

The `copilot` npm package requires Node.js 22 or later. If the container image
was built against a base with an older Node.js, force a rebuild:

```bash
paude create --agent copilot --yolo --git --rebuild copilot-test
```

If the rebuild still installs an older Node.js, the base OS image's AppStream
module for `nodejs:22` may not be available. In that case, add a `paude.json` in
the project root to inject a custom Dockerfile layer:

```json
{
  "build": {
    "dockerfile": "Dockerfile.paude"
  }
}
```

```dockerfile
# Dockerfile.paude — override Node.js version before paude's agent install
ARG PAUDE_BASE_IMAGE
FROM ${PAUDE_BASE_IMAGE}
USER root
RUN curl -fsSL https://rpm.nodesource.com/setup_22.x | bash - && \
    dnf install -y nodejs && dnf clean all
```

### Blocked network domains

If the agent reports connection failures, check what the proxy blocked:

```bash
paude blocked-domains copilot-test
```

The Copilot agent should have `api.githubcopilot.com`,
`copilot-proxy.githubusercontent.com`, and `github.com` allowed by default. If
any are missing, add them:

```bash
paude allowed-domains copilot-test --add api.githubcopilot.com
```

### Copilot still prompts for `/login` inside the container

The `COPILOT_GITHUB_TOKEN` was not forwarded. Verify the variable is exported on
the host **before** running `paude create`:

```bash
echo $COPILOT_GITHUB_TOKEN
```

If empty, export it and recreate the session:

```bash
export COPILOT_GITHUB_TOKEN=github_pat_...
paude delete copilot-test --confirm
paude create --agent copilot --yolo --git copilot-test
```

### Workspace trust prompt still appears

The sandbox config pre-trusts the workspace directory in
`~/.copilot/settings.json`. If the prompt still appears, the key name may differ
in your installed version of the CLI. Inside the container:

```bash
copilot help config
cat ~/.copilot/settings.json
```

Report the correct key name so `apply_sandbox_config` in
`src/paude/agents/copilot.py` can be updated.

---

## Cleaning up

```bash
# Stop the session (preserves volume for later)
paude stop copilot-test

# Delete completely (removes volume)
paude delete copilot-test --confirm
```

---

## Notes for enterprise environments

- `COPILOT_GITHUB_TOKEN` takes precedence over `GH_TOKEN` and `GITHUB_TOKEN`.
  Use it explicitly to avoid conflicts with other tooling that sets `GH_TOKEN`.
- If your organization has Copilot CLI disabled in enterprise settings, the token
  will authenticate but the binary will return an authorization error. An
  enterprise admin must enable Copilot CLI at the org or enterprise level.
- Copilot CLI's default model is **Claude Sonnet 4.5**. You can change it per
  session by passing agent args: `paude create --agent copilot -a '--model gpt-4o' ...`
