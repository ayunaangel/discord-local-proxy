"""Para onde a sua chamada está indo.

O Discord escolhe o servidor de voz a partir de onde ele acha que você está —
e é esse servidor que carrega a voz, a câmera e o compartilhamento de tela.
Sair por um proxy em outro país faz o Discord entregar um servidor de lá.

Este módulo responde às duas perguntas que sobram:

* `voice_endpoints` — para qual servidor a chamada de agora está indo.
* `exit_address` — que IP o proxy configurado apresenta para o mundo.

Nada aqui roda sozinho. A consulta de país sai para um serviço externo e só
acontece quando você pede.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import os
import socket
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path

from . import bridge as bridge_module
from .config import Proxy
from .discord import Install
from .voice import data_root

STATE_NAME = "voice-endpoint.txt"
JOURNAL_NAME = "bridge-targets.txt"
# Nome dos servidores de mídia do Discord — voz, câmera e tela passam por eles.
# Um servidor de verdade é `<região><número>.discord.media`; nomes sem número
# no fim são outra coisa (o `latency.discord.media` só mede latência).
MEDIA_SUFFIX = ".discord.media"
# O Chromium também fala UDP em 443 (QUIC) e 80; isso não é voz.
WEB_PORTS = frozenset({80, 443, 8080})
# Faixa que o Discord usa para os servidores de voz.
VOICE_PORT_RANGE = range(50000, 65536)
# Nem todo serviço aceita conexão vinda do Tor; tentamos na ordem. O último
# não diz o país, mas confirma se a saída é mesmo do Tor.
LOOKUP_SERVICES = (
    ("ipinfo.io", "/json", "/{address}/json"),
    ("ifconfig.co", "/json", "/json?ip={address}"),
    ("check.torproject.org", "/api/ip", ""),
)
LOOKUP_HOST = LOOKUP_SERVICES[0][0]
RESOLVE_TIMEOUT = 3.0
LOOKUP_TIMEOUT = 8.0


class LookupFailed(OSError):
    """Nenhum dos serviços de consulta respondeu."""


@dataclass(frozen=True)
class Endpoint:
    """Um servidor de voz do Discord que o cliente está usando."""

    address: str
    port: int
    hostname: str = ""

    @property
    def region(self) -> str:
        """`brazil11111.discord.media` -> `brazil`."""
        if not self.hostname:
            return ""
        label = self.hostname.split(".", 1)[0]
        name = label.rstrip("0123456789")
        return name or label

    def __str__(self) -> str:
        where = self.region or "região desconhecida"
        who = self.hostname or self.address
        if self.address and self.hostname:
            who = f"{self.hostname} ({self.address})"
        return f"{who}:{self.port} — {where}"


@dataclass(frozen=True)
class Place:
    """O que um serviço de geolocalização respondeu sobre um IP."""

    address: str
    country: str = ""
    region: str = ""
    city: str = ""
    org: str = ""

    def __str__(self) -> str:
        parts = [part for part in (self.city, self.region, self.country) if part]
        where = ", ".join(parts) if parts else "local desconhecido"
        return f"{self.address} — {where}" + (f" · {self.org}" if self.org else "")


def state_path() -> Path:
    """Arquivo onde o componente nativo anota o destino da voz (Windows)."""
    return data_root() / STATE_NAME


def journal_path() -> Path:
    """Arquivo onde a ponte anota os destinos dos túneis que abriu."""
    return data_root() / JOURNAL_NAME


def voice_endpoints(install: Install | None = None, *, resolve: bool = True) -> list[Endpoint]:
    """Os servidores de mídia em uso agora, do mais recente para o mais antigo.

    Com proxy configurado o WebRTC do Discord passa pela ponte, e aí o destino
    vem com nome e tudo — é a fonte melhor. Sem proxy a mídia sai por UDP, e o
    destino vem do componente nativo (Windows) ou do /proc (Linux).
    """
    found = _from_journal()
    if found:
        return found
    found = _from_state_file()
    if not found and sys.platform.startswith("linux"):
        found = _from_proc(install)
    # Quem está na faixa de voz vem primeiro; a ordem relativa é preservada.
    found.sort(key=lambda item: item.port not in VOICE_PORT_RANGE)
    if resolve:
        found = [_with_hostname(item) for item in found]
    return found


def _is_media_server(host: str) -> bool:
    """`rotterdam1234.discord.media` sim; `latency.discord.media` não."""
    host = host.lower()
    if not host.endswith(MEDIA_SUFFIX):
        return False
    label = host[: -len(MEDIA_SUFFIX)].rsplit(".", 1)[-1]
    return bool(label) and label[-1].isdigit()


def _from_journal() -> list[Endpoint]:
    """Os servidores de mídia que a ponte viu, mais recentes primeiro."""
    try:
        lines = journal_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    found: list[Endpoint] = []
    for line in reversed(lines[-4096:]):
        host, _, port = line.strip().rpartition(":")
        if not host or not port.isdigit() or not _is_media_server(host):
            continue
        endpoint = Endpoint(address="", port=int(port), hostname=host)
        if endpoint not in found:
            found.append(endpoint)
    return found


def exit_address(proxy: Proxy) -> Place:
    """O IP que o proxy apresenta ao mundo — consulta um serviço externo."""
    upstream = proxy if proxy.enabled else None
    problems: list[str] = []
    for host, path, _ in LOOKUP_SERVICES:
        try:
            return _place_from(_https_get_json(host, path, proxy=upstream))
        except (OSError, ValueError) as exc:
            problems.append(f"{host}: {exc}")
    raise LookupFailed("; ".join(problems))


def locate(address: str) -> Place:
    """Onde fica um IP — consulta um serviço externo."""
    ipaddress.ip_address(address)
    problems: list[str] = []
    for host, _, template in LOOKUP_SERVICES:
        if not template:
            continue
        try:
            return _place_from(_https_get_json(host, template.format(address=address), proxy=None))
        except (OSError, ValueError) as exc:
            problems.append(f"{host}: {exc}")
    raise LookupFailed("; ".join(problems))


# ------------------------------------------------------------------ destino --


def _from_state_file() -> list[Endpoint]:
    """No Windows quem sabe o destino é o componente nativo, que anota aqui."""
    path = state_path()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    found: list[Endpoint] = []
    for line in reversed(lines[-32:]):
        address, _, port = line.strip().rpartition(":")
        if not address or not port.isdigit():
            continue
        try:
            ipaddress.ip_address(address)
        except ValueError:
            continue
        endpoint = Endpoint(address=address, port=int(port))
        if endpoint not in found:
            found.append(endpoint)
    return found


def _from_proc(install: Install | None) -> list[Endpoint]:
    """No Linux o próprio kernel conta: /proc/net/udp cruzado com os fds."""
    sockets = _discord_socket_inodes(install)
    if not sockets:
        return []
    found: list[Endpoint] = []
    for table, size in (("/proc/net/udp", 4), ("/proc/net/udp6", 16)):
        try:
            lines = Path(table).read_text(encoding="ascii", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 10 or fields[9] not in sockets:
                continue
            endpoint = _parse_proc_address(fields[2], size)
            if endpoint is not None:
                found.append(endpoint)
    return found


def _discord_socket_inodes(install: Install | None) -> set[str]:
    wanted = _discord_pids(install)
    inodes: set[str] = set()
    for pid in wanted:
        directory = Path("/proc") / str(pid) / "fd"
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                target = os.readlink(entry)
            except OSError:
                continue
            if target.startswith("socket:["):
                inodes.add(target[8:-1])
    return inodes


def _discord_pids(install: Install | None) -> list[int]:
    names = {"discord", "discordptb", "discordcanary"}
    target = None
    if install is not None and install.executable is not None:
        target = install.executable.resolve(strict=False)
    pids: list[int] = []
    proc = Path("/proc")
    try:
        entries = list(proc.iterdir())
    except OSError:
        return pids
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            linked = (entry / "exe").resolve(strict=True)
        except OSError:
            continue
        if (target is not None and linked == target) or linked.name.lower() in names:
            pids.append(int(entry.name))
    return pids


def _parse_proc_address(field: str, size: int) -> Endpoint | None:
    """`0100007F:14E9` vem em hex, little-endian por palavra de 32 bits."""
    text, _, port_text = field.partition(":")
    try:
        port = int(port_text, 16)
    except ValueError:
        return None
    if port == 0 or port in WEB_PORTS or len(text) != size * 2:
        return None
    raw = bytes.fromhex(text)
    packed = b"".join(raw[index : index + 4][::-1] for index in range(0, len(raw), 4))
    try:
        address = ipaddress.ip_address(packed)
    except ValueError:
        return None
    if address.is_private or address.is_loopback or address.is_multicast or address.is_unspecified:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return Endpoint(address=str(address), port=port)


def _with_hostname(endpoint: Endpoint) -> Endpoint:
    if endpoint.hostname:
        return endpoint
    name = _reverse_dns(endpoint.address)
    return Endpoint(address=endpoint.address, port=endpoint.port, hostname=name)


def _reverse_dns(address: str) -> str:
    """O resolver do sistema não aceita timeout, então damos um por fora."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(socket.gethostbyaddr, address)
        try:
            return str(future.result(timeout=RESOLVE_TIMEOUT)[0])
        except (concurrent.futures.TimeoutError, OSError):
            return ""


