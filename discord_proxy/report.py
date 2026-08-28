"""Relatório de diagnóstico em .txt.

Serve para quando algo não funciona e a pessoa precisa mostrar para alguém que
entenda. Junta num arquivo só tudo o que costuma ser perguntado: o sistema, o
Discord encontrado, a configuração, o que a ponte registrou e o que o Tor disse.

Senha nunca entra aqui. O que aparece é `socks5://ana:***@servidor:1080`.
"""

from __future__ import annotations

import os
import platform
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from . import region as region_module
from . import tor as tor_module
from . import voice as voice_module
from .config import CONFIG_NAME, ConfigError, load_or_default
from .discord import CHANNELS, detect_channel

REPORT_NAME = "discord-proxy-relatorio.txt"
LINE = "-" * 68


def report_path(directory: Path | None = None) -> Path:
    """Por padrão vai para a Área de Trabalho, que é onde a pessoa acha."""
    if directory is not None:
        return Path(directory) / REPORT_NAME
    for name in ("Desktop", "Área de Trabalho", "Area de Trabalho"):
        candidate = Path.home() / name
        if candidate.is_dir():
            return candidate / REPORT_NAME
    return voice_module.data_root() / REPORT_NAME


def build(*, config_path: Path | None = None) -> str:
    blocks = [
        _header(),
        _system(),
        _discord(),
        _configuration(config_path),
        _tor(),
        _bridge_log(),
        _tor_log(),
        _footer(),
    ]
    return "\n".join(blocks)


def save(directory: Path | None = None, *, config_path: Path | None = None) -> Path:
    path = report_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(config_path=config_path), encoding="utf-8")
    return path


def _title(text: str) -> str:
    return f"\n{LINE}\n{text}\n{LINE}"


def _header() -> str:
    return (
        f"RELATÓRIO DO DISCORD PROXY\n"
        f"Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M:%S')}\n"
        f"Versão do programa: {__version__}\n"
        "\n"
        "Este arquivo pode ser enviado para quem for te ajudar.\n"
        "Ele NÃO contém a sua senha nem o conteúdo das suas conversas."
    )


def _system() -> str:
    frozen = "sim (programa pronto)" if getattr(sys, "frozen", False) else "não (rodando pelo código)"
    lines = [
        _title("SISTEMA"),
        f"Sistema     : {platform.system()} {platform.release()}",
        f"Detalhe     : {platform.platform()}",
        f"Arquitetura : {platform.machine()}",
        f"Python      : {platform.python_version()}",
        f"Empacotado  : {frozen}",
        f"Pasta de dados: {voice_module.data_root()}",
    ]
    try:
        import tkinter

        lines.append(f"Tk (janela) : disponível, versão {tkinter.TkVersion}")
    except Exception as exc:  # noqa: BLE001 - queremos o motivo exato aqui
        lines.append(f"Tk (janela) : INDISPONÍVEL — {exc}")
    return "\n".join(lines)


def _discord() -> str:
    lines = [_title("DISCORD ENCONTRADO")]
    achou = False
    for channel in CHANNELS:
        try:
            install = detect_channel(channel)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{channel}: erro ao procurar — {exc}")
            continue
        if install is None:
            lines.append(f"{channel}: não encontrado")
            continue
        achou = True
        lines.append(f"{channel}: {install.label}")
        lines.append(f"   tipo     : {install.kind}")
        lines.append(f"   caminho  : {install.executable or ' '.join(install.command)}")
        lines.append(
            f"   ajuste de voz: {'suportado' if install.supports_voice else install.voice_reason}"
        )
    if not achou:
        lines.append("")
        lines.append("NENHUM Discord foi encontrado. Se ele está instalado num lugar")
        lines.append("diferente, informe o caminho no campo 'Discord' da janela.")
    return "\n".join(lines)


