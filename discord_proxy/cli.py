"""Linha de comando do Discord Proxy."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from . import bridge as bridge_module
from . import region as region_module
from . import run as run_module
from . import shortcut as shortcut_module
from . import voice as voice_module
from .config import (
    CONFIG_NAME,
    ConfigError,
    load_or_default,
    parse_proxy,
    save,
    validate_packet,
)
from .discord import CHANNELS, detect, detect_channel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-proxy",
        description=(
            "Faz o Discord sair pela internet por outro lugar, o que muda a "
            "região do servidor de voz — e é por ele que passam a câmera e o "
            "compartilhamento de tela. Sem argumentos, abre a janela."
        ),
    )
    subcommands = parser.add_subparsers(dest="command")

    def add_common(sub: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sub.add_argument(
            "--channel", choices=CHANNELS, default="stable", help="canal do Discord"
        )
        sub.add_argument("--config", type=Path, help=f"caminho de um {CONFIG_NAME}")
        return sub

    add_common(subcommands.add_parser("run", help="abre o Discord")).add_argument(
        "--no-wait",
        action="store_true",
        help="não segura o terminal (a ponte fecha junto, use só no modo direto)",
    )
    add_common(subcommands.add_parser("plan", help="mostra o que seria feito, sem abrir nada"))
    add_common(subcommands.add_parser("test", help="testa só o proxy"))
    subcommands.add_parser("detect", help="lista as instalações encontradas")
    subcommands.add_parser("gui", help="abre a janela de configuração")

    config_command = subcommands.add_parser("config", help="lê ou grava a configuração")
    config_command.add_argument("--config", type=Path, help="caminho do arquivo")
    config_command.add_argument("--proxy", help="define o proxy (vazio = modo direto)")
    config_command.add_argument(
        "--voice", choices=("on", "off"), help="liga ou desliga o ajuste de voz"
    )
    config_command.add_argument("--delay", type=int, help="pausa em milissegundos")
    config_command.add_argument("--packet", type=Path, help="arquivo .bin inicial")

    link = add_common(subcommands.add_parser("shortcut", help="cria o atalho Discord (Proxy)"))
    link.add_argument("--remove", action="store_true", help="remove o atalho")

    region_command = add_common(
        subcommands.add_parser("region", help="em que região a chamada de agora está caindo")
    )
    region_command.add_argument(
        "--online",
        action="store_true",
        help=f"também pergunta o país a um serviço externo ({region_module.LOOKUP_HOST})",
    )

    exit_command = subcommands.add_parser(
        "exit-ip", help="que IP e país o proxy apresenta (consulta um serviço externo)"
    )
    exit_command.add_argument("--config", type=Path, help="caminho do arquivo")

    subcommands.add_parser("clean", help="remove atalhos e o componente nativo instalado")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    command = arguments.command or "gui"
    try:
        return _dispatch(command, arguments)
    except (ConfigError, run_module.LaunchError, voice_module.VoiceError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def _dispatch(command: str, arguments: argparse.Namespace) -> int:
    if command == "gui":
        from .ui import run_ui

        return run_ui()

    if command == "detect":
        return _detect()

    if command == "config":
        return _config(arguments)

    if command == "test":
        config = load_or_default(_path_for(arguments))
        result = bridge_module.test_proxy(config.proxy)
        print(f"{config.proxy.label}: {result.message}")
        return 0 if result.ok else 1

    if command == "plan":
        plan = run_module.build_plan(
            arguments.channel,
            explicit_config=arguments.config,
            bridge_url="http://127.0.0.1:0 (a ponte real usa uma porta livre)",
        )
        print(run_module.describe(plan))
        print("\nComando:")
        print("  " + " ".join(plan.command))
        return 0

    if command == "region":
        return _region(arguments)

    if command == "exit-ip":
        return _exit_ip(arguments)

    if command == "shortcut":
        return _shortcut(arguments)

    if command == "clean":
        return _clean()

    if command == "run":
        result = run_module.launch(
            arguments.channel,
            explicit_config=arguments.config,
            wait=False if arguments.no_wait else None,
        )
        proxy = "com proxy" if result.proxy_used else "sem proxy"
        voice = "com ajuste de voz" if result.voice_used else "sem ajuste de voz"
        print(f"Discord aberto ({proxy}, {voice}). pid {result.pid}")
        if result.note:
            print(f"aviso: {result.note}", file=sys.stderr)
        return 0

    raise AssertionError(f"comando não tratado: {command}")


def _detect() -> int:
    found = detect()
    if not found:
        print("Nenhuma instalação do Discord encontrada.")
        return 1
    for install in found:
        voice = "sim" if install.supports_voice else f"não ({install.voice_reason})"
        print(f"{install.label}")
        print(f"  tipo ......: {install.kind}")
        print(f"  comando ...: {' '.join(install.command)}")
        print(f"  voz .......: {voice}")
    return 0


def _config(arguments: argparse.Namespace) -> int:
    path = _path_for(arguments)
    config = load_or_default(path)
    updates: dict[str, object] = {}

    if arguments.proxy is not None:
        updates["proxy"] = parse_proxy(arguments.proxy)
    if arguments.voice is not None:
        updates["voice"] = arguments.voice == "on"
    if arguments.delay is not None:
        updates["delay_ms"] = arguments.delay
    if arguments.packet is not None:
        updates["packet"] = validate_packet(arguments.packet)

    if updates:
        config = replace(config, **updates)
        save(path, config)
        print(f"gravado em {path}")
    else:
        print(f"# {path}")
        print(config.as_ini(), end="")
    return 0


def _region(arguments: argparse.Namespace) -> int:
    install = detect_channel(arguments.channel)
    endpoints = region_module.voice_endpoints(install)
    if not endpoints:
        print("Nenhuma chamada de voz ativa no momento.")
        print(
            "Entre numa chamada (ou comece um Go Live) e rode de novo — é durante a\n"
            "chamada que dá para ver o servidor em uso."
        )
        if os.name == "nt":
            print(
                "\nNo Windows isto depende do componente nativo: ligue `voice = on`\n"
                "e abra o Discord pelo launcher, senão não há como ver o destino."
            )
        return 1

    print("Servidor de voz em uso (é por ele que passam a câmera e o Go Live):")
    for endpoint in endpoints:
        print(f"  {endpoint}")
        if arguments.online:
            print(f"    consultando {region_module.LOOKUP_HOST}…")
            try:
                print(f"    {region_module.locate(endpoint.address)}")
            except (OSError, ValueError) as exc:
                print(f"    não deu para consultar: {exc}")
    if not any(endpoint.region for endpoint in endpoints) and not arguments.online:
        print("\nO nome do servidor não veio pelo DNS. Use --online para perguntar o país.")
    return 0


def _exit_ip(arguments: argparse.Namespace) -> int:
    config = load_or_default(_path_for(arguments))
    print(f"Consultando {region_module.LOOKUP_HOST} através de: {config.proxy.label}")
    try:
        place = region_module.exit_address(config.proxy)
    except (OSError, ValueError, bridge_module.ProxyError) as exc:
        print(f"não deu para consultar: {exc}", file=sys.stderr)
        return 1
    print(f"  {place}")
    if not config.proxy.enabled:
        print("\nEste é o seu IP de verdade — não há proxy configurado.")
    return 0


def _shortcut(arguments: argparse.Namespace) -> int:
    install = detect_channel(arguments.channel)
    if install is None:
        print(f"canal '{arguments.channel}' não encontrado", file=sys.stderr)
        return 1
    if arguments.remove:
        print("atalho removido" if shortcut_module.remove(install) else "nenhum atalho encontrado")
        return 0
    created = shortcut_module.create(install)
    print(f"atalho criado em {created.path}")
    return 0


def _clean() -> int:
    removed: list[str] = []
    for channel in CHANNELS:
        install = detect_channel(channel)
        if install is None:
            continue
        if shortcut_module.remove(install):
            removed.append(f"atalho de {install.label}")
        try:
            if voice_module.remove_shim(install):
                removed.append(f"componente nativo de {install.label}")
        except voice_module.VoiceError as exc:
            print(f"aviso: {exc}", file=sys.stderr)
    if voice_module.remove_shared_shim():
        removed.append("componente nativo guardado na pasta de dados")
    print("removido: " + ", ".join(removed) if removed else "nada para remover")
    print(f"a configuração continua em {run_module.default_config_path()}")
    return 0


def _path_for(arguments: argparse.Namespace) -> Path:
    explicit = getattr(arguments, "config", None)
    channel = getattr(arguments, "channel", "stable")
    install = detect_channel(channel) if explicit is None else None
    return run_module.config_path(install, explicit=explicit)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
