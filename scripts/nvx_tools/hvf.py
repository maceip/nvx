"""ARM direct-boot adapter for the common sandbox lifecycle."""

from __future__ import annotations

import ipaddress
import time
from typing import Any

from .common import ScriptError


def network_arguments(config: dict[str, Any]) -> tuple[list[str], str]:
    if config.get("network_ingress") == "allow":
        raise ScriptError("ARM portable networking supports ingress deny only")
    for name in ("host_loopback_forward",):
        if config.get(name):
            raise ScriptError(
                f"ARM sandbox does not yet support {name.replace('_', '-')}"
            )
    cidr = config.get("net")
    if cidr is None:
        if (
            config.get("network_egress_allow")
            or config.get("network_egress") == "allow"
        ):
            raise ScriptError("ARM egress policy requires --net IPv4/PREFIX")
        return [], ""
    network = ipaddress.IPv4Network(str(cidr), strict=False)
    if not 1 <= network.prefixlen <= 30:
        raise ScriptError("ARM network requires an IPv4 prefix between 1 and 30")
    guest = network.network_address + 2
    gateway = network.network_address + 1
    fragments = [
        f"consomme:{network}",
        f"egress={config.get('network_egress') or 'deny'}",
        "ingress=deny",
    ]
    if config.get("host_loopback") == "allow":
        fragments.append("gwloopback")
    proxy = config.get("network_proxy")
    if proxy:
        host, separator, port = str(proxy).rpartition(":")
        if (
            not separator
            or host != str(gateway)
            or not port.isdigit()
            or not 1 <= int(port) <= 65535
        ):
            raise ScriptError("ARM proxy must name its gateway and a TCP port")
        if (
            config.get("host_loopback") == "allow"
            or config.get("network_egress") == "allow"
        ):
            raise ScriptError(
                "ARM proxy requires default-deny egress and host loopback"
            )
        fragments.append("gwproxy=" + port)
    for name, option in (
        ("network_egress_allow", "egress-allow"),
        ("network_egress_deny", "egress-deny"),
    ):
        fragments.extend(f"{option}={rule}" for rule in config.get(name, []))
    cmdline = f"virtnet_ip={guest} virtnet_mask={network.netmask} virtnet_gw={gateway}"
    return ["--virtio-net", ",".join(fragments)], cmdline


def boot_tokens(managed: bool) -> str:
    tokens = f"nvx_host_epoch={int(time.time())} nvx_lifecycle={'managed' if managed else 'one-shot'}"
    if managed:
        tokens += " nvx_control_tty=hvc1"
    return tokens
