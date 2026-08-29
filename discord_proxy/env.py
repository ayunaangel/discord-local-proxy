"""Limpar do ambiente o rastro do empacotador antes de abrir outro programa.

Quando o programa roda empacotado (PyInstaller), o carregador aponta
`LD_LIBRARY_PATH` para a pasta temporária onde ele descompactou as bibliotecas
dele — libssl, libz, libfontconfig e companhia. Isso é necessário para o nosso
Python, mas é veneno para qualquer processo filho: o Discord é um Electron que
já traz as próprias bibliotecas e conta com as do sistema, e ao herdar essa
variável ele carrega as nossas por engano.

O resultado não é um erro visível, é morte instantânea antes de qualquer log:

    Discord: symbol lookup error: /lib64/libpangoft2-1.0.so.0:
             undefined symbol: FcConfigSetDefaultSubstitute

Da janela, parecia que o Discord simplesmente não abria.
"""

from __future__ import annotations

import os
import sys
from typing import Mapping

# Variáveis de caminho de biblioteca que o empacotador reescreve, por sistema.
LIBRARY_PATHS = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LIBPATH")

# Variáveis internas do empacotador; nenhum filho tem o que fazer com elas.
INTERNAL_PREFIXES = ("_PYI_", "_MEIPASS")


def bundle_directory() -> str:
    """A pasta temporária do empacotador, ou vazio se não estamos empacotados."""
    if not getattr(sys, "frozen", False):
        return ""
    return str(getattr(sys, "_MEIPASS", "") or "")


def strip_bundle(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Devolve uma cópia do ambiente sem nada que aponte para o empacotador.

    Rodando a partir do código-fonte não há o que limpar, e a cópia sai igual.
    """
    environment = dict(os.environ if source is None else source)
    # A própria variável interna diz onde o pacote foi aberto; serve de reserva
    # quando o `sys._MEIPASS` não está à mão (modo pasta, processo herdado).
    bundle = bundle_directory() or environment.get("_PYI_APPLICATION_HOME_DIR", "")

    for name in list(environment):
        if name.startswith(INTERNAL_PREFIXES):
            del environment[name]

    for name in LIBRARY_PATHS:
        original = environment.pop(f"{name}_ORIG", None)
        if original is not None:
            # O empacotador guarda aqui o valor que existia antes dele; é o
            # único jeito de devolver ao filho o que o sistema tinha.
            environment[name] = original
            continue
        if bundle:
            _drop_entry(environment, name, bundle)

    return environment


def _drop_entry(environment: dict[str, str], name: str, unwanted: str) -> None:
    """Tira só a pasta do empacotador da lista, preservando o resto."""
    current = environment.get(name)
    if current is None:
        return
    kept = [part for part in current.split(os.pathsep) if part and part != unwanted]
    if kept:
        environment[name] = os.pathsep.join(kept)
    else:
        del environment[name]
