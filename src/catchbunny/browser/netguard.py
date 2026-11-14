"""Addresses no scan opens: a cloud machine's metadata service, and the link-local range it answers in.

On a cloud machine, http://169.254.169.254/ hands out the machine's own credentials to whoever asks
from it. A tester's browser that opened it would put them in a screenshot, a recording or a trace,
where anyone who can read the run would see them. No app under test lives there, so:

  * a run whose app is at such an address is blocked before a browser opens, on every server;
  * on a team server, where people other than the machine's owner start scans, every request a
    tester's browser makes is checked too (`Settings.guard_network`), so a link, a frame or an image
    in the app cannot lead there either.

Private networks (10/8, 192.168/16, ...) and the machine itself stay open: a staging server on the
office network and a dev server on localhost are what Catchbunny is for.

A host name is judged by what it points at, looked up here. Two things get past a check in the
browser: a redirect (Playwright hands a route handler only the first address of a chain, so an app
that answers with a redirect to 169.254.169.254 is followed), and a name whose answer changes between
this lookup and the browser's own. Closing those for certain is the cloud's job: on AWS, require
IMDSv2, whose token a browser cannot fetch; Google Cloud and Azure already demand a header no page
can send. Or refuse 169.254.169.254 in the machine's firewall for the user Catchbunny runs as.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit

from playwright.async_api import Route

NAMES = {"metadata", "metadata.google.internal", "metadata.goog", "instance-data", "instance-data.ec2.internal"}
NETWORKS = [
    ipaddress.ip_network(n)
    for n in (
        "169.254.0.0/16",  # link-local: the metadata service of AWS, Google Cloud, Azure, Oracle and DigitalOcean
        "fe80::/10",  # link-local, IPv6
        "fd00:ec2::254/128",  # AWS's metadata service over IPv6
        "100.100.100.200/32",  # Alibaba Cloud's metadata service
    )
]
_TTL = 60.0
_looked_up: dict[str, tuple[float, list[str]]] = {}


def _forbidden_ip(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text.split("%")[0])  # an IPv6 address may carry a zone: fe80::1%en0
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in NETWORKS)


async def _addresses(host: str) -> list[str]:
    seen = _looked_up.get(host)
    if seen and time.monotonic() - seen[0] < _TTL:
        return seen[1]
    try:
        found = sorted({info[4][0] for info in await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)})
    except (OSError, UnicodeError):
        found = []  # a name that does not resolve here does not open in the browser either
    if len(_looked_up) > 1000:
        _looked_up.clear()
    _looked_up[host] = (time.monotonic(), found)
    return found


async def refusal(url: str) -> str | None:
    """Why `url` must not be opened, in words for the person who asked; None when it may be."""
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    if not host:
        return None
    if host in NAMES:
        return f"{host} is a cloud metadata service"
    if _forbidden_ip(host):
        return f"{host} is where a cloud machine's metadata service answers"
    hit = next((a for a in await _addresses(host) if _forbidden_ip(a)), None)  # also catches 2852039166 and 0xa9fea9fe
    return f"{host} points at {hit}, where a cloud machine's metadata service answers" if hit else None


async def guard(route: Route) -> None:
    """A context's route handler: refuse what `refusal` refuses, pass everything else on unchanged."""
    if await refusal(route.request.url):
        await route.abort("blockedbyclient")
    else:
        await route.fallback()
