"""
Where a mailbox's SMTP and IMAP servers may be. A mailbox is added by an owner, but this service makes the connection from inside the
network, so on a real server a "mail server" must not be the machine itself, a neighbour on the private network, or a cloud metadata address:
that would turn the Email page into a way to probe what the internet can't reach. Development (Mailpit on localhost) is left alone.
"""
import asyncio
import ipaddress
import socket

REASON = "That address isn't a public mail server. On a real server, mail servers must be reachable on the internet (not localhost, a private network or a link-local address)."


def _check(host: str) -> None:
    host = (host or "").strip().strip("[]")
    if not host:
        return
    try:
        found = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            found = {ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}
        except socket.gaierror:
            raise ValueError("That server's name doesn't resolve.") from None
    for ip in found:
        ip = ip.ipv4_mapped or ip if isinstance(ip, ipaddress.IPv6Address) else ip
        if not ip.is_global:
            raise ValueError(REASON)


async def refuse_internal(*hosts: str) -> None:
    """Raises ValueError, in words an owner can act on, when any host is (or resolves to) a non-public address."""
    loop = asyncio.get_running_loop()
    for host in hosts:
        await loop.run_in_executor(None, _check, host)
