"""GitHub Copilot CLI agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
)


class CopilotAgent:
    """GitHub Copilot CLI agent implementation.

    Wraps the standalone `copilot` binary (npm: @github/copilot).
    Requires Node.js 22 or later in the container image.
    Authentication is handled entirely via environment variables:
    COPILOT_GITHUB_TOKEN, GH_TOKEN, or GITHUB_TOKEN (checked in that order).
    """

    def __init__(self, provider: str | None = None) -> None:
        creds = build_provider_credentials("copilot", provider)
        creds.extra_env_vars["NODE_USE_ENV_PROXY"] = "1"
        self._config = AgentConfig(
            name="copilot",
            display_name="GitHub Copilot CLI",
            process_name="copilot",
            session_name="copilot",
            # Runtime fallback only — requires Node.js 22+ already in the image.
            # Normal path: dockerfile_install_lines bakes Node.js 22 + CLI into image.
            install_script="npm install -g @github/copilot",
            install_dir=".local/bin",
            env_vars=creds.extra_env_vars,
            passthrough_env_vars=creds.passthrough_env_vars,
            secret_env_vars=creds.secret_env_vars,
            passthrough_env_prefixes=creds.passthrough_env_prefixes,
            config_dir_name=".copilot",
            config_file_name=None,
            activity_files=[],
            yolo_flag="--allow-all-tools",
            clear_command="/clear",
            extra_domain_aliases=["copilot", "github", "nodejs"],
            provider=creds.resolved_provider_name,
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        return [
            "",
            "# Install Node.js 22 for GitHub Copilot CLI",
            "USER root",
            "RUN dnf module enable nodejs:22 -y 2>/dev/null || true && \\",
            "    dnf install -y nodejs npm && dnf clean all",
            "",
            "# Install GitHub Copilot CLI",
            "RUN npm install -g @github/copilot",
            "",
            "# Ensure Node.js respects http_proxy/https_proxy env vars",
            "ENV NODE_USE_ENV_PROXY=1",
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
# Pre-trust the workspace folder so Copilot CLI doesn't prompt on every connect
config_dir="{home}/.copilot"
settings_json="$config_dir/settings.json"
mkdir -p "$config_dir" 2>/dev/null || true

if [ -f "$settings_json" ]; then
    jq --arg ws "{workspace}" \\
        '.trustedDirectories |= (. + [$ws] | unique)' \\
        "$settings_json" > "${{settings_json}}.tmp" \\
        && mv "${{settings_json}}.tmp" "$settings_json"
else
    jq -n --arg ws "{workspace}" \\
        '{{"trustedDirectories": [$ws]}}' > "$settings_json"
fi
"""

    def launch_command(self, args: str) -> str:
        if args:
            return f"copilot {args}"
        return "copilot"

    def host_config_mounts(self, home: Path) -> list[str]:
        # Auth is handled via env vars (COPILOT_GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN).
        # No credential files to mount.
        return []

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)
