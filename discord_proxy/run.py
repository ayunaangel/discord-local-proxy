"""Abrir o Discord com o proxy e o ajuste de voz aplicados só a ele."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping

from . import bridge as bridge_module
from . import tor as tor_module
from . import voice as voice_module
from . import config as _proxy_module
from .config import CONFIG_NAME, Config, load_or_default
from .discord import Install, detect_channel, install_for_executable, running_processes

# Variáveis lidas pelo componente nativo; sempre reescritas para não herdar lixo.
ENV_INI = "DISCORD_PROXY_INI"
ENV_VOICE = "DISCORD_PROXY_VOICE"
ENV_DELAY = "DISCORD_PROXY_DELAY"
ENV_PACKET = "DISCORD_PROXY_PACKET"
ENV_STATE = "DISCORD_PROXY_STATE"


class LaunchError(RuntimeError):
    """O Discord não pôde ser aberto do jeito pedido."""


@dataclass(frozen=True)
class Plan:
    """Tudo o que será feito, montado antes de qualquer efeito colateral."""

    install: Install
    config: Config
    command: tuple[str, ...]
    environment: dict[str, str]
    shim: Path | None
    voice_note: str = ""


@dataclass(frozen=True)
class Result:
    pid: int
    exit_code: int | None
    proxy_used: bool
    voice_used: bool
    note: str = ""


def config_path(
    install: Install | None = None,
    *,
    explicit: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Onde procurar o INI, do mais específico para o mais geral."""
    if explicit is not None:
        return Path(explicit).expanduser().absolute()
    from_env = (os.environ if environ is None else environ).get(ENV_INI)
    if from_env:
        return Path(from_env).expanduser().absolute()
    if install is not None and install.directory is not None:
        beside = install.directory / CONFIG_NAME
        if beside.is_file():
            return beside
    return default_config_path()


def default_config_path() -> Path:
    return voice_module.data_root() / CONFIG_NAME


def resolve_install(
    channel: str, config: Config, *, environ: Mapping[str, str] | None = None
) -> Install:
    if config.executable is not None:
        return install_for_executable(channel, config.executable)
    install = detect_channel(channel, environ=environ)
    if install is None:
        raise LaunchError(
            f"não encontrei o canal '{channel}'. Instale-o ou informe o caminho "
            "no campo `discord` do arquivo de configuração."
        )
    return install


