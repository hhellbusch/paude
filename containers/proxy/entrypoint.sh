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

# ── Credential injection config (OpenAI-compat + Vertex relay pattern) ─────
# Build one credentials config consumed by paude-proxy.
# - OpenAI-compat uses OPENAI_COMPAT_API_KEY on the host-defined endpoint host.
# - Vertex relay pattern (experimental) uses a host-minted short-lived bearer
#   token (PAUDE_VERTEX_BEARER_TOKEN) scoped to <region>-aiplatform.googleapis.com.
_proxy_creds_file="/tmp/paude-proxy-credentials.json"
python3 <<'PY' || true
import json
import os
import sys
from urllib.parse import urlparse

cfg = {"credentials": []}

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
    cfg["credentials"].append(
        {
            "env_var": "GH_TOKEN",
            "injector": "bearer",
            "domains": ["github.com", "api.github.com"],
        }
    )
    print("GitHub credential injection: ENABLED (github.com)", file=sys.stderr)

vertex_mode = (os.environ.get("PAUDE_VERTEX_AUTH_MODE") or "").strip().lower()
if vertex_mode == "proxy":
    token = (os.environ.get("PAUDE_VERTEX_BEARER_TOKEN") or "").strip()
    region = (
        (os.environ.get("PAUDE_VERTEX_REGION") or "").strip()
        or (os.environ.get("GOOGLE_CLOUD_LOCATION") or "").strip()
        or (os.environ.get("CLOUD_ML_REGION") or "").strip()
        or "us-east5"
    )
    host = f"{region}-aiplatform.googleapis.com"
    if token:
        cfg["credentials"].append(
            {
                "env_var": "PAUDE_VERTEX_BEARER_TOKEN",
                "injector": "bearer",
                "domains": [host],
            }
        )
        print(
            f"Vertex proxy auth pattern: ENABLED ({host}, token relay)",
            file=sys.stderr,
        )
    else:
        print(
            "WARN: PAUDE_VERTEX_AUTH_MODE=proxy set but no PAUDE_VERTEX_BEARER_TOKEN is available",
            file=sys.stderr,
        )

if cfg["credentials"]:
    with open("/tmp/paude-proxy-credentials.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
PY

if [[ -f "${_proxy_creds_file}" ]]; then
    export PAUDE_PROXY_CREDENTIALS_CONFIG="${_proxy_creds_file}"
fi

# ── Paude Proxy ─────────────────────────────────────────────────────
echo "Starting paude-proxy..."
exec /usr/local/bin/paude-proxy
