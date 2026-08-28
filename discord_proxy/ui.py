"""A janela do Discord Proxy.

Feita para quem não abre terminal: escolher de onde sair, clicar em um botão e
pronto. Tudo o que é opcional fica escondido atrás de "Ajustes avançados", e
qualquer erro vira um texto em português com o que fazer em seguida.
"""

from __future__ import annotations

import queue
import threading
import traceback
import webbrowser
from dataclasses import replace
from pathlib import Path

from . import bridge as bridge_module
from . import region as region_module
from . import report as report_module
from . import run as run_module
from . import shortcut as shortcut_module
from . import tor as tor_module
from . import voice as voice_module
from .config import Config, ConfigError, load_or_default, parse_proxy, save, validate_packet
from .discord import CHANNELS, CHANNEL_SPECS, detect_channel

IVORY = "#F6F1EA"
SURFACE = "#FFFDFC"
INK = "#0B0B0C"
VIOLET = "#2A1E5C"
MAGENTA = "#D82D91"
MUTED = "#6D6870"
LINE = "#DED7CF"
DANGER = "#96364C"
SUCCESS = "#1F6F4A"
ATENCAO = "#9A6412"

AQUI = "Daqui mesmo (sem trocar nada)"
MEU_PROXY = "Meu próprio proxy…"
TOR_PREFIXO = "Tor · "


def _exit_options() -> list[str]:
    opcoes = [AQUI]
    opcoes += [TOR_PREFIXO + label for label in tor_module.COUNTRIES.values()]
    opcoes.append(MEU_PROXY)
    return opcoes


def _country_of(option: str) -> str:
    label = option[len(TOR_PREFIXO) :]
    for code, name in tor_module.COUNTRIES.items():
        if name == label:
            return code
    return ""