def build_plan(
    channel: str = "stable",
    *,
    explicit_config: Path | None = None,
    bridge_url: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Plan:
    source = dict(os.environ if environ is None else environ)
    probe = detect_channel(channel, environ=source)
    path = config_path(probe, explicit=explicit_config, environ=source)
    config = load_or_default(path, environ=source)
    install = resolve_install(channel, config, environ=source)

    command = list(install.command)
    if config.has_exit:
        if not bridge_url:
            raise LaunchError("há uma saída configurada, mas a ponte local não foi iniciada")
        command += [
            f"--proxy-server={bridge_url}",
            "--proxy-bypass-list=<local>",
            # Sem isso o Chromium pode escapar do proxy usando QUIC (UDP).
            "--disable-quic",
        ]

    shim: Path | None = None
    note = ""
    if config.voice:
        if not install.supports_voice:
            note = install.voice_reason
        else:
            try:
                shim = voice_module.install_shim(install)
            except voice_module.VoiceError as exc:
                # Sem o componente nativo o Discord ainda abre — só perde o
                # ajuste de voz. Avisar e seguir é melhor que não abrir nada.
                note = str(exc)

    environment = _environment(source, config, shim, path)
    return Plan(
        install=install,
        config=config,
        command=tuple(command),
        environment=environment,
        shim=shim,
        voice_note=note,
    )


def launch(
    channel: str = "stable",
    *,
    explicit_config: Path | None = None,
    wait: bool | None = None,
    require_closed: bool = True,
    environ: Mapping[str, str] | None = None,
    on_started: Callable[[Result], None] | None = None,
    on_step: Callable[[str], None] | None = None,
) -> Result:
    source = dict(os.environ if environ is None else environ)
    probe = detect_channel(channel, environ=source)
    path = config_path(probe, explicit=explicit_config, environ=source)
    config = load_or_default(path, environ=source)
    install = resolve_install(channel, config, environ=source)

    if require_closed and running_processes(install):
        raise LaunchError(
            f"o {install.label} já está aberto. Feche-o por inteiro, inclusive o "
            "ícone da bandeja, e tente de novo — uma janela viva ignora os "
            "argumentos novos."
        )

    bridge: bridge_module.Bridge | None = None
    tor_process: tor_module.TorProcess | None = None
    try:
        url: str | None = None
        if config.has_exit:
            proxy = config.proxy
            if config.use_tor:
                _say(on_step, "Ligando o Tor… (na primeira vez costuma demorar)")
                tor_process = tor_module.start(
                    country=config.country,
                    extra_path=config.tor_path,
                    on_progress=lambda pct, etapa: _say(
                        on_step, f"Tor {pct}%{(' — ' + etapa) if etapa else ''}"
                    ),
                )
                proxy = _proxy_module.parse_proxy(tor_process.proxy_url)
                _say(on_step, "Tor pronto.")

            _say(on_step, "Testando a saída…")
            check = bridge_module.test_proxy(proxy)
            if not check.ok:
                raise LaunchError(
                    f"a saída falhou, então o Discord não foi aberto: {check.message}"
                )
            journal = voice_module.data_root(source) / "bridge-targets.txt"
            journal.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            journal.unlink(missing_ok=True)
            bridge = bridge_module.Bridge(proxy, journal=journal).start()
            url = bridge.url
            _say(on_step, "Abrindo o Discord…")

        plan = build_plan(
            channel, explicit_config=path, bridge_url=url, environ=source
        )
        process = subprocess.Popen(
            list(plan.command),
            cwd=str(plan.install.directory) if plan.install.directory else None,
            env=plan.environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_process_options(),
        )

        started = Result(
            pid=process.pid,
            exit_code=None,
            proxy_used=config.has_exit,
            voice_used=plan.shim is not None,
            note=plan.voice_note,
        )
        # Avisa antes de bloquear: com proxy, a espera dura o quanto o Discord
        # ficar aberto, e quem chamou precisa saber que já subiu.
        if on_started is not None:
            on_started(started)

        # Enquanto houver proxy, este processo precisa continuar vivo: a ponte
        # local morre junto com ele.
        should_wait = config.has_exit if wait is None else wait
        exit_code = _wait_for_discord(process, plan.install) if should_wait else None
        return replace(started, exit_code=exit_code)
    except OSError as exc:
        raise LaunchError(f"não consegui abrir o {install.label}: {exc}") from exc
    finally:
        if bridge is not None:
            bridge.stop()
        if tor_process is not None:
            tor_process.stop()


def _process_options() -> dict:
    """No Windows, o Discord é aberto sem piscar um console preto."""
    if os.name != "nt":
        return {"start_new_session": True}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _say(on_step: Callable[[str], None] | None, message: str) -> None:
    if on_step is not None:
        on_step(message)


def _environment(
    source: Mapping[str, str],
    config: Config,
    shim: Path | None,
    path: Path,
) -> dict[str, str]:
    blocked = {
        "ELECTRON_RUN_AS_NODE",
        "NODE_OPTIONS",
        "LD_PRELOAD",
        ENV_INI,
        ENV_VOICE,
        ENV_DELAY,
        ENV_PACKET,
        ENV_STATE,
    }
    lowered = {name.casefold() for name in blocked}
    environment = {k: v for k, v in source.items() if k.casefold() not in lowered}

    environment[ENV_VOICE] = "1" if config.voice and shim is not None else "0"
    environment[ENV_DELAY] = str(config.delay_ms)
    if config.packet is not None:
        environment[ENV_PACKET] = str(config.packet)
    environment[ENV_INI] = str(path)
    if shim is not None:
        # Onde o componente anota o servidor de voz em uso, para o `region`.
        state = voice_module.data_root(source) / "voice-endpoint.txt"
        state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        state.unlink(missing_ok=True)
        environment[ENV_STATE] = str(state)
    if shim is not None and os.name != "nt":
        environment["LD_PRELOAD"] = str(shim)
    return environment


def _wait_for_discord(process: "subprocess.Popen[bytes]", install: Install) -> int:
    """Espera o processo e a árvore dele; alguns pacotes só fazem exec e saem."""
    try:
        while process.poll() is None:
            time.sleep(0.5)
        exit_code = int(process.returncode or 0)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not running_processes(install):
            time.sleep(0.25)
        while running_processes(install):
            time.sleep(1.0)
        return exit_code
    except KeyboardInterrupt:
        # Ctrl+C encerra só o ajudante; o Discord segue aberto, sem proxy novo.
        return 130


def describe(plan: Plan) -> str:
    lines = [
        f"Canal .......: {plan.install.label} ({plan.install.kind})",
        f"Executável ..: {plan.install.executable or plan.install.command[0]}",
        f"Config ......: {plan.config.path or '(padrão embutido)'}",
        f"Saída .......: {plan.config.exit_label}",
    ]
    if plan.shim is not None:
        lines.append(f"Voz .........: ativa via {plan.shim}")
    elif plan.voice_note:
        lines.append(f"Voz .........: indisponível — {plan.voice_note}")
    else:
        lines.append("Voz .........: desligada na configuração")
    if plan.config.packet is not None:
        lines.append(f"Pacote ......: {plan.config.packet}")
    return "\n".join(lines)


def replace_proxy(config: Config, proxy_text: str) -> Config:
    """Ajuda a interface a trocar só o proxy sem tocar no resto."""
    from .config import parse_proxy

    return replace(config, proxy=parse_proxy(proxy_text))
