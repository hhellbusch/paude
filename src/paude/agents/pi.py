"""Pi coding agent implementation."""

from __future__ import annotations

import json
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
      vertex    — Gemini or Anthropic models via Vertex AI (GOOGLE_CLOUD_PROJECT + ADC).
                  If ANTHROPIC_VERTEX_PROJECT_ID is also set, Claude models are seeded
                  into ~/.pi/agent/models.json inside the container.
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
            "    dnf install -y nodejs npm && dnf clean all",
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
        # Build the optional models.json content for Vertex Anthropic support.
        models_json_block = self._models_json_seed_script(home)

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
{models_json_block}"""

    def launch_command(self, args: str) -> str:
        if args:
            return f"pi {args}"
        return "pi"

    def host_config_mounts(self, home: Path) -> list[str]:
        mounts: list[str] = []

        # Mount Pi's OAuth auth.json for GitHub Copilot (if the user has logged in).
        # The user must run `pi /login` once on the host to populate this file.
        auth_json = home / ".pi" / "agent" / "auth.json"
        resolved_auth = resolve_path(auth_json)
        if resolved_auth and resolved_auth.is_file():
            mounts.extend(["-v", f"{resolved_auth}:/tmp/pi-auth.seed:ro"])

        return mounts

    def _models_json_seed_script(self, home: str) -> str:
        """Return a bash fragment that seeds ~/.pi/agent/models.json for Vertex Anthropic.

        Only emits anything when ANTHROPIC_VERTEX_PROJECT_ID is in the environment at
        container startup time (it arrives as a passthrough env var from the host).
        """
        provider = self._config.provider
        if provider != "vertex":
            return ""

        # Build the static models.json for Vertex Anthropic.  The project ID is
        # substituted at runtime via the env var — we use a shell heredoc so the
        # container picks up whatever value was passed through.
        models_config = {
            "providers": {
                "anthropic-vertex": {
                    "baseUrl": "https://us-east5-aiplatform.googleapis.com/v1/projects/${ANTHROPIC_VERTEX_PROJECT_ID}/locations/us-east5/publishers/anthropic/models",
                    "api": "anthropic-messages",
                    "authHeader": True,
                    "apiKey": "!gcloud auth print-access-token",
                    "models": [
                        {
                            "id": "claude-opus-4@20250514",
                            "name": "Vertex Claude Opus 4",
                            "reasoning": True,
                            "input": ["text", "image"],
                            "contextWindow": 200000,
                            "maxTokens": 32000,
                        },
                        {
                            "id": "claude-sonnet-4-5@20250514",
                            "name": "Vertex Claude Sonnet 4.5",
                            "reasoning": True,
                            "input": ["text", "image"],
                            "contextWindow": 200000,
                            "maxTokens": 16000,
                        },
                        {
                            "id": "claude-3-5-sonnet@20241022",
                            "name": "Vertex Claude 3.5 Sonnet",
                            "reasoning": True,
                            "input": ["text", "image"],
                            "contextWindow": 200000,
                            "maxTokens": 8192,
                        },
                        {
                            "id": "claude-3-haiku@20240307",
                            "name": "Vertex Claude 3 Haiku",
                            "reasoning": False,
                            "input": ["text", "image"],
                            "contextWindow": 200000,
                            "maxTokens": 4096,
                        },
                    ],
                }
            }
        }
        models_json_str = json.dumps(models_config, indent=2)

        return f"""\

# Seed models.json for Vertex Anthropic support (only when project ID is set)
models_json="$agent_dir/models.json"
if [ -n "${{ANTHROPIC_VERTEX_PROJECT_ID:-}}" ] && [ ! -f "$models_json" ]; then
    cat > "$models_json" << 'MODELS_EOF'
{models_json_str}
MODELS_EOF
fi
"""

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)
