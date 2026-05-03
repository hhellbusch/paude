"""Agent-provider intersection: valid combinations and per-agent overrides."""

from __future__ import annotations

from dataclasses import dataclass, field

from paude.providers.base import ProviderConfig, get_provider


@dataclass
class AgentProviderConfig:
    """Provider-specific overrides for an agent.

    Attributes:
        extra_passthrough_env_vars: Additional passthrough vars beyond provider base.
        extra_secret_env_vars: Additional secret vars beyond provider base.
        extra_env_vars: Agent-specific env vars to set for this provider.
        model_config: Provider-specific model configuration (e.g., primary model).
    """

    extra_passthrough_env_vars: list[str] = field(default_factory=list)
    extra_secret_env_vars: list[str] = field(default_factory=list)
    extra_env_vars: dict[str, str] = field(default_factory=dict)
    model_config: dict[str, str] = field(default_factory=dict)


# Which providers each agent supports, with agent-specific overrides.
AGENT_PROVIDERS: dict[str, dict[str, AgentProviderConfig]] = {
    "claude": {
        "vertex": AgentProviderConfig(
            extra_env_vars={"CLAUDE_CODE_USE_VERTEX": "1"},
        ),
        "anthropic": AgentProviderConfig(),
    },
    "openclaw": {
        "vertex": AgentProviderConfig(
            model_config={"primary": "anthropic-vertex/claude-sonnet-4-6"},
        ),
        "openai": AgentProviderConfig(
            model_config={"primary": "openai/gpt-5.4-mini"},
        ),
        "anthropic": AgentProviderConfig(
            model_config={"primary": "anthropic/claude-opus-4-6"},
        ),
    },
    "cursor": {
        "cursor": AgentProviderConfig(),
    },
    "gemini": {
        "google": AgentProviderConfig(),
    },
    "copilot": {
        "github": AgentProviderConfig(),
    },
    "pi": {
        # Direct Anthropic API — requires ANTHROPIC_API_KEY.
        "anthropic": AgentProviderConfig(),
        # Gemini via Vertex AI — uses GOOGLE_CLOUD_PROJECT + ADC (CLOUDSDK_AUTH_*).
        # Also supports Anthropic/Claude via Vertex when ANTHROPIC_VERTEX_PROJECT_ID
        # is set and ~/.pi/agent/models.json is seeded (handled by PiAgent).
        # CLOUD_ML_REGION mirrors the Claude Code setup (typically "global" for 4.x models).
        "vertex": AgentProviderConfig(
            extra_passthrough_env_vars=["ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION"],
        ),
        # Gemini via Google AI API — requires GEMINI_API_KEY.
        "google": AgentProviderConfig(
            extra_secret_env_vars=["GEMINI_API_KEY"],
        ),
        # GitHub Copilot — auth via ~/.pi/agent/auth.json seeded from host.
        # Run `pi /login` once on the host to populate the auth file, then
        # PiAgent will mount it automatically.
        "github": AgentProviderConfig(),
    },
}

# Default provider for each agent (used when --provider is not specified).
DEFAULT_PROVIDER: dict[str, str] = {
    "claude": "vertex",
    "openclaw": "vertex",
    "cursor": "cursor",
    "gemini": "google",
    "copilot": "github",
    "pi": "anthropic",
}


def resolve_agent_provider(
    agent_name: str, provider_name: str | None = None
) -> tuple[ProviderConfig, AgentProviderConfig]:
    """Resolve the provider configuration for an agent.

    Args:
        agent_name: Agent identifier (e.g., "claude", "openclaw").
        provider_name: Provider name, or None for the agent's default.

    Returns:
        Tuple of (provider base config, agent-specific overrides).

    Raises:
        ValueError: If the agent-provider combination is invalid.
    """
    # Resolve default provider
    if provider_name is None:
        provider_name = DEFAULT_PROVIDER.get(agent_name)
        if provider_name is None:
            raise ValueError(f"No default provider for agent '{agent_name}'")

    # Validate provider exists
    provider_config = get_provider(provider_name)

    # Validate agent supports this provider
    agent_providers = AGENT_PROVIDERS.get(agent_name)
    if agent_providers is None:
        raise ValueError(f"No provider configuration for agent '{agent_name}'")

    agent_provider_config = agent_providers.get(provider_name)
    if agent_provider_config is None:
        valid = ", ".join(sorted(agent_providers.keys()))
        raise ValueError(
            f"Agent '{agent_name}' does not support provider '{provider_name}'. "
            f"Valid providers: {valid}"
        )

    return provider_config, agent_provider_config


def supported_providers(agent_name: str) -> list[str]:
    """List valid provider names for an agent."""
    agent_providers = AGENT_PROVIDERS.get(agent_name, {})
    return sorted(agent_providers.keys())
