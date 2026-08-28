"""Tor embutido: acha o programa, sobe escondido e espera o circuito ficar pronto.

A ideia é que ninguém precise abrir o Tor Browser nem mexer em terminal. Se o
Tor Browser estiver instalado, usamos só o programa `tor` de dentro dele — sem
abrir janela nenhuma, numa porta própria, com os dados numa pasta nossa. O Tor
Browser continua funcionando normalmente ao mesmo tempo.

Também dá para escolher o país de saída, que é o ponto todo: o Discord decide a
região da chamada pelo país de onde você parece vir.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .voice import data_root

BOOTSTRAP_TIMEOUT = 240.0
STOP_TIMEOUT = 10.0

# Países que costumam ter saída no Tor e servem para a troca de região.
COUNTRIES = {
    "": "Automático (o Tor escolhe)",
    "us": "Estados Unidos",
    "nl": "Holanda",
    "de": "Alemanha",
    "fr": "França",
    "gb": "Reino Unido",
    "es": "Espanha",
    "se": "Suécia",
    "ch": "Suíça",
    "ca": "Canadá",
    "fi": "Finlândia",
    "at": "Áustria",
    "pl": "Polônia",
    "ro": "Romênia",
    "jp": "Japão",
    "au": "Austrália",
}


class TorError(RuntimeError):
    """O Tor não foi encontrado, não subiu ou demorou demais."""


@dataclass(frozen=True)
class TorProgram:
    """Onde está o executável e o que ele precisa por perto."""

    executable: Path
    library_dir: Path | None
    label: str
    geoip: Path | None = None
    geoip6: Path | None = None

    @property
    def can_choose_country(self) -> bool:
        """Escolher país exige as tabelas de GeoIP; sem elas o Tor nem sobe."""
        return self.geoip is not None


class TorProcess:
    """Um Tor rodando só para nós, numa porta própria."""

    def __init__(self, process: subprocess.Popen, port: int, log: Path):
        self.process = process
        self.port = port
        self.log = log

    @property
    def proxy_url(self) -> str:
        return f"socks5://127.0.0.1:{self.port}"

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.process.kill()
            try:
                self.process.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:  # pragma: no cover - só se o SO travar
                pass

    def __enter__(self) -> "TorProcess":
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


# --------------------------------------------------------------- onde está --


def search_locations() -> list[Path]:
    """Pastas onde o Tor Browser costuma ficar, por sistema."""
    home = Path.home()
    if os.name == "nt":
        bases = [
            os.environ.get("USERPROFILE", str(home)),
            os.environ.get("LOCALAPPDATA", ""),
            os.environ.get("PROGRAMFILES", ""),
            os.environ.get("PROGRAMFILES(X86)", ""),
        ]
        names = ["Tor Browser", "Desktop/Tor Browser", "Downloads/Tor Browser"]
        return [Path(base) / name for base in bases if base for name in names]
    if sys.platform == "darwin":
        return [
            Path("/Applications/Tor Browser.app"),
            home / "Applications" / "Tor Browser.app",
            home / "Downloads" / "Tor Browser.app",
        ]
    return [
        home / "Downloads" / "tor-browser",
        home / "Downloads" / "tor-browser_pt-BR",
        home / "Baixados" / "tor-browser",
        home / "tor-browser",
        home / ".local" / "share" / "torbrowser" / "tbb" / "x86_64" / "tor-browser",
        home / "Aplicativos" / "tor-browser",
        Path("/opt/tor-browser"),
        Path("/usr/local/share/tor-browser"),
    ]


def find_tor(extra: Path | None = None) -> TorProgram:
    """Acha o `tor` — dentro do Tor Browser ou instalado no sistema."""
    candidates: list[Path] = []
    if extra is not None:
        candidates.append(Path(extra).expanduser())
    from_env = os.environ.get("DISCORD_PROXY_TOR")
    if from_env:
        candidates.append(Path(from_env).expanduser())
    candidates.extend(search_locations())

    for base in candidates:
        program = _program_inside(base)
        if program is not None:
            return program

    # Tor instalado pelo sistema (dnf/apt) — não precisa de bibliotecas ao lado.
    import shutil

    system = shutil.which("tor")
    if system:
        geoip, geoip6 = _geoip_near(Path(system), Path("/usr/share/tor"))
        return TorProgram(Path(system), None, "Tor do sistema", geoip, geoip6)

    raise TorError(
        "não encontrei o Tor. Instale o Tor Browser (torproject.org) e deixe a "
        "pasta em Downloads, ou informe o caminho no campo 'Pasta do Tor'."
    )


def _program_inside(base: Path) -> TorProgram | None:
    """Reconhece tanto a pasta do Tor Browser quanto o executável direto."""
    base = base.expanduser()
    if _runnable(base):
        return TorProgram(base, base.parent, base.name)

    name = "tor.exe" if os.name == "nt" else "tor"
    relatives = [
        Path("Browser") / "TorBrowser" / "Tor" / name,          # Windows e Linux
        Path("Contents") / "MacOS" / "Tor" / "tor.real",        # macOS
        Path("Contents") / "MacOS" / "Tor" / "tor",
        Path("TorBrowser") / "Tor" / name,
        Path(name),
    ]
    for relative in relatives:
        candidate = base / relative
        if _runnable(candidate):
            geoip, geoip6 = _geoip_near(candidate, base)
            return TorProgram(candidate, candidate.parent, _label_for(base), geoip, geoip6)
    return None


def _geoip_near(executable: Path, base: Path) -> tuple[Path | None, Path | None]:
    """As tabelas de país ficam em Data/Tor no Tor Browser, ou junto do binário."""
    places = [
        base / "Browser" / "TorBrowser" / "Data" / "Tor",
        base / "TorBrowser" / "Data" / "Tor",
        base / "Contents" / "Resources" / "TorBrowser" / "Tor",
        base / "Data" / "Tor",
        executable.parent,
        Path("/usr/share/tor"),
    ]
    for place in places:
        geoip = place / "geoip"
        if geoip.is_file():
            geoip6 = place / "geoip6"
            return geoip, geoip6 if geoip6.is_file() else None
    return None, None


def _label_for(base: Path) -> str:
    return f"Tor Browser em {base}"


def _runnable(path: Path) -> bool:
    try:
        info = path.stat()
    except OSError:
        return False
    if not path.is_file():
        return False
    return os.name == "nt" or bool(info.st_mode & 0o111)


def is_available(extra: Path | None = None) -> bool:
    try:
        find_tor(extra)
        return True
    except TorError:
        return False


# ------------------------------------------------------------------ subir --


def start(
    *,
    country: str = "",
    program: TorProgram | None = None,
    extra_path: Path | None = None,
    on_progress=None,
    timeout: float = BOOTSTRAP_TIMEOUT,
) -> TorProcess:
    """Sobe o Tor sem janela e só devolve quando o circuito estiver pronto."""
    program = program or find_tor(extra_path)
    port = _free_port()
    root = data_root() / "tor"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = root / "tor.log"

    arguments = [
        str(program.executable),
        "--SocksPort", f"127.0.0.1:{port}",
        "--DataDirectory", str(root / "dados"),
        "--ClientOnly", "1",
        "--AvoidDiskWrites", "1",
        "--Log", "notice stdout",
    ]
    if program.geoip is not None:
        arguments += ["--GeoIPFile", str(program.geoip)]
        if program.geoip6 is not None:
            arguments += ["--GeoIPv6File", str(program.geoip6)]

    country = (country or "").strip().lower()
    if country:
        if not re.fullmatch(r"[a-z]{2}", country):
            raise TorError(f"código de país inválido: {country}")
        if not program.can_choose_country:
            raise TorError(
                "este Tor não trouxe as tabelas de país (arquivo geoip), então não dá "
                "para escolher de onde sair. Use 'Automático', ou instale o Tor Browser "
                "completo em vez do tor avulso."
            )
        arguments += ["--ExitNodes", "{" + country + "}", "--StrictNodes", "1"]

    environment = dict(os.environ)
    if program.library_dir is not None and os.name != "nt":
        previous = environment.get("LD_LIBRARY_PATH", "")
        parts = [str(program.library_dir), str(program.library_dir.parent.parent)]
        environment["LD_LIBRARY_PATH"] = os.pathsep.join(parts + ([previous] if previous else []))

    try:
        process = subprocess.Popen(
            arguments,
            cwd=str(program.executable.parent),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_hidden_window(),
        )
    except OSError as exc:
        raise TorError(f"não consegui iniciar o Tor: {exc}") from exc

    return _wait_for_circuit(process, port, log, country, on_progress, timeout)


def _wait_for_circuit(process, port, log, country, on_progress, timeout) -> TorProcess:
    """Lê a saída do Tor até o circuito ficar pronto (ou dar errado)."""
    lines: list[str] = []
    done = threading.Event()
    failure: list[str] = []
    reached = [0]

    def read() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line.rstrip())
            percent = _percent_of(line)
            if percent is not None:
                reached[0] = max(reached[0], percent)
                if on_progress is not None:
                    on_progress(percent, _stage_of(line))
            if "Bootstrapped 100%" in line:
                done.set()
                return
            if "[err]" in line or "Failed to bind" in line:
                failure.append(line.strip())
                done.set()
                return
        done.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    done.wait(timeout)

    try:
        log.write_text("\n".join(lines[-400:]) + "\n", encoding="utf-8")
    except OSError:
        pass

    if failure:
        process.kill()
        raise TorError(_explain(failure[0], country))
    if not done.is_set() or process.poll() is not None:
        process.kill()
        onde = f" (parou em {reached[0]}%)" if reached[0] else ""
        conselho = (
            "Se você escolheu um país, tente 'Automático': pode não haver saída "
            "disponível nele agora."
            if country
            else "Verifique sua conexão com a internet."
        )
        raise TorError(
            f"o Tor não conseguiu abrir um circuito em {timeout:.0f} segundos{onde}. "
            f"{conselho} Na primeira vez ele baixa a lista de servidores e demora mais."
        )
    return TorProcess(process, port, log)


def _explain(line: str, country: str) -> str:
    if "Failed to bind" in line:
        return "a porta do Tor já estava ocupada; tente de novo."
    if country:
        return (
            f"o Tor não conseguiu subir com saída em {COUNTRIES.get(country, country)}. "
            "Pode não haver servidor disponível nesse país agora — tente 'Automático' "
            f"ou outro país. ({line})"
        )
    return f"o Tor falhou ao iniciar: {line}"


def _percent_of(line: str) -> int | None:
    match = re.search(r"Bootstrapped (\d+)%", line)
    return int(match.group(1)) if match else None


def _stage_of(line: str) -> str:
    match = re.search(r"Bootstrapped \d+% \(([^)]*)\)(?::\s*(.*))?", line)
    if not match:
        return ""
    return (match.group(2) or match.group(1) or "").strip()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _hidden_window() -> dict:
    """No Windows, impede que uma janela preta pisque na tela."""
    if os.name != "nt":
        return {"start_new_session": True}
    flags = 0
    for name in ("CREATE_NO_WINDOW", "DETACHED_PROCESS"):
        flags |= getattr(subprocess, name, 0)
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0  # SW_HIDE
    return {"creationflags": flags, "startupinfo": info}


def country_label(code: str) -> str:
    return COUNTRIES.get((code or "").strip().lower(), code or "Automático")


def wait_until_ready(port: int, timeout: float = 15.0) -> bool:
    """Confere que a porta SOCKS já aceita conexão."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 2):
                return True
        except OSError:
            time.sleep(0.3)
    return False
