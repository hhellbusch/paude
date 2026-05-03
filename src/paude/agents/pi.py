"""Pi coding agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
)
from paude.mounts import resolve_path


class PiAgent:
    """Pi coding agent implementation.

    Pi is a minimal terminal coding harness (npm: @mariozechner/pi-coding-agent).
    Unlike Claude Code or Gemini CLI, pi has no permission system by design —
    the author's explicit guidance is "run in a container", which is exactly what
    paude provides. There is therefore no yolo flag.

    Supported providers (selectable via --provider on paude create):
      anthropic — ANTHROPIC_API_KEY (direct Anthropic API)
      vertex    — Gemini models via Vertex AI, using Pi's built-in google-vertex
                  provider.  Requires GOOGLE_CLOUD_PROJECT + ADC (CLOUDSDK_AUTH_*).
                  Note: Anthropic/Claude models on Vertex are NOT supported by Pi —
                  Pi uses @anthropic-ai/sdk which appends /v1/messages to baseURL,
                  incompatible with Vertex's per-model :rawPredict endpoint.
                  Use Claude Code (paude --agent claude) for Claude on Vertex.
      google    — Gemini via Google AI API (GEMINI_API_KEY)
      github    — GitHub Copilot via ~/.pi/agent/auth.json seeded from host.
                  Run `pi /login` once on the host to populate that file.
    """

    def __init__(self, provider: str | None = None) -> None:
        creds = build_provider_credentials("pi", provider)
        # Disable startup version checks and install telemetry — these are
        # unnecessary in a container and slow cold starts.
        creds.extra_env_vars["PI_OFFLINE"] = "1"
        creds.extra_env_vars["NODE_USE_ENV_PROXY"] = "1"
        # Suppress the "EnvHttpProxyAgent is experimental" undici warning —
        # the proxy works correctly, the warning is just noise in containers.
        creds.extra_env_vars["NODE_NO_WARNINGS"] = "1"

        extra_domains = ["nodejs"]
        if creds.resolved_provider_name == "github":
            extra_domains.extend(["github", "copilot"])

        self._config = AgentConfig(
            name="pi",
            display_name="Pi",
            process_name="pi",
            session_name="pi",
            # Runtime fallback only — requires Node.js already in the image.
            # Normal path: dockerfile_install_lines bakes Node.js + CLI into image.
            install_script="npm install -g @mariozechner/pi-coding-agent",
            install_dir=".local/bin",
            env_vars=creds.extra_env_vars,
            passthrough_env_vars=creds.passthrough_env_vars,
            secret_env_vars=creds.secret_env_vars,
            passthrough_env_prefixes=creds.passthrough_env_prefixes,
            config_dir_name=".pi",
            config_file_name=None,
            activity_files=[],
            yolo_flag=None,  # pi has no permission system — the container is the boundary
            clear_command="/new",
            extra_domain_aliases=extra_domains,
            provider=creds.resolved_provider_name,
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        return [
            "",
            "# Install Node.js 22 for Pi coding agent",
            "USER root",
            "RUN dnf module enable nodejs:22 -y 2>/dev/null || true && \\",
            "    dnf install -y nodejs npm ripgrep fd-find && dnf clean all",
            "",
            "# Install Pi coding agent",
            "RUN npm install -g @mariozechner/pi-coding-agent",
            "",
            "# Ensure Node.js respects http_proxy/https_proxy env vars",
            "ENV NODE_USE_ENV_PROXY=1",
            "",
            "# Disable pi startup version checks and telemetry in containers",
            "ENV PI_OFFLINE=1",
            "",
            "# Set up home directory",
            "USER paude",
            f"WORKDIR {container_home}",
        ]

    def apply_sandbox_config(
        self, home: str, workspace: str, args: str, *, yolo: bool = False
    ) -> str:
        return f"""\
#!/bin/bash
# Pre-configure Pi for containerized operation
agent_dir="{home}/.pi/agent"
settings_json="$agent_dir/settings.json"
mkdir -p "$agent_dir" 2>/dev/null || true

if [ -f "$settings_json" ]; then
    jq '. * {{"enableInstallTelemetry": false}}' \\
        "$settings_json" > "${{settings_json}}.tmp" \\
        && mv "${{settings_json}}.tmp" "$settings_json"
else
    jq -n '{{"enableInstallTelemetry": false}}' > "$settings_json"
fi

# Seed GitHub Copilot OAuth tokens from host (Podman/Docker bind-mount path)
if [ -f /tmp/pi-auth.seed ]; then
    cp /tmp/pi-auth.seed "$agent_dir/auth.json"
    chmod 600 "$agent_dir/auth.json" 2>/dev/null || true
fi
# OpenShift path: credentials synced by sync.py
if [ -f /credentials/pi-auth.json ]; then
    cp /credentials/pi-auth.json "$agent_dir/auth.json"
    chmod 600 "$agent_dir/auth.json" 2>/dev/null || true
fi
"""

    def launch_command(self, args: str) -> str:
        default_flags = self._default_model_flags()
        # Don't override if the user explicitly specified --model or --provider
        if "--provider" in (args or "") or "--model" in (args or ""):
            return f"pi {args}" if args else "pi"
        parts = ["pi", default_flags, args]
        return " ".join(p for p in parts if p).strip()

    def _default_model_flags(self) -> str:
        """Return --model flag with Pi's provider/model shorthand for the configured provider.

        Pi resolves `--model provider/id` without needing a separate --provider flag.
        Defaults below are chosen to match the most capable model each provider
        exposes to pi out of the box.  The user can override at session creation:
          paude create --agent pi --provider vertex \\
            --agent-args "--model google-vertex/gemini-2.0-flash" my-session
        """
        defaults: dict[str, str] = {
            # Vertex AI — Pi's built-in google-vertex provider (Gemini only).
            # Anthropic models on Vertex are not supported by Pi's current SDK.
            "vertex": "--model google-vertex/gemini-2.5-pro",
            # Direct Anthropic API
            "anthropic": "--model anthropic/claude-sonnet-4-6",
            # Google AI direct API (GEMINI_API_KEY)
            "google": "--model google-ai/gemini-2.5-pro",
            # GitHub Copilot — let Pi pick its default Copilot model
            "github": "--provider github-copilot",
        }
        return defaults.get(self._config.provider or "", "")

    def host_config_mounts(self, home: Path) -> list[str]:
        mounts: list[str] = []

        # Mount Pi's OAuth auth.json for GitHub Copilot (if the user has logged in).
        # The user must run `pi /login` once on the host to populate this file.
        auth_json = home / ".pi" / "agent" / "auth.json"
        resolved_auth = resolve_path(auth_json)
        if resolved_auth and resolved_auth.is_file():
            mounts.extend(["-v", f"{resolved_auth}:/tmp/pi-auth.seed:ro"])

        return mounts

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)