def _configuration(config_path: Path | None) -> str:
    from . import run as run_module

    path = config_path or run_module.default_config_path()
    lines = [_title("CONFIGURAÇÃO"), f"Arquivo: {path}"]
    if not Path(path).is_file():
        lines.append("(o arquivo ainda não existe — o programa está usando os padrões)")
        return "\n".join(lines)
    try:
        config = load_or_default(path)
    except ConfigError as exc:
        lines.append(f"ERRO ao ler a configuração: {exc}")
        return "\n".join(lines)

    lines += [
        f"Saída        : {config.exit_label}",
        f"País pedido  : {config.country or '(automático)'}",
        f"Ajuste de voz: {'ligado' if config.voice else 'desligado'}",
        f"Pausa        : {config.delay_ms} ms",
        f"Pacote .bin  : {config.packet or '(nenhum)'}",
        f"Discord manual: {config.executable or '(detecção automática)'}",
        "",
        "Conteúdo do arquivo (a senha aparece trocada por ***):",
    ]
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            lines.append("   " + _mask(line))
    except OSError as exc:
        lines.append(f"   (não deu para ler: {exc})")
    return "\n".join(lines)


def _mask(line: str) -> str:
    """Esconde a senha de uma linha `proxy = esquema://user:senha@host:porta`."""
    if "=" not in line:
        return line
    key, _, value = line.partition("=")
    if key.strip().lower() != "proxy" or "@" not in value:
        return line
    before, _, after = value.rpartition("@")
    scheme, sep, credentials = before.rpartition("//")
    user = credentials.split(":", 1)[0]
    return f"{key}={scheme}{sep}{user}:***@{after}"


def _tor() -> str:
    lines = [_title("TOR")]
    try:
        program = tor_module.find_tor()
        lines.append(f"Encontrado: {program.label}")
        lines.append(f"Programa  : {program.executable}")
    except tor_module.TorError as exc:
        lines.append(f"NÃO encontrado — {exc}")
        lines.append("")
        lines.append("Onde o programa procurou:")
        for place in tor_module.search_locations():
            lines.append(f"   {place}")
    return "\n".join(lines)


def _bridge_log() -> str:
    lines = [_title("O QUE PASSOU PELA PONTE (últimas 40 linhas)")]
    try:
        registered = region_module.journal_path().read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        lines.append("(nada registrado — a ponte só escreve com o Discord aberto por aqui)")
        return "\n".join(lines)
    if not registered:
        lines.append("(registro vazio)")
        return "\n".join(lines)
    lines += ["   " + item for item in registered[-40:]]
    slow = [item for item in registered if _slow(item)]
    if slow:
        lines.append("")
        lines.append(
            f"ATENÇÃO: {len(slow)} conexões passaram de 30 segundos. Isso costuma "
            "significar saída lenta — é o que faz imagem sumir ao enviar."
        )
    return "\n".join(lines)


def _slow(line: str) -> bool:
    fields = line.split()
    if not fields or not fields[-1].endswith("s"):
        return False
    try:
        return float(fields[-1][:-1]) > 30.0
    except ValueError:
        return False


def _tor_log() -> str:
    lines = [_title("ÚLTIMAS MENSAGENS DO TOR")]
    path = voice_module.data_root() / "tor" / "tor.log"
    try:
        recorded = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines.append("(o Tor ainda não foi usado por aqui)")
        return "\n".join(lines)
    lines += ["   " + item for item in recorded[-30:]] or ["(vazio)"]
    return "\n".join(lines)


def _footer() -> str:
    return (
        _title("O QUE FAZER COM ISTO")
        + "\n"
        "1. Se aparecer 'NENHUM Discord foi encontrado', o programa não achou o\n"
        "   Discord — informe o caminho na janela.\n"
        "2. Se o Tor aparecer como 'NÃO encontrado', instale o Tor Browser em\n"
        "   torproject.org e deixe a pasta em Downloads.\n"
        "3. Se houver conexões acima de 30 segundos, a saída está lenta: imagens\n"
        "   grandes vão falhar. Troque de país ou use um proxy mais rápido.\n"
        "4. Qualquer outra coisa: mande este arquivo para quem for te ajudar."
    )
