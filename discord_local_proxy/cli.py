from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .config import (
    AppConfig,
    ConfigError,
    ProxySettings,
    VoiceSettings,
    default_config,
    load_config,
    save_config,
)
from .discovery import CHANNELS, default_config_path, discover_installations
from .diagnostics import (
    LOGGER,
    configure_logging,
    log_file_path,
    log_hint,
    open_log_directory,
    record_exception,
    record_session,
)
from .installer import InstallError, install, status, uninstall
from .launcher import LaunchError, launch_discord
from .native import NativeShimError
from .proxy_bridge import probe_proxy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-local-proxy",
        description="Proxy local por processo e compatibilidade experimental de voz para Discord.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("gui", help="abrir o instalador gráfico")
    subparsers.add_parser("check-gui", help="verificar o runtime gráfico")
    subparsers.add_parser("check-font", help="verificar a tipografia da interface")
    detect = subparsers.add_parser("detect", help="listar instalações detectadas")
    detect.add_argument("--json", action="store_true", dest="as_json")

    launch = subparsers.add_parser("launch", help="iniciar um canal pelo launcher")
    launch.add_argument("--channel", choices=CHANNELS, required=True)
    launch.add_argument("--config", type=Path)

    install_parser = subparsers.add_parser("install", help="instalar atalhos por usuário")
    install_parser.add_argument("--channels", nargs="+", choices=CHANNELS, required=True)
    install_parser.add_argument("--from-config", type=Path)
    install_parser.add_argument("--proxy-type", choices=("none", "http", "socks5"), default="none")
    install_parser.add_argument("--host", default="")
    install_parser.add_argument("--port", type=int, default=0)
    install_parser.add_argument("--username", default="")
    install_parser.add_argument("--password-env", default="")
    install_parser.add_argument(
        "--prompt-password",
        action="store_true",
        help="ler a senha sem colocá-la na linha de comando",
    )
    install_parser.add_argument("--no-voice", action="store_true")
    install_parser.add_argument("--voice-delay-ms", type=int, default=50)
    install_parser.add_argument("--voice-packet-file", type=Path)

    uninstall_parser = subparsers.add_parser("uninstall", help="remover atalhos e componentes")
    uninstall_parser.add_argument("--purge-config", action="store_true")

    check = subparsers.add_parser("check", help="validar um INI e testar o proxy")
    check.add_argument("--config", type=Path, required=True)

    init = subparsers.add_parser("init-config", help="criar um INI manual padrão")
    init.add_argument("path", type=Path)
    init.add_argument("--force", action="store_true")

    subparsers.add_parser("status", help="mostrar estado da instalação")
    logs = subparsers.add_parser("logs", help="mostrar a pasta dos registros de diagnóstico")
    logs.add_argument("--open", action="store_true", help="abrir a pasta no gerenciador de arquivos")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "gui"
    record_session(command)
    try:
        exit_code = _run_command(parser, args, command)
    except (ConfigError, InstallError, LaunchError, NativeShimError) as exc:
        record_exception(f"comando {command} não concluído", exc, warning=True)
        _report_error(str(exc))
        return 2
    except Exception as exc:
        record_exception(f"falha inesperada no comando {command}", exc)
        _report_error(f"Falha inesperada: {exc}")
        return 3
    LOGGER.info("comando concluído | comando=%s | código=%s", command, exit_code)
    return exit_code


