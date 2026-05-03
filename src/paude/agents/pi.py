"""Pi coding agent implementation."""

from __future__ import annotations

import os
from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
)
from paude.mounts import resolve_path

_VERTEX_EXTENSION_REPO = "https://github.com/hhellbusch/pi-anthropic-vertex.git"
_VERTEX_EXTENSION_SHA = "4938be6fbf3f94d5d6119409b48de556e00f200d"


class PiAgent:
    """Pi coding agent implementation.

    Pi is a minimal terminal coding harness (npm: @mariozechner/pi-coding-agent).
    Unlike Claude Code or Gemini CLI, pi has no permission system by design —
    the author's explicit guidance is "run in a container", which is exactly what
    paude provides. There is therefore no yolo flag.

    Supported providers (selectable via --provider on paude create):
      anthropic — ANTHROPIC_API_KEY (direct Anthropic API)
      vertex    — Gemini and Anthropic/Claude models via Vertex AI.
                  Requires GOOGLE_CLOUD_PROJECT + ADC (CLOUDSDK_AUTH_*).
                  Gemini: Pi's built-in google-vertex provider.
                  Claude: basnijholt/pi-anthropic-vertex extension, installed into
                  the image at build time, uses @anthropic-ai/vertex-sdk + ADC —
                  same auth flow as Claude Code and OpenClaw on Vertex.
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

        # Pi checks GOOGLE_CLOUD_LOCATION (not CLOUD_ML_REGION) to show google-vertex
        # models as available.  Derive it from CLOUD_ML_REGION when not explicitly set.
        if not os.environ.get("GOOGLE_CLOUD_LOCATION") and os.environ.get("CLOUD_ML_REGION"):
            creds.extra_env_vars["GOOGLE_CLOUD_LOCATION"] = os.environ["CLOUD_ML_REGION"]

        # Map OPENAI_BASE_URL → OPENAI_COMPAT_BASE_URL for pi-openai-compat extension.
        # Avoids triggering Pi's built-in openai provider (which floods the picker
        # with GPT models that don't exist on the private endpoint).
        if os.environ.get("OPENAI_BASE_URL"):
            creds.extra_env_vars["OPENAI_COMPAT_BASE_URL"] = os.environ["OPENAI_BASE_URL"]
        if os.environ.get("OPENAI_API_KEY"):
            creds.extra_env_vars["OPENAI_COMPAT_API_KEY"] = os.environ["OPENAI_API_KEY"]

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
        lines = [
            "",
            "# Install Node.js 22 and tools for Pi coding agent",
            "USER root",
            "RUN dnf module enable nodejs:22 -y 2>/dev/null || true && \\",
            "    dnf install -y nodejs npm git ripgrep fd-find && dnf clean all",
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
        if self._config.provider == "vertex":
            ext_dir = f"{container_home}/.pi/agent/extensions/pi-anthropic-vertex"
            lines += [
                "",
                "# Install pi-anthropic-vertex extension for Claude models via Vertex AI.",
                "# Uses @anthropic-ai/vertex-sdk + ADC — same auth flow as Claude Code on Vertex.",
                f"# Pinned to {_VERTEX_EXTENSION_SHA[:7]}.",
                f"# Source: {_VERTEX_EXTENSION_REPO}",
                f"RUN mkdir -p {container_home}/.pi/agent/extensions && \\",
                f"    git clone {_VERTEX_EXTENSION_REPO} \\",
                f"        {ext_dir} && \\",
                f"    cd {ext_dir} && \\",
                f"    git checkout {_VERTEX_EXTENSION_SHA} && \\",
                "    npm install --quiet --no-fund --no-audit",
            ]
        return lines

    def apply_sandbox_config(
        self,
        home: str,
        workspace: str,
        args: str,
        *,
        yolo: bool = False,
        pi_extensions: list[str] | None = None,
    ) -> str:
        import base64
        import json as _json

        exts = [x for x in (pi_extensions or []) if isinstance(x, str) and x.strip()]
        ext_block = ""
        if exts:
            b64 = base64.b64encode(_json.dumps(exts).encode()).decode()
            ext_block = f"""
# Pi extensions from paude create (runs before first agent start; needs network)
_PAUDE_PI_EXT_B64='{b64}'
if command -v jq >/dev/null 2>&1 && command -v pi >/dev/null 2>&1; then
  echo "$_PAUDE_PI_EXT_B64" | base64 -d | jq -r '.[]' | while IFS= read -r _pi_spec || [ -n "$_pi_spec" ]; do
    [ -z "$_pi_spec" ] && continue
    echo "paude: installing Pi extension: $_pi_spec" >&2
    pi install "$_pi_spec" || echo "paude: warning: pi install failed for: $_pi_spec" >&2
  done
fi
"""

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
{ext_block}"""

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
            --agent-args "--model anthropic-vertex/claude-sonnet-4-5@20250929" my-session
        """
        defaults: dict[str, str] = {
            # Vertex AI — use --models to restrict Ctrl+P cycling to
            # wired-up providers only (hides github-copilot etc.).
            "vertex": "--model anthropic-vertex/claude-sonnet-4-6 --models anthropic-vertex/*,google-vertex/*,openai-compat/*",
            # Direct Anthropic API
            "anthropic": "--model anthropic/claude-sonnet-4-6 --models anthropic/*",
            # Google AI direct API (GEMINI_API_KEY)
            "google": "--model google-ai/gemini-2.5-pro --models google-ai/*",
            # GitHub Copilot — not yet wired up; placeholder for future use
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
