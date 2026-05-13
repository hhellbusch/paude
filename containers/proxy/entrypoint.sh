#!/usr/bin/env bash
set -euo pipefail

# ── DNS Configuration ────────────────────────────────────────────────
# Start dnsmasq for local DNS forwarding. This is required for tools
# that bypass the system resolver (e.g., Rust reqwest) and need a DNS
# server on localhost.

DNSMASQ_CONF="/tmp/dnsmasq.conf"

# Build dnsmasq config from resolv.conf
{
    echo "# Auto-generated dnsmasq config"
    echo "listen-address=127.0.0.1"
    echo "port=53"
    echo "bind-interfaces"
    echo "no-resolv"
    echo "no-poll"
    echo "no-daemon"
    echo "no-hosts"

    # Forward to upstream DNS servers from resolv.conf
    if [[ -f /etc/resolv.conf ]]; then
        while IFS= read -r line; do
            if [[ "$line" =~ ^nameserver[[:space:]]+(.+) ]]; then
                server="${BASH_REMATCH[1]}"
                # Skip localhost (that would be us)
                if [[ "$server" != "127.0.0.1" && "$server" != "::1" ]]; then
                    echo "server=$server"
                fi
            fi
        done < /etc/resolv.conf
    fi

    # Use custom DNS if provided
    if [[ -n "${PROXY_DNS:-}" ]]; then
        echo "server=$PROXY_DNS"
    fi

    # Fallback public DNS
    echo "server=8.8.8.8"
    echo "server=1.1.1.1"
} > "$DNSMASQ_CONF"

echo "Starting dnsmasq..."
dnsmasq --conf-file="$DNSMASQ_CONF" &
DNSMASQ_PID=$!

# Give dnsmasq a moment to start
sleep 0.2

# ── Credential injection config ─────────────────────────────────────────────
# Build a credentials config consumed by paude-proxy. Handles:
# - GCP ADC (Vertex AI / Gemini): TokenVendor + GCloudInjector pattern.
#   GCP_ADC_JSON content is written to /tmp/gcp-adc.json; GOOGLE_APPLICATION_CREDENTIALS
#   is exported so paude-proxy can mint and refresh real OAuth2 tokens.
#   The agent container holds only a stub ADC with dummy values.
# - OpenAI-compat: bearer token injection for private LLM endpoints.
# - GitHub: bearer (API) + basic auth (git over HTTPS).
_proxy_creds_file="/tmp/paude-proxy-credentials.json"
python3 <<'PY' || true
import json
import os
import sys
from urllib.parse import urlparse

cfg = {"credentials": []}

# GCP ADC — write JSON content to a file so GOOGLE_APPLICATION_CREDENTIALS can
# point to it. The gcloud injector replaces the agent's dummy Bearer token with
# a real OAuth2 token on every request to *.googleapis.com.
gcp_adc_json = (os.environ.get("GCP_ADC_JSON") or "").strip()
if gcp_adc_json:
    adc_path = "/tmp/gcp-adc.json"
    try:
        with open(adc_path, "w", encoding="utf-8") as f:
            f.write(gcp_adc_json)
        cfg["credentials"].append(
            {
                "env_var": "GOOGLE_APPLICATION_CREDENTIALS",
                "injector": "gcloud",
                "domains": [".googleapis.com"],
            }
        )
        print("GCP ADC credential injection: ENABLED (.googleapis.com)", file=sys.stderr)
    except Exception as e:
        print(f"WARN: Failed to write GCP ADC file: {e}", file=sys.stderr)

openai_key = (os.environ.get("OPENAI_COMPAT_API_KEY") or "").strip()
openai_base = (os.environ.get("OPENAI_COMPAT_BASE_URL") or "").strip()
if openai_key and openai_base:
    host = (urlparse(openai_base).hostname or "").strip()
    if host:
        cfg["credentials"].append(
            {
                "env_var": "OPENAI_COMPAT_API_KEY",
                "injector": "bearer",
                "domains": [host],
            }
        )
        print(f"OpenAI-compat credential injection: ENABLED ({host})", file=sys.stderr)
    else:
        print(
            "WARN: OPENAI_COMPAT_BASE_URL is set but hostname could not be parsed — skipping injection",
            file=sys.stderr,
        )

gh_token = (os.environ.get("GH_TOKEN") or "").strip()
if gh_token and gh_token != "proxy-managed":
    # Bearer for GitHub REST API (gh CLI, GitHub API calls).
    cfg["credentials"].append(
        {
            "env_var": "GH_TOKEN",
            "injector": "bearer",
            "domains": ["api.github.com"],
        }
    )
    # Basic auth for github.com git operations (clone/fetch/push).
    # Git smart HTTP requires Basic auth — Bearer is rejected by GitHub's git server.
    # username "x-access-token" is the standard GitHub PAT credential username.
    cfg["credentials"].append(
        {
            "env_var": "GH_TOKEN",
            "injector": "basic",
            "params": {"username": "x-access-token"},
            "domains": ["github.com"],
        }
    )
    print("GitHub credential injection: ENABLED (api.github.com Bearer + github.com Basic git)", file=sys.stderr)

if cfg["credentials"]:
    with open("/tmp/paude-proxy-credentials.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
PY

# Export GOOGLE_APPLICATION_CREDENTIALS if the ADC file was written above.
# This must happen in bash (not Python) so the env var is inherited by paude-proxy.
if [[ -f "/tmp/gcp-adc.json" ]]; then
    export GOOGLE_APPLICATION_CREDENTIALS="/tmp/gcp-adc.json"
fi

if [[ -f "${_proxy_creds_file}" ]]; then
    export PAUDE_PROXY_CREDENTIALS_CONFIG="${_proxy_creds_file}"
fi

# ── Paude Proxy ─────────────────────────────────────────────────────
echo "Starting paude-proxy..."
exec /usr/local/bin/paude-proxy