def _run_command(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    command: str,
) -> int:
    if command == "gui":
        return gui_main(session_started=True)
    if command == "check-gui":
        return _check_gui_runtime()
    if command == "check-font":
        return _check_font_runtime()
    if command == "detect":
        return _detect(args.as_json)
    if command == "launch":
        result = launch_discord(args.channel, config_path=args.config)
        if result.exit_code not in (None, 0):
            return result.exit_code
        return 0
    if command == "install":
        config = _config_from_install_args(args)
        result = install(args.channels, config)
        for item in result.channels:
            print(f"{item.channel}: {item.config_path}")
        return 0
    if command == "uninstall":
        result = uninstall(purge_config=args.purge_config)
        print(f"Removidos: {len(result.removed)}")
        if result.preserved_configs:
            print(f"INIs preservados: {len(result.preserved_configs)}")
        for warning in result.warnings:
            print(f"Aviso: {warning}", file=sys.stderr)
        return 0
    if command == "check":
        config = load_config(args.config)
        result = probe_proxy(config.proxy)
        print(result.message)
        return 0 if result.ok else 1
    if command == "init-config":
        if args.path.exists() and not args.force:
            raise ConfigError(f"{args.path} já existe; use --force para substituir")
        save_config(args.path, default_config())
        print(args.path)
        return 0
    if command == "status":
        current_status = status()
        current_status["logs"] = str(log_file_path())
        print(json.dumps(current_status, ensure_ascii=False, indent=2))
        return 0
    if command == "logs":
        path = log_file_path().parent
        if args.open:
            try:
                path = open_log_directory()
            except OSError as exc:
                raise ConfigError(f"não foi possível abrir a pasta de logs: {exc}") from exc
        print(path)
        return 0
    parser.error(f"comando desconhecido: {command}")
    return 2


def gui_main(*, session_started: bool = False) -> int:
    configure_logging()
    if not session_started:
        record_session("gui")
    try:
        from .gui import run_gui
    except ImportError as exc:
        record_exception("runtime gráfico indisponível", exc)
        _report_error("Tk não está disponível. Instale python3-tk ou use um pacote binário.")
        return 2
    try:
        return run_gui()
    except Exception as exc:
        record_exception("falha inesperada na interface", exc)
        _report_error(f"Falha inesperada na interface: {exc}")
        return 3


def _check_gui_runtime() -> int:
    try:
        import _tkinter  # noqa: F401
        import tkinter  # noqa: F401
    except ImportError as exc:
        record_exception("verificação do runtime gráfico falhou", exc)
        _report_error(f"runtime gráfico indisponível: {exc}")
        return 2
    return 0


def _check_font_runtime() -> int:
    try:
        from .gui import font_diagnostics

        result = font_diagnostics()
    except Exception as exc:
        record_exception("verificação de fontes falhou", exc)
        _report_error(f"não foi possível verificar as fontes: {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["healthy"] else 2


def _detect(as_json: bool) -> int:
    installations = discover_installations()
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "channel": item.channel,
                        "label": item.label,
                        "source": item.source,
                        "command": list(item.command),
                        "path": item.display_path,
                        "config": str(default_config_path(item)),
                        "voice_supported": item.supports_udp_shim,
                    }
                    for item in installations
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    elif not installations:
        print("Nenhuma instalação do Discord encontrada.")
    else:
        for item in installations:
            voice = "voz: sim" if item.supports_udp_shim else "voz: não neste pacote"
            print(f"{item.label}: {item.display_path} [{item.source}; {voice}]")
    return 0


def _config_from_install_args(args: argparse.Namespace) -> AppConfig:
    if args.from_config:
        return load_config(args.from_config)
    password = getpass.getpass("Senha do proxy: ") if args.prompt_password else ""
    kind = args.proxy_type
    return AppConfig(
        proxy=ProxySettings(
            kind=kind,
            host=args.host if kind != "none" else "",
            port=args.port if kind != "none" else 0,
            username=args.username if kind != "none" else "",
            password=password if kind != "none" else "",
            password_env=args.password_env if kind != "none" else "",
        ),
        voice=VoiceSettings(
            enabled=not args.no_voice,
            delay_ms=args.voice_delay_ms,
            packet_file=args.voice_packet_file,
        ),
    )


def _report_error(message: str) -> None:
    detailed_message = f"{message}\n\n{log_hint()}"
    if sys.stderr is not None:
        print(f"Erro: {detailed_message}", file=sys.stderr)
        return
    if getattr(sys, "frozen", False):
        try:
            from tkinter import messagebox

            messagebox.showerror("Discord Local Proxy", detailed_message)
        except BaseException:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