# ------------------------------------------------------------- consulta web --


def _https_get_json(host: str, path: str, *, proxy: Proxy | None) -> dict:
    """GET simples em HTTPS, direto ou por dentro do proxy configurado."""
    if proxy is not None:
        raw, _ = bridge_module.open_tunnel(proxy, host, 443)
    else:
        raw = socket.create_connection((host, 443), LOOKUP_TIMEOUT)
    raw.settimeout(LOOKUP_TIMEOUT)
    context = ssl.create_default_context()
    try:
        with context.wrap_socket(raw, server_hostname=host) as secure:
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                "User-Agent: discord-proxy\r\n"
                "Accept: application/json\r\n"
                "Connection: close\r\n\r\n"
            )
            secure.sendall(request.encode("ascii"))
            chunks = []
            while True:
                block = secure.recv(65536)
                if not block:
                    break
                chunks.append(block)
                if sum(len(item) for item in chunks) > 256 * 1024:
                    break
    except BaseException:
        raw.close()
        raise
    payload = b"".join(chunks)
    head, _, body = payload.partition(b"\r\n\r\n")
    status = _status_of(head)
    start = body.find(b"{")
    end = body.rfind(b"}")
    if start < 0 or end <= start:
        if status and status != 200:
            raise ValueError(f"respondeu HTTP {status} (o serviço costuma recusar saídas do Tor)")
        raise ValueError("respondeu algo que não é JSON")
    return json.loads(body[start : end + 1].decode("utf-8", "replace"))


def _status_of(head: bytes) -> int:
    first = head.split(b"\r\n", 1)[0].decode("iso-8859-1", "replace").split(" ")
    return int(first[1]) if len(first) > 1 and first[1].isdigit() else 0


def _first(body: dict, *names: str) -> str:
    for name in names:
        value = body.get(name)
        if value:
            return str(value)
    return ""


def _place_from(body: dict) -> Place:
    """Cada serviço nomeia os campos do seu jeito; aceitamos os três."""
    place = Place(
        address=_first(body, "ip", "IP", "query"),
        country=_first(body, "country", "country_iso", "countryCode"),
        region=_first(body, "region", "region_name", "regionName"),
        city=_first(body, "city"),
        org=_first(body, "org", "asn_org", "isp"),
    )
    if body.get("IsTor") and not place.country:
        return Place(address=place.address, country="saída do Tor", org=place.org)
    return place
