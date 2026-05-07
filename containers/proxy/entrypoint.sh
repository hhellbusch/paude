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

# ── OpenAI-compat credential injection ──────────────────────────────
# If a private LLM endpoint key was forwarded to the proxy (set by paude via
# gather_proxy_credentials — NOT by the agent container), generate a
# paude-proxy credentials config so the proxy injects Authorization: Bearer
# for requests to that hostname.  The Pi container never sees the key.
if [[ -n "${OPENAI_COMPAT_API_KEY:-}" ]] && [[ -n "${OPENAI_COMPAT_BASE_URL:-}" ]]; then
    _compat_host=$(python3 -c "from urllib.parse import urlparse; print(urlparse('${OPENAI_COMPAT_BASE_URL}').hostname)" 2>/dev/null || true)
    if [[ -n "${_compat_host}" ]]; then
        _compat_creds_file="/tmp/openai-compat-credentials.json"
        cat > "${_compat_creds_file}" <<EOF
{
  "credentials": [
    {
      "env_var": "OPENAI_COMPAT_API_KEY",
      "injector": "bearer",
      "domains": ["${_compat_host}"]
    }
  ]
}
EOF
        export PAUDE_PROXY_CREDENTIALS_CONFIG="${_compat_creds_file}"
        echo "OpenAI-compat credential injection: ENABLED (${_compat_host})"
    else
        echo "WARN: OPENAI_COMPAT_BASE_URL is set but hostname could not be parsed — skipping injection"
    fi
fi

# ── Paude Proxy ─────────────────────────────────────────────────────
echo "Starting paude-proxy..."
exec /usr/local/bin/paude-proxy
