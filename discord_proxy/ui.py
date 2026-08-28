"""Janela de configuração — uma tela só, feita com o Tk que já vem no Python."""

from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import replace
from pathlib import Path

from . import bridge as bridge_module
from . import region as region_module
from . import run as run_module
from . import shortcut as shortcut_module
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


class Window:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()
        self.busy = False

        self.root = tk.Tk()
        self.root.title("Discord Proxy")
        self.root.configure(bg=IVORY)
        self.root.minsize(560, 460)

        self.channel = tk.StringVar(value="stable")
        self.proxy_text = tk.StringVar()
        self.voice = tk.BooleanVar(value=True)
        self.delay = tk.StringVar(value="50")
        self.packet = tk.StringVar()
        self.status = tk.StringVar(value="Pronto.")

        self._build()
        self._load_current()
        self.root.after(120, self._drain)

    # ------------------------------------------------------------------ UI --

    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover - depende do Tk instalado
            pass
        style.configure("TFrame", background=IVORY)
        style.configure("Card.TFrame", background=SURFACE, relief="flat")
        style.configure("TLabel", background=IVORY, foreground=INK)
        style.configure("Card.TLabel", background=SURFACE, foreground=INK)
        style.configure("Muted.TLabel", background=SURFACE, foreground=MUTED)
        style.configure("Title.TLabel", background=IVORY, foreground=VIOLET, font=("", 16, "bold"))
        style.configure("TButton", padding=(12, 7))
        style.configure("Go.TButton", padding=(14, 9), foreground="#FFFFFF", background=MAGENTA)
        style.map("Go.TButton", background=[("active", VIOLET), ("disabled", LINE)])
        style.configure("TCheckbutton", background=SURFACE, foreground=INK)

        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text="Discord Proxy", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            outer,
            text=(
                "Faz o Discord sair por outro país — é isso que muda a região do\n"
                "servidor de voz, por onde passam a câmera e o compartilhamento de tela."
            ),
            style="TLabel",
            foreground=MUTED,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, 14))

        card = ttk.Frame(outer, style="Card.TFrame", padding=16)
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="Canal", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=6)
        channels = ttk.Combobox(
            card,
            textvariable=self.channel,
            values=[CHANNEL_SPECS[key].label for key in CHANNELS],
            state="readonly",
            width=18,
        )
        channels.grid(row=0, column=1, sticky="w", pady=6)
        channels.set(CHANNEL_SPECS["stable"].label)
        channels.bind("<<ComboboxSelected>>", lambda _event: self._on_channel_change())
        self.channels = channels

        ttk.Label(card, text="Proxy", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=6)
        entry = ttk.Entry(card, textvariable=self.proxy_text)
        entry.grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Label(
            card,
            text="socks5://127.0.0.1:9150 (Tor)  ·  vazio = sai daqui mesmo",
            style="Muted.TLabel",
        ).grid(row=2, column=1, sticky="w")

        ttk.Checkbutton(
            card,
            text="Ajuste de voz por UDP (só se a voz estiver bloqueada na sua rede)",
            variable=self.voice,
        ).grid(row=3, column=1, sticky="w", pady=(12, 4))

        ttk.Label(card, text="Pausa (ms)", style="Card.TLabel").grid(
            row=4, column=0, sticky="w", pady=6
        )
        ttk.Entry(card, textvariable=self.delay, width=8).grid(row=4, column=1, sticky="w", pady=6)

        ttk.Label(card, text="Pacote inicial", style="Card.TLabel").grid(
            row=5, column=0, sticky="w", pady=6
        )
        packet_row = ttk.Frame(card, style="Card.TFrame")
        packet_row.grid(row=5, column=1, sticky="ew", pady=6)
        packet_row.columnconfigure(0, weight=1)
        ttk.Entry(packet_row, textvariable=self.packet).grid(row=0, column=0, sticky="ew")
        ttk.Button(packet_row, text="Selecionar…", command=self._pick_packet).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(packet_row, text="Limpar", command=lambda: self.packet.set("")).grid(
            row=0, column=2, padx=(6, 0)
        )

        buttons = ttk.Frame(outer)
        buttons.grid(row=3, column=0, sticky="ew", pady=(16, 8))
        self.go = ttk.Button(
            buttons, text="Abrir Discord", style="Go.TButton", command=self._open_discord
        )
        self.go.pack(side="left")
        ttk.Button(buttons, text="Salvar", command=self._save).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Testar proxy", command=self._test).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Onde estou saindo", command=self._where).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(buttons, text="Região da call", command=self._call_region).pack(
            side="left", padx=(8, 0)
        )

        extra = ttk.Frame(outer)
        extra.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(extra, text="Criar atalho", command=self._make_shortcut).pack(side="left")
        ttk.Button(extra, text="Remover atalho", command=self._drop_shortcut).pack(
            side="left", padx=(8, 0)
        )

        self.log = self.tk.Text(
            outer,
            height=9,
            wrap="word",
            bg=SURFACE,
            fg=INK,
            relief="flat",
            highlightthickness=1,
            highlightbackground=LINE,
            padx=10,
            pady=8,
        )
        self.log.grid(row=4, column=0, sticky="nsew")
        self.log.configure(state="disabled")
        outer.rowconfigure(4, weight=1)

        ttk.Label(outer, textvariable=self.status, style="TLabel", foreground=MUTED).grid(
            row=5, column=0, sticky="w", pady=(8, 0)
        )

    # -------------------------------------------------------------- estado --

    @property
    def channel_key(self) -> str:
        label = self.channels.get()
        for key in CHANNELS:
            if CHANNEL_SPECS[key].label == label:
                return key
        return "stable"

    def _config_path(self) -> Path:
        install = detect_channel(self.channel_key)
        return run_module.config_path(install)

    def _load_current(self) -> None:
        path = self._config_path()
        try:
            config = load_or_default(path)
        except ConfigError as exc:
            self._write(f"Não consegui ler {path}: {exc}")
            return
        self.proxy_text.set(config.proxy.url)
        self.voice.set(config.voice)
        self.delay.set(str(config.delay_ms))
        self.packet.set(str(config.packet) if config.packet else "")
        install = detect_channel(self.channel_key)
        if install is None:
            self._write(f"{CHANNEL_SPECS[self.channel_key].label} não foi encontrado neste sistema.")
        else:
            note = "" if install.supports_voice else f" — voz indisponível: {install.voice_reason}"
            self._write(f"{install.label} encontrado ({install.kind}){note}")
        self._write(f"Configuração: {path}")

    def _collect(self) -> Config:
        packet_text = self.packet.get().strip()
        return Config(
            proxy=parse_proxy(self.proxy_text.get()),
            voice=bool(self.voice.get()),
            delay_ms=_delay(self.delay.get()),
            packet=validate_packet(Path(packet_text)) if packet_text else None,
            path=self._config_path(),
        )

    def _on_channel_change(self) -> None:
        self._load_current()

    # -------------------------------------------------------------- ações ---

    def _save(self) -> Config | None:
        try:
            config = self._collect()
        except ConfigError as exc:
            self._write(f"Configuração inválida: {exc}", error=True)
            return None
        path = save(self._config_path(), config)
        self._write(f"Salvo em {path}")
        return replace(config, path=path)

    def _pick_packet(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askopenfilename(
            title="Escolher pacote inicial",
            filetypes=[("Arquivos binários", "*.bin"), ("Todos os arquivos", "*.*")],
        )
        if chosen:
            self.packet.set(chosen)

    def _test(self) -> None:
        try:
            proxy = parse_proxy(self.proxy_text.get())
        except ConfigError as exc:
            self._write(f"Proxy inválido: {exc}", error=True)
            return
        self._background(
            "Testando o proxy…",
            lambda: bridge_module.test_proxy(proxy).message,
        )

    def _where(self) -> None:
        """Mostra o IP e o país que o Discord vai enxergar."""
        try:
            proxy = parse_proxy(self.proxy_text.get())
        except ConfigError as exc:
            self._write(f"Proxy inválido: {exc}", error=True)
            return

        def work() -> str:
            place = region_module.exit_address(proxy)
            if not proxy.enabled:
                return f"Sem proxy, o Discord te vê como: {place}"
            return f"Com este proxy, o Discord te vê como: {place}"

        self._background(
            f"Perguntando ao {region_module.LOOKUP_HOST} de onde você parece vir…", work
        )

    def _call_region(self) -> None:
        """Mostra para qual servidor a chamada de agora está indo."""
        channel = self.channel_key

        def work() -> str:
            install = detect_channel(channel)
            endpoints = region_module.voice_endpoints(install)
            if not endpoints:
                return (
                    "Nenhuma chamada de voz ativa. Entre numa call ou comece um "
                    "compartilhamento de tela e clique de novo."
                )
            lines = ["Servidor em uso (por ele passam a câmera e a tela):"]
            lines += [f"   {endpoint}" for endpoint in endpoints]
            return "\n".join(lines)

        self._background("Procurando a chamada em andamento…", work)

    def _open_discord(self) -> None:
        config = self._save()
        if config is None:
            return
        channel = self.channel_key

        def work() -> str:
            result = run_module.launch(channel, explicit_config=config.path, wait=False)
            proxy = "com proxy" if result.proxy_used else "modo direto"
            voice = "com ajuste de voz" if result.voice_used else "sem ajuste de voz"
            if result.note:
                voice += f" ({result.note})"
            if result.proxy_used:
                return (
                    f"Discord aberto ({proxy}, {voice}), pid {result.pid}. "
                    "Mantenha esta janela aberta enquanto usar o proxy."
                )
            return f"Discord aberto ({proxy}, {voice}), pid {result.pid}."

        self._background("Abrindo o Discord…", work)

    def _make_shortcut(self) -> None:
        install = detect_channel(self.channel_key)
        if install is None:
            self._write("Canal não encontrado.", error=True)
            return
        try:
            created = shortcut_module.create(install)
        except (OSError, RuntimeError) as exc:
            self._write(f"Não consegui criar o atalho: {exc}", error=True)
            return
        self._write(f"Atalho criado em {created.path}")

    def _drop_shortcut(self) -> None:
        install = detect_channel(self.channel_key)
        if install is None:
            self._write("Canal não encontrado.", error=True)
            return
        removed = shortcut_module.remove(install)
        try:
            removed = voice_module.remove_shim(install) or removed
        except voice_module.VoiceError as exc:
            self._write(f"Aviso: {exc}")
        self._write("Atalho removido." if removed else "Não havia atalho para remover.")

    # ------------------------------------------------------------ plumbing --

    def _background(self, message: str, work) -> None:
        if self.busy:
            self._write("Aguarde a operação anterior terminar.")
            return
        self.busy = True
        self.go.state(["disabled"])
        self.status.set(message)
        self._write(message)

        def runner() -> None:
            try:
                self.messages.put(("info", work()))
            except Exception as exc:  # noqa: BLE001 - a janela mostra qualquer falha
                detail = str(exc) or traceback.format_exc(limit=1)
                self.messages.put(("error", detail))
            finally:
                self.messages.put(("done", ""))

        threading.Thread(target=runner, daemon=True).start()

    def _drain(self) -> None:
        while True:
            try:
                kind, text = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "done":
                self.busy = False
                self.go.state(["!disabled"])
                self.status.set("Pronto.")
            else:
                self._write(text, error=kind == "error")
        self.root.after(120, self._drain)

    def _write(self, text: str, *, error: bool = False) -> None:
        self.log.configure(state="normal")
        if error:
            self.log.tag_configure("error", foreground=DANGER)
            self.log.insert("end", text + "\n", "error")
        else:
            self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def run(self) -> int:
        self.root.mainloop()
        return 0


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
            "ou use os comandos de terminal (discord-proxy run).",
        )
        return 3
    return Window().run()
