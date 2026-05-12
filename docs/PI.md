# Pi Agent — Setup and Usage Guide

[Pi](https://github.com/earendil-works/pi) is a minimal terminal coding agent with no built-in permission system. The author's explicit guidance is "run it in a container" — which is exactly what paude provides. Pi is installed automatically inside the container; no local Pi installation is needed.

Pi supports multiple inference backends selectable via `--provider` at session creation time.

Paude does not auto-install optional Pi workflow extensions. If you want additional prompt/context behavior, pass explicit extensions with repeatable `--pi-extension` flags.

---

## Providers

| Provider | Flag | Models | Auth |
|----------|------|--------|------|
| Vertex AI | `--provider vertex` | Claude (Anthropic) + Gemini | `GOOGLE_CLOUD_PROJECT` + ADC |
| Anthropic | `--provider anthropic` | Claude | `ANTHROPIC_API_KEY` |
| Google AI | `--provider google` | Gemini | `GEMINI_API_KEY` |
| GitHub Copilot | `--provider github` | Copilot models | `pi /login` on host |

Default provider when `--provider` is not specified: `anthropic`.

---

## Vertex AI Provider

Pi on Vertex supports both Claude (via the [pi-anthropic-vertex](https://github.com/hhellbusch/pi-anthropic-vertex) extension, installed at image build time) and Gemini (via Pi's built-in `google-vertex` provider). Authentication follows paude's standard Vertex model: ADC credentials are available in the container while network egress remains proxy-filtered.

### Prerequisites

```bash
gcloud auth application-default login

export GOOGLE_CLOUD_PROJECT=your-project-id
export ANTHROPIC_VERTEX_PROJECT_ID=your-project-id  # for Claude models
export CLOUD_ML_REGION=us-east5                     # or your preferred region
```

### First session

```bash
cd your-project
# Default: Claude Sonnet via Vertex
paude create --agent pi --provider vertex --yolo --git my-pi-session

# Gemini instead:
paude create --agent pi --provider vertex \
  --agent-args "--model google-vertex/gemini-2.5-pro" \
  --yolo --git my-pi-session
```

### Available models (Vertex)

**Claude (via pi-anthropic-vertex extension):**
```
anthropic-vertex/claude-sonnet-4-6       (default)
anthropic-vertex/claude-opus-4-6
anthropic-vertex/claude-sonnet-4-5@20250929
anthropic-vertex/claude-haiku-4-5@20251001
```

**Gemini (built-in):**
```
google-vertex/gemini-2.5-pro             (pass --model to select)
google-vertex/gemini-2.5-flash
```

Switch models without recreating the session by passing `--model` inside the agent interface, or specify at create time:

```bash
paude create --agent pi --provider vertex \
  --agent-args "--model anthropic-vertex/claude-opus-4-6" \
  --yolo --git my-pi-session
```

---

## Anthropic Provider (direct API)

```bash
export ANTHROPIC_API_KEY=your-api-key

paude create --agent pi --provider anthropic --yolo --git my-pi-session
```

Default model: `anthropic/claude-sonnet-4-6`.

---

## Google AI Provider

```bash
export GEMINI_API_KEY=your-api-key

paude create --agent pi --provider google --yolo --git my-pi-session
```

Default model: `google-ai/gemini-2.5-pro`.

---

## GitHub Copilot Provider

Pi authenticates with GitHub Copilot via an OAuth token stored in `~/.pi/agent/auth.json`. You need to log in once on the host — paude mounts the token into the container automatically at session creation.

### Prerequisites

Install Pi on the host (one-time):

```bash
npm install -g @earendil-works/pi-coding-agent
```

Log in:

```bash
pi /login
```

This opens a browser OAuth flow and writes `~/.pi/agent/auth.json`. Once that file exists, all future `paude create --agent pi --provider github` sessions pick it up automatically.

### First session

```bash
paude create --agent pi --provider github --yolo --git my-pi-session
```

Pi will use its default GitHub Copilot model. To specify a model:

```bash
paude create --agent pi --provider github \
  --agent-args "--model github-copilot/claude-sonnet-4-5" \
  --yolo --git my-pi-session
```

---

## Connect and use

```bash
paude connect my-pi-session
```

Pi starts in an interactive terminal. Type your task directly. To switch model mid-session, use Pi's `/model` command.

Detach without stopping: **Ctrl+b d** (tmux detach).

Pull commits back:

```bash
git pull paude-my-pi-session main
```

---

## Workspace context loading

Pi automatically discovers and loads context from your project. Understanding the loading order prevents surprises — especially the `SYSTEM.md` gotcha that can turn Pi from a coding assistant into something else entirely.

### File discovery order

| File | Where Pi looks | Effect |
|------|---------------|--------|
| `AGENTS.md` / `CLAUDE.md` | Walks from `cwd` up to `/` | Appended as context alongside the system prompt — Pi can read these but they do not replace the coding assistant behavior |
| `.pi/SYSTEM.md` | `<cwd>/.pi/SYSTEM.md`, then `~/.pi/agent/SYSTEM.md` | **Replaces Pi's built-in coding assistant prompt entirely** — use with caution |
| `.pi/APPEND_SYSTEM.md` | `<cwd>/.pi/APPEND_SYSTEM.md`, then `~/.pi/agent/APPEND_SYSTEM.md` | Appended after the system prompt without replacing it |

### The `SYSTEM.md` gotcha

If your project contains a `.pi/SYSTEM.md`, Pi will use it as the complete system prompt — discarding the built-in coding assistant instructions. This is intentional (it lets you fully customize Pi's persona), but it is easy to accidentally create a file that removes the coding assistant behavior without realizing it.

**Symptom:** Pi seems confused about its role, ignores coding conventions, or doesn't behave like a coding agent.

**Fix:** Remove or rename `.pi/SYSTEM.md`. Use `.pi/APPEND_SYSTEM.md` instead if you only want to add workspace-specific context on top of Pi's default behavior.

### Recommended pattern for workspace context

Use `AGENTS.md` for project rules (coding conventions, where files go, commit style). Pi loads it as context without affecting its core coding assistant behavior:

```
your-project/
  AGENTS.md          # project rules — loaded automatically as context
  .pi/
    APPEND_SYSTEM.md # optional: workspace-specific additions to system prompt
```

Avoid `.pi/SYSTEM.md` unless you intentionally want a custom persona that replaces Pi's default coding assistant.

### CLI overrides

```bash
# Disable AGENTS.md / CLAUDE.md discovery entirely
pi --no-context-files

# Replace system prompt from a file or inline text
pi --system-prompt path/to/prompt.md

# Append to the system prompt (can be repeated)
pi --append-system-prompt "Always use TypeScript strict mode"
pi --append-system-prompt path/to/extra-rules.md
```

---

## Defaults

To avoid typing `--agent pi --provider vertex` on every session:

```bash
mkdir -p ~/.config/paude
cat > ~/.config/paude/defaults.json <<'EOF'
{
  "defaults": {
    "backend": "podman",
    "agent": "pi",
    "provider": "vertex"
  }
}
EOF
```

With this in place, `paude create --yolo --git my-session` uses Pi on Vertex automatically.

---

## Troubleshooting

### `ripgrep not found` / `fd not found` at startup

Pi prints these warnings when it falls back to offline mode for tool discovery. Both binaries are installed in the container — the warnings appear because Pi checks for them before the PATH is fully resolved. They are harmless and don't affect agent functionality.

### `Organization Policy constraint ... disallowed model`

Your GCP project's org policy restricts which Vertex AI models are available. Switch to a permitted model:

```bash
paude create --agent pi --provider vertex \
  --agent-args "--model google-vertex/gemini-2.5-flash" \
  --yolo --git my-session
```

Ask your GCP admin which models are permitted, or check via the Cloud Console under Vertex AI model garden.

### Claude models not appearing in the model list

The `pi-anthropic-vertex` extension is installed at image build time. If you're seeing only Gemini models, the image may have been built before the extension was added. Force a rebuild:

```bash
paude delete my-pi-session --confirm
paude create --agent pi --provider vertex --yolo --git my-pi-session
```

### `Connection error` on Vertex

Verify your ADC token is valid and your project ID is set:

```bash
gcloud auth application-default print-access-token
echo $GOOGLE_CLOUD_PROJECT
echo $ANTHROPIC_VERTEX_PROJECT_ID
```

If the token prints but the connection still fails, check the proxy blocked-domains log:

```bash
paude blocked-domains my-pi-session
```

### GitHub Copilot prompts for `/login` inside container

The host `auth.json` was not found or is stale. On the host:

```bash
ls -la ~/.pi/agent/auth.json    # verify the file exists
pi /login                       # re-authenticate if missing or expired
paude delete my-pi-session --confirm
paude create --agent pi --provider github --yolo --git my-pi-session
```

---

## Cleaning up

```bash
paude stop my-pi-session           # stop, preserve volume
paude delete my-pi-session --confirm  # full removal
```
