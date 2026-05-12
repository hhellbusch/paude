"""Proxy container lifecycle methods extracted from ContainerRunner."""

from __future__ import annotations

import time

from paude.container.engine import ContainerEngine
from paude.container.runner import ContainerRunner


class ProxyStartError(Exception):
    """Error starting the proxy container."""

    pass


class ProxyRunner:
    """Proxy container lifecycle operations.

    Wraps a ContainerRunner to provide proxy-specific create/start/stop
    operations. Handles engine differences (e.g. Docker multi-network).
    """

    def __init__(self, runner: ContainerRunner) -> None:
        self._runner = runner

    @property
    def _engine(self) -> ContainerEngine:
        return self._runner.engine

    def _build_multi_network(self, internal: str, ip: str | None = None) -> list[str]:
        """Build network arguments for proxy containers.

        Podman supports ``--network net1,net2`` in create/run.
        Docker requires creating with one network, then connecting
        the second.

        When *ip* is given, separate ``--network`` flags are used because
        per-network options (``net:ip=…``) and comma-separated network
        lists are incompatible in Podman.
        """
        bridge = self._engine.default_bridge_network
        if self._engine.supports_multi_network_create:
            if ip:
                return ["--network", f"{internal}:ip={ip}", "--network", bridge]
            return ["--network", f"{internal},{bridge}"]
        return ["--network", internal]

    def _connect_bridge_if_needed(self, container_name: str) -> None:
        """Connect the container to the default bridge (Docker only)."""
        if self._engine.supports_multi_network_create:
            return
        bridge = self._engine.default_bridge_network
        self._engine.run("network", "connect", bridge, container_name, check=False)

    def _build_env_args(
        self,
        dns: str | None,
        allowed_domains: list[str] | None,
        otel_ports: list[int] | None = None,
        credentials: dict[str, str] | None = None,
        allowed_clients: str | None = None,
    ) -> list[str]:
        """Build environment variable arguments for proxy containers."""
        args: list[str] = []
        if dns:
            args.extend(["-e", f"PROXY_DNS={dns}"])
        if allowed_domains:
            args.extend(["-e", f"ALLOWED_DOMAINS={','.join(allowed_domains)}"])
        if otel_ports:
            args.extend(
                ["-e", f"ALLOWED_OTEL_PORTS={','.join(str(p) for p in otel_ports)}"]
            )
        if credentials:
            for key, value in credentials.items():
                args.extend(["-e", f"{key}={value}"])
        if allowed_clients:
            args.extend(["-e", f"PAUDE_PROXY_ALLOWED_CLIENTS={allowed_clients}"])
        return args

    @staticmethod
    def _build_secret_args(secret_refs: list[str] | None = None) -> list[str]:
        """Build ``--secret`` arguments for podman create."""
        args: list[str] = []
        if secret_refs:
            for ref in secret_refs:
                args.extend(["--secret", ref])
        return args

    def _build_volume_args(
        self,
        ca_volume: str | None = None,
        upstream_ca_path: str | None = None,
    ) -> list[str]:
        """Build volume mount arguments for proxy containers."""
        args: list[str] = []
        if ca_volume:
            args.extend(["-v", f"{ca_volume}:/data/ca"])
        if upstream_ca_path:
            args.extend(
                [
                    "-v",
                    f"{upstream_ca_path}:/etc/pki/ca-trust/source/anchors/paude-upstream-ca.crt:z",
                ]
            )
        return args

    def _build_entrypoint_args(self, upstream_ca_path: str | None = None) -> list[str]:
        """Build entrypoint override args for proxy containers.

        When an upstream CA cert is mounted, the proxy container's entrypoint
        must run ``update-ca-trust`` before starting so that the Go TLS client
        trusts the private CA when connecting to the upstream LLM server.
        """
        if not upstream_ca_path:
            return []
        return [
            "--entrypoint",
            '["bash", "-c", "update-ca-trust && exec /usr/local/bin/paude-entrypoint.sh"]',
        ]

    @staticmethod
    def _build_add_host_args(add_hosts: list[str] | None = None) -> list[str]:
        """Build --add-host arguments for proxy containers.

        Each entry should be in the form ``hostname:ip`` or
        ``hostname:host-gateway``.
        """
        args: list[str] = []
        if add_hosts:
            for entry in add_hosts:
                args.extend(["--add-host", entry])
        return args

    def create_session_proxy(
        self,
        name: str,
        image: str,
        network: str,
        dns: str | None = None,
        allowed_domains: list[str] | None = None,
        ip: str | None = None,
        otel_ports: list[int] | None = None,
        ca_volume: str | None = None,
        credentials: dict[str, str] | None = None,
        allowed_clients: str | None = None,
        secret_refs: list[str] | None = None,
        upstream_ca_path: str | None = None,
        add_hosts: list[str] | None = None,
    ) -> str:
        """Create a proxy container for a session (does not start it).

        When *secret_refs* is provided, credentials are injected via
        ``--secret`` flags (podman only) instead of ``-e`` environment
        variables, so they do not appear in ``podman inspect`` output.

        Returns:
            Container name.
        """
        net_args = self._build_multi_network(network, ip=ip)
        env_credentials = None if secret_refs else credentials
        env_args = self._build_env_args(
            dns, allowed_domains, otel_ports, env_credentials, allowed_clients
        )
        secret_args = self._build_secret_args(secret_refs)
        vol_args = self._build_volume_args(ca_volume, upstream_ca_path)
        entrypoint_args = self._build_entrypoint_args(upstream_ca_path)
        add_host_args = self._build_add_host_args(add_hosts)

        ip_args: list[str] = []
        if ip and not self._engine.supports_multi_network_create:
            # Docker doesn't support multi-network create, so --ip is separate
            ip_args = ["--ip", ip]

        result = self._engine.run(
            "create",
            "--pull=never",
            "--name",
            name,
            *net_args,
            *ip_args,
            *env_args,
            *secret_args,
            *vol_args,
            *entrypoint_args,
            *add_host_args,
            image,
            check=False,
        )
        if result.returncode != 0:
            raise ProxyStartError(f"Failed to create proxy: {result.stderr}")

        self._connect_bridge_if_needed(name)
        return name

    def start_session_proxy(self, name: str) -> None:
        """Start a session proxy container and wait for initialization.

        Raises:
            ProxyStartError: If the proxy fails to start.
        """
        result = self._engine.run("start", name, check=False)
        if result.returncode != 0:
            raise ProxyStartError(f"Failed to start proxy: {result.stderr}")
        time.sleep(1)

    def recreate_session_proxy(
        self,
        name: str,
        image: str,
        network: str,
        dns: str | None = None,
        allowed_domains: list[str] | None = None,
        ip: str | None = None,
        otel_ports: list[int] | None = None,
        ca_volume: str | None = None,
        credentials: dict[str, str] | None = None,
        allowed_clients: str | None = None,
        secret_refs: list[str] | None = None,
        upstream_ca_path: str | None = None,
        add_hosts: list[str] | None = None,
    ) -> str:
        """Recreate a session proxy with new configuration.

        Returns:
            Container name.
        """
        self._runner.stop_container(name)
        self._runner.remove_container(name, force=True)

        self.create_session_proxy(
            name=name,
            image=image,
            network=network,
            dns=dns,
            allowed_domains=allowed_domains,
            ip=ip,
            otel_ports=otel_ports,
            ca_volume=ca_volume,
            credentials=credentials,
            allowed_clients=allowed_clients,
            secret_refs=secret_refs,
            upstream_ca_path=upstream_ca_path,
            add_hosts=add_hosts,
        )
        self.start_session_proxy(name)

        return name