class Window:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()
        self.busy = False
        self.session: threading.Thread | None = None
        self.session_alive = threading.Event()

        self.root = tk.Tk()
        self.root.title("Discord Proxy")
        self.root.configure(bg=IVORY)
        self.root.minsize(680, 640)

        self.exit_choice = tk.StringVar(value=AQUI)
        self.proxy_text = tk.StringVar()
        self.channel = tk.StringVar(value=CHANNEL_SPECS["stable"].label)
        self.voice = tk.BooleanVar(value=False)
        self.delay = tk.StringVar(value="50")
        self.packet = tk.StringVar()
        self.discord_path = tk.StringVar()
        self.tor_path = tk.StringVar()
        self.status = tk.StringVar(value="Pronto.")
        self.advanced_open = False

        self._build()
        self._load_current()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._drain)

    # ------------------------------------------------------------------ UI --

    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover
            pass
        style.configure("TFrame", background=IVORY)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("TLabel", background=IVORY, foreground=INK)
        style.configure("Card.TLabel", background=SURFACE, foreground=INK)
        style.configure("Muted.TLabel", background=SURFACE, foreground=MUTED)
        style.configure("Title.TLabel", background=IVORY, foreground=VIOLET, font=("", 17, "bold"))
        style.configure("Step.TLabel", background=SURFACE, foreground=VIOLET, font=("", 10, "bold"))
        style.configure("TButton", padding=(11, 6))
        style.configure("Go.TButton", padding=(18, 12), font=("", 11, "bold"))
        style.map(
            "Go.TButton",
            background=[("!disabled", MAGENTA), ("disabled", LINE)],
            foreground=[("!disabled", "#FFFFFF"), ("disabled", MUTED)],
        )
        style.configure("TCheckbutton", background=SURFACE, foreground=INK)

        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(5, weight=1)

        ttk.Label(outer, text="Discord Proxy", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            outer,
            text=(
                "Faz o Discord sair por outro país. Isso muda a região do servidor da\n"
                "chamada — o mesmo por onde passam a câmera e o compartilhamento de tela."
            ),
            foreground=MUTED,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, 14))

        card = ttk.Frame(outer, style="Card.TFrame", padding=16)
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)

        ttk.Label(card, text="PASSO 1 — De onde você quer sair", style="Step.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.exit_combo = ttk.Combobox(
            card, textvariable=self.exit_choice, values=_exit_options(), state="readonly"
        )
        self.exit_combo.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        self.exit_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_exit_change())

        self.proxy_row = ttk.Frame(card, style="Card.TFrame")
        self.proxy_row.columnconfigure(0, weight=1)
        ttk.Entry(self.proxy_row, textvariable=self.proxy_text).grid(row=0, column=0, sticky="ew")
        ttk.Label(
            self.proxy_row, text="exemplo: socks5://127.0.0.1:1080", style="Muted.TLabel"
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        self.exit_hint = ttk.Label(card, text="", style="Muted.TLabel", justify="left")
        self.exit_hint.grid(row=3, column=0, sticky="w", pady=(6, 0))

        card2 = ttk.Frame(outer, style="Card.TFrame", padding=16)
        card2.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        card2.columnconfigure(1, weight=1)
        ttk.Label(card2, text="PASSO 2 — Abrir o Discord", style="Step.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            card2,
            text=(
                "Feche o Discord por inteiro antes (inclusive o ícone ao lado do relógio).\n"
                "Depois de abrir, deixe esta janela aberta enquanto estiver usando."
            ),
            style="Muted.TLabel",
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 10))
        self.go = ttk.Button(
            card2, text="Abrir o Discord", style="Go.TButton", command=self._open_discord
        )
        self.go.grid(row=2, column=0, sticky="w")
        self.stop_button = ttk.Button(
            card2, text="Encerrar sessão", command=self._stop_session, state="disabled"
        )
        self.stop_button.grid(row=2, column=1, sticky="w", padx=(10, 0))

        tools = ttk.Frame(outer)
        tools.grid(row=4, column=0, sticky="ew", pady=(12, 8))
        ttk.Button(tools, text="Onde estou saindo", command=self._where).pack(side="left")
        ttk.Button(tools, text="Região da chamada", command=self._call_region).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(tools, text="Salvar relatório (.txt)", command=self._save_report).pack(
            side="left", padx=(8, 0)
        )
        self.advanced_button = ttk.Button(
            tools, text="Ajustes avançados ▾", command=self._toggle_advanced
        )
        self.advanced_button.pack(side="right")

        self.advanced = ttk.Frame(outer, style="Card.TFrame", padding=16)
        self.advanced.columnconfigure(1, weight=1)
        self._build_advanced()

        self.log = tk.Text(
            outer,
            height=10,
            wrap="word",
            bg=SURFACE,
            fg=INK,
            relief="flat",
            highlightthickness=1,
            highlightbackground=LINE,
            padx=10,
            pady=8,
        )
        self.log.grid(row=5, column=0, sticky="nsew")
        self.log.tag_configure("erro", foreground=DANGER)
        self.log.tag_configure("bom", foreground=SUCCESS)
        self.log.tag_configure("passo", foreground=VIOLET)
        self.log.tag_configure("atencao", foreground=ATENCAO)
        self.log.configure(state="disabled")

        ttk.Label(outer, textvariable=self.status, foreground=MUTED).grid(
            row=6, column=0, sticky="w", pady=(8, 0)
        )

    def _build_advanced(self) -> None:
        ttk = self.ttk
        ttk.Label(
            self.advanced,
            text="Você só precisa disto se algo não foi encontrado sozinho.",
            style="Muted.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Label(self.advanced, text="Canal do Discord", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=4
        )
        self.channel_combo = ttk.Combobox(
            self.advanced,
            textvariable=self.channel,
            values=[CHANNEL_SPECS[key].label for key in CHANNELS],
            state="readonly",
            width=20,
        )
        self.channel_combo.grid(row=1, column=1, sticky="w", pady=4, padx=(8, 0))

        campos = [
            ("Caminho do Discord", self.discord_path, "arquivo"),
            ("Pasta do Tor", self.tor_path, "pasta"),
            ("Pacote inicial", self.packet, "arquivo"),
            ("Pausa da voz (ms)", self.delay, None),
        ]
        for indice, (rotulo, variavel, tipo) in enumerate(campos, start=2):
            ttk.Label(self.advanced, text=rotulo, style="Card.TLabel").grid(
                row=indice, column=0, sticky="w", pady=4
            )
            ttk.Entry(self.advanced, textvariable=variavel).grid(
                row=indice, column=1, sticky="ew", pady=4, padx=(8, 0)
            )
            if tipo is not None:
                ttk.Button(
                    self.advanced,
                    text="Procurar…",
                    command=lambda v=variavel, t=tipo, r=rotulo: self._pick(v, t, r),
                ).grid(row=indice, column=2, padx=(8, 0))

        ttk.Checkbutton(
            self.advanced,
            text="Ajuste de voz por UDP — só se a voz estiver bloqueada na sua rede",
            variable=self.voice,
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 0))

        extra = ttk.Frame(self.advanced, style="Card.TFrame")
        extra.grid(row=8, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Button(extra, text="Criar atalho", command=self._make_shortcut).pack(side="left")
        ttk.Button(extra, text="Remover atalho", command=self._drop_shortcut).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(extra, text="Abrir pasta de dados", command=self._open_data).pack(
            side="left", padx=(8, 0)
        )

    def _toggle_advanced(self) -> None:
        self.advanced_open = not self.advanced_open
        if self.advanced_open:
            self.advanced.grid(row=7, column=0, sticky="ew", pady=(10, 0))
            self.advanced_button.configure(text="Ajustes avançados ▴")
        else:
            self.advanced.grid_remove()
            self.advanced_button.configure(text="Ajustes avançados ▾")

    # -------------------------------------------------------------- estado --

    @property
    def channel_key(self) -> str:
        for key in CHANNELS:
            if CHANNEL_SPECS[key].label == self.channel.get():
                return key
        return "stable"

    def _config_path(self) -> Path:
        return run_module.config_path(detect_channel(self.channel_key))

    def _tor_path_value(self) -> Path | None:
        texto = self.tor_path.get().strip()
        return Path(texto) if texto else None

    def _on_exit_change(self) -> None:
        escolha = self.exit_choice.get()
        if escolha == MEU_PROXY:
            self.proxy_row.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        else:
            self.proxy_row.grid_remove()

        if escolha == AQUI:
            texto = "O Discord sai pela sua conexão normal. Nada é trocado."
        elif escolha == MEU_PROXY:
            texto = "Use isto se você tem um proxy próprio (uma VPS, por exemplo)."
        elif tor_module.is_available(self._tor_path_value()):
            texto = (
                "O Tor será ligado sozinho, sem abrir janela nenhuma.\n"
                "Aviso: pelo Tor o envio de imagens grandes costuma falhar — ele é lento."
            )
            if _country_of(escolha):
                texto += "\nSe não houver saída nesse país agora, escolha 'Automático'."
        else:
            texto = (
                "O Tor não foi encontrado. Instale o Tor Browser em torproject.org e\n"
                "deixe a pasta em Downloads — ou informe a pasta em Ajustes avançados."
            )
        self.exit_hint.configure(text=texto)

    def _load_current(self) -> None:
        path = self._config_path()
        try:
            config = load_or_default(path)
        except ConfigError as exc:
            self._write(f"Não consegui ler {path}: {exc}", tag="erro")
            config = Config()

        if config.use_tor:
            self.exit_choice.set(TOR_PREFIXO + tor_module.country_label(config.country))
        elif config.proxy.enabled:
            self.exit_choice.set(MEU_PROXY)
            self.proxy_text.set(config.proxy.url)
        else:
            self.exit_choice.set(AQUI)

        self.voice.set(config.voice)
        self.delay.set(str(config.delay_ms))
        self.packet.set(str(config.packet) if config.packet else "")
        self.discord_path.set(str(config.executable) if config.executable else "")
        self.tor_path.set(str(config.tor_path) if config.tor_path else "")
        self._on_exit_change()

        install = detect_channel(self.channel_key)
        if install is None:
            self._write(
                f"{CHANNEL_SPECS[self.channel_key].label} não foi encontrado neste "
                "computador. Se ele está instalado, informe o caminho em Ajustes avançados.",
                tag="erro",
            )
        else:
            self._write(f"{install.label} encontrado: {install.executable}", tag="bom")
        if tor_module.is_available():
            self._write("Tor encontrado — dá para sair por outro país.", tag="bom")
        else:
            self._write(
                "Tor não encontrado. Instale o Tor Browser (torproject.org) se quiser "
                "trocar de país sem ter um proxy próprio."
            )

    def _collect(self) -> Config:
        escolha = self.exit_choice.get()
        use_tor = escolha.startswith(TOR_PREFIXO)
        proxy = parse_proxy(self.proxy_text.get()) if escolha == MEU_PROXY else parse_proxy("")
        packet = self.packet.get().strip()
        discord = self.discord_path.get().strip()
        pasta_tor = self.tor_path.get().strip()
        return Config(
            proxy=proxy,
            use_tor=use_tor,
            country=_country_of(escolha) if use_tor else "",
            tor_path=Path(pasta_tor) if pasta_tor else None,
            voice=bool(self.voice.get()),
            delay_ms=_delay(self.delay.get()),
            packet=validate_packet(Path(packet)) if packet else None,
            executable=Path(discord) if discord else None,
            path=self._config_path(),
        )

    # -------------------------------------------------------------- ações ---

    def _save(self) -> Config | None:
        try:
            config = self._collect()
        except ConfigError as exc:
            self._write(f"Configuração inválida: {exc}", tag="erro")
            return None
        return replace(config, path=save(self._config_path(), config))

    def _pick(self, variable, tipo: str, rotulo: str) -> None:
        from tkinter import filedialog

        if tipo == "pasta":
            escolhido = filedialog.askdirectory(title=f"Escolher: {rotulo}")
        elif rotulo == "Pacote inicial":
            escolhido = filedialog.askopenfilename(
                title=f"Escolher: {rotulo}",
                filetypes=[("Arquivos .bin", "*.bin"), ("Todos", "*.*")],
            )
        else:
            escolhido = filedialog.askopenfilename(title=f"Escolher: {rotulo}")
        if escolhido:
            variable.set(escolhido)

    def _open_discord(self) -> None:
        if self.session_alive.is_set():
            self._write("O Discord já está aberto por aqui.", tag="erro")
            return
        config = self._save()
        if config is None:
            return
        channel = self.channel_key
        self.go.state(["disabled"])
        self.stop_button.state(["!disabled"])
        self.session_alive.set()
        self.status.set("Preparando…")

        def anuncio(resultado) -> None:
            if resultado.proxy_used:
                texto = (
                    "Discord aberto. Deixe ESTA JANELA ABERTA enquanto estiver usando — "
                    "a saída depende dela."
                )
            else:
                texto = "Discord aberto, saindo pela sua conexão normal."
            if resultado.note:
                texto += f"\nAviso: {resultado.note}"
            self.messages.put(("bom", texto))

        def work() -> None:
            try:
                run_module.launch(
                    channel,
                    explicit_config=config.path,
                    wait=True,
                    on_step=lambda texto: self.messages.put(("passo", texto)),
                    on_started=anuncio,
                    on_warning=lambda texto: self.messages.put(("aviso", texto)),
                )
                self.messages.put(("info", "O Discord foi fechado. A saída foi desligada."))
            except Exception as exc:  # noqa: BLE001
                self.messages.put(("erro", _explain(exc)))
            finally:
                self.messages.put(("sessao-fim", ""))

        self.session = threading.Thread(target=work, daemon=True)
        self.session.start()

    def _stop_session(self) -> None:
        from tkinter import messagebox

        if not messagebox.askokcancel(
            "Encerrar a sessão?",
            "O Discord vai ser fechado e a saída desligada.\n\n"
            "Depois é só abrir o Discord normalmente — ele volta a usar a sua "
            "conexão de sempre.",
        ):
            return

        def work() -> str:
            resultado = run_module.stop_session()
            return (
                f"{resultado}.\nAbra o Discord normalmente para voltar ao uso comum."
            )

        self._background("Encerrando…", work)

    def _where(self) -> None:
        try:
            config = self._collect()
        except ConfigError as exc:
            self._write(f"Configuração inválida: {exc}", tag="erro")
            return

        def work() -> str:
            if config.use_tor:
                with tor_module.start(
                    country=config.country,
                    extra_path=config.tor_path,
                    on_progress=lambda p, e: self.messages.put(("passo", f"Tor {p}%")),
                ) as processo:
                    lugar = region_module.exit_address(parse_proxy(processo.proxy_url))
            else:
                lugar = region_module.exit_address(config.proxy)
            if not config.has_exit:
                return f"Sem trocar nada, o Discord te vê como:\n   {lugar}"
            return f"Com esta saída, o Discord te veria como:\n   {lugar}"

        self._background("Descobrindo de onde você parece vir…", work)

    def _call_region(self) -> None:
        channel = self.channel_key

        def work() -> str:
            endpoints = region_module.voice_endpoints(detect_channel(channel))
            if not endpoints:
                return (
                    "Nenhuma chamada em andamento. Entre numa chamada ou comece um "
                    "compartilhamento de tela e clique de novo."
                )
            linhas = ["Servidor da chamada (por ele passam a câmera e a tela):"]
            linhas += [f"   {item}" for item in endpoints]
            return "\n".join(linhas)

        self._background("Procurando a chamada…", work)

    def _save_report(self) -> None:
        caminho_config = self._config_path()

        def work() -> str:
            caminho = report_module.save(config_path=caminho_config)
            return (
                f"Relatório salvo em:\n   {caminho}\n"
                "Envie este arquivo para quem for te ajudar."
            )

        self._background("Montando o relatório…", work)

    def _open_data(self) -> None:
        caminho = voice_module.data_root()
        caminho.mkdir(parents=True, exist_ok=True)
        try:
            webbrowser.open(caminho.as_uri())
            self._write(f"Abri a pasta {caminho}")
        except Exception as exc:  # noqa: BLE001
            self._write(f"A pasta é {caminho} (não consegui abrir sozinho: {exc})")

    def _make_shortcut(self) -> None:
        install = detect_channel(self.channel_key)
        if install is None:
            self._write("Canal não encontrado.", tag="erro")
            return
        try:
            criado = shortcut_module.create(install)
        except (OSError, RuntimeError) as exc:
            self._write(f"Não consegui criar o atalho: {exc}", tag="erro")
            return
        self._write(f"Atalho criado em {criado.path}", tag="bom")

    def _drop_shortcut(self) -> None:
        install = detect_channel(self.channel_key)
        if install is None:
            self._write("Canal não encontrado.", tag="erro")
            return
        removido = shortcut_module.remove(install)
        try:
            removido = voice_module.remove_shim(install) or removido
        except voice_module.VoiceError as exc:
            self._write(f"Aviso: {exc}")
        self._write("Atalho removido." if removido else "Não havia atalho para remover.")

    # ------------------------------------------------------------ plumbing --

    def _background(self, message: str, work) -> None:
        if self.busy:
            self._write("Espere a operação anterior terminar.")
            return
        self.busy = True
        self.status.set(message)
        self._write(message, tag="passo")

        def runner() -> None:
            try:
                self.messages.put(("info", work()))
            except Exception as exc:  # noqa: BLE001
                self.messages.put(("erro", _explain(exc)))
            finally:
                self.messages.put(("fim", ""))

        threading.Thread(target=runner, daemon=True).start()

    def _drain(self) -> None:
        while True:
            try:
                kind, text = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "fim":
                self.busy = False
                self.status.set("Pronto.")
            elif kind == "sessao-fim":
                self.session_alive.clear()
                self.go.state(["!disabled"])
                self.stop_button.state(["disabled"])
                self.status.set("Pronto.")
            elif kind == "passo":
                self.status.set(text)
                self._write(text, tag="passo")
            elif kind == "aviso":
                self.status.set("A saída está lenta.")
                self._write("⚠ " + text, tag="atencao")
            else:
                self._write(text, tag={"erro": "erro", "bom": "bom"}.get(kind))
        self.root.after(120, self._drain)

    def _write(self, text: str, *, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_close(self) -> None:
        if self.session_alive.is_set():
            from tkinter import messagebox

            if not messagebox.askokcancel(
                "Fechar mesmo?",
                "O Discord está usando a saída desta janela.\n\n"
                "Se fechar agora, o Discord continua aberto mas volta a sair pela sua "
                "conexão normal.",
            ):
                return
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def _explain(exc: BaseException) -> str:
    """Transforma a falha em algo que dá para agir."""
    texto = str(exc).strip() or exc.__class__.__name__
    if isinstance(exc, tor_module.TorError):
        return f"Tor: {texto}"
    if isinstance(exc, run_module.LaunchError):
        return texto
    if isinstance(exc, bridge_module.ProxyError):
        return f"A saída não respondeu: {texto}"
    if isinstance(exc, ConfigError):
        return f"Configuração inválida: {texto}"
    if isinstance(exc, OSError):
        return f"Erro do sistema: {texto}"
    return f"{texto}\n\n{traceback.format_exc(limit=2)}"


def _delay(text: str) -> int:
    try:
        value = int((text or "50").strip())
    except ValueError:
        raise ConfigError("a pausa precisa ser um número de milissegundos") from None
    if not 0 <= value <= 1000:
        raise ConfigError("a pausa precisa estar entre 0 e 1000 milissegundos")
    return value


def run_ui() -> int:
    try:
        __import__("tkinter")
    except ImportError:
        print(
            "A janela precisa do Tk. No Fedora: sudo dnf install python3-tkinter · "
            "no Debian/Ubuntu: sudo apt install python3-tk · "
            "ou use os comandos de terminal (discord-proxy run)."
        )
        return 3
    return Window().run()
