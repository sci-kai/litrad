"""Where the web UI listens: this computer only, all networks, or one network.

``web.host`` (or ``litrad serve --network``) accepts

- ``local``: only this computer (127.0.0.1);
- ``all``: every network interface (0.0.0.0);
- an interface name such as ``eth0`` or ``wlan0``: only that network. The
  address is looked up at every start, so a changing DHCP address is fine;
- an IPv4 address of this computer.

Interfaces are found with the standard library only (Linux ioctl), so no
packets are sent and nothing extra needs to be installed.
"""

import ipaddress
import socket
import struct
import sys
from dataclasses import dataclass

LOCAL = ("local", "localhost", "127.0.0.1")
ALL = ("all", "0.0.0.0")
SIOCGIFADDR = 0x8915  # Linux: get the IPv4 address of an interface


class NetworkError(ValueError):
    pass


@dataclass(frozen=True)
class Interface:
    name: str
    address: str

    @property
    def kind(self) -> str:
        if self.name == "lo" or self.address.startswith("127."):
            return "this computer only"
        if self.name.startswith(("wlan", "wl")):
            return "WLAN"
        if self.name.startswith(("eth", "en")):
            return "cable (Ethernet)"
        if self.name.startswith(("tailscale", "wg", "tun", "zt")):
            return "VPN"
        return "network"


def _address_of(name: str) -> str | None:
    import fcntl

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            packed = fcntl.ioctl(
                probe.fileno(), SIOCGIFADDR, struct.pack("256s", name.encode()[:15])
            )
        except OSError:  # interface without IPv4 address (or down)
            return None
    return socket.inet_ntoa(packed[20:24])


def interfaces() -> list[Interface]:
    """All interfaces that have an IPv4 address, loopback first."""
    if not sys.platform.startswith("linux"):
        return [Interface("lo", "127.0.0.1")]
    found = []
    for _, name in socket.if_nameindex():
        address = _address_of(name)
        if address:
            found.append(Interface(name, address))
    return sorted(found, key=lambda i: (i.name != "lo", i.name))


@dataclass(frozen=True)
class Binding:
    host: str  # what the server binds to
    description: str
    urls: tuple[str, ...]  # addresses to open in a browser
    local_only: bool


def resolve(spec: str | None, port: int, available: list[Interface] | None = None) -> Binding:
    """Turn ``web.host`` / ``--network`` into a bind address and URLs."""
    spec = (spec or "local").strip()
    available = interfaces() if available is None else available
    networks = [i for i in available if i.kind != "this computer only"]

    def url(address: str) -> str:
        return f"http://{address}:{port}/"

    if spec.lower() in LOCAL:
        return Binding("127.0.0.1", "this computer only", (url("127.0.0.1"),), True)
    if spec.lower() in ALL:
        return Binding(
            "0.0.0.0",
            "all networks" + (f" ({', '.join(i.name for i in networks)})" if networks else ""),
            tuple(url(i.address) for i in networks) or (url("127.0.0.1"),),
            False,
        )
    by_name = {i.name: i for i in available}
    if spec in by_name:
        chosen = by_name[spec]
        local = chosen.kind == "this computer only"
        return Binding(
            chosen.address, f"{chosen.name}, {chosen.kind}", (url(chosen.address),), local
        )
    try:
        address = ipaddress.IPv4Address(spec)
    except ValueError:
        raise NetworkError(
            f"Unknown network '{spec}'. Use local, all, an interface name or an IP address. "
            + describe_available(available)
        ) from None
    match = next((i for i in available if i.address == str(address)), None)
    if match is None:
        raise NetworkError(
            f"{address} is not an address of this computer. " + describe_available(available)
        )
    return Binding(
        match.address,
        f"{match.name}, {match.kind}",
        (url(match.address),),
        match.kind == "this computer only",
    )


def local_names() -> frozenset[str]:
    """Names under which this computer is reached without a DNS server of its own."""
    name = socket.gethostname().lower()
    return frozenset({"localhost", name, f"{name}.local"})


def is_trusted_host(host: str, names: frozenset[str]) -> bool:
    """True if a request's ``Host`` header is an IP address or one of ``names``.

    Guards a server without password against DNS rebinding: a malicious website
    can point its own domain at 127.0.0.1, but the browser then still sends
    that domain as ``Host``.
    """
    host = host.strip().lower()
    if host.startswith("["):  # [IPv6]:port
        name = host[1 : host.find("]")]
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return name.rstrip(".") in names


def describe_available(available: list[Interface] | None = None) -> str:
    available = interfaces() if available is None else available
    listing = ", ".join(f"{i.name} ({i.address})" for i in available) or "none found"
    return f"Available: local, all, {listing}."
