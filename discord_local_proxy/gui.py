from __future__ import annotations

import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .config import AppConfig, ConfigError, ProxySettings, VoiceSettings, load_config
from .discovery import SPECS, default_config_path, discover_installations
from .diagnostics import LOGGER, log_hint, open_log_directory, record_exception
from .installer import InstallError, install, uninstall
from .proxy_bridge import probe_proxy


IVORY = "#F6F1EA"
INK = "#0B0B0C"
VIOLET = "#2A1E5C"
THISTLE = "#D8C5E7"
MAGENTA = "#D82D91"
SURFACE = "#FFFDFC"
MUTED = "#6D6870"
LINE = "#DED7CF"
DANGER = "#96364C"
DANGER_SURFACE = "#F9E7EA"
PROXY_LABELS = {"none": "Direto", "http": "HTTP", "socks5": "SOCKS5"}
PROXY_KINDS = {label: kind for kind, label in PROXY_LABELS.items()}


class DiscordProxyGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Discord Local Proxy")
        self.root.geometry("620x580")
        self.root.minsize(560, 540)
        self.root.configure(background=IVORY)
        self.root.option_add("*tearOff", False)
        self.body_font = self._pick_font(
            (
                "Segoe UI Variable",
                "Segoe UI",
                "Adwaita Sans",
                "Noto Sans",
                "Liberation Sans",
                "Arial",
            )
        )
        self.display_font = self._pick_font(
            (
                "Comfortaa",
                "Segoe UI Variable Display",
                "Segoe UI",
                "Adwaita Sans",
                "Noto Sans",
                "Liberation Sans",
            )
        )
        self._configure_theme()
        self.installations = discover_installations()
        self.channel_vars: dict[str, tk.BooleanVar] = {}
        self.proxy_kind = tk.StringVar(value="none")
        self.proxy_kind_display = tk.StringVar(value=PROXY_LABELS["none"])
        self.host = tk.StringVar()
        self.port = tk.StringVar()
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.password_env = tk.StringVar()
        self.voice_enabled = tk.BooleanVar(value=True)
        self.voice_delay = tk.StringVar(value="50")
        self.voice_packet_file = tk.StringVar()
        self.status_text = tk.StringVar(value="Pronto.")
        self._action_buttons: list[ttk.Button] = []
        self._build()
        self._load_existing_config()
        self._proxy_kind_changed()

    def _pick_font(self, candidates: tuple[str, ...]) -> str:
        available = {name.casefold(): name for name in tkfont.families(self.root)}
        for candidate in candidates:
            if candidate.casefold() in available:
                return available[candidate.casefold()]
        return tkfont.nametofont("TkDefaultFont").cget("family")

    def _configure_theme(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        tkfont.nametofont("TkDefaultFont").configure(family=self.body_font, size=10)
        tkfont.nametofont("TkTextFont").configure(family=self.body_font, size=10)

        style.configure(".", font=(self.body_font, 10), background=IVORY, foreground=INK)
        style.configure("TFrame", background=IVORY)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("Tint.TFrame", background=THISTLE)
        style.configure("Dark.TFrame", background=VIOLET)

        style.configure("TLabel", background=IVORY, foreground=INK)
        style.configure(
            "Eyebrow.TLabel",
            background=IVORY,
            foreground=VIOLET,
            font=(self.body_font, 8, "bold"),
        )
        style.configure(
            "Title.TLabel",
            background=IVORY,
            foreground=INK,
            font=(self.display_font, 21, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=IVORY,
            foreground=MUTED,
            font=(self.body_font, 9),
        )
        style.configure(
            "Section.TLabel",
            background=SURFACE,
            foreground=VIOLET,
            font=(self.body_font, 8, "bold"),
        )
        style.configure("Card.TLabel", background=SURFACE, foreground=INK)
        style.configure(
            "CardMuted.TLabel",
            background=SURFACE,
            foreground=MUTED,
            font=(self.body_font, 8),
        )
        style.configure(
            "Pill.TLabel",
            background=THISTLE,
            foreground=VIOLET,
            font=(self.body_font, 8, "bold"),
            padding=(9, 4),
        )
        style.configure("Tint.TLabel", background=THISTLE, foreground=VIOLET)
        style.configure(
            "TintTitle.TLabel",
            background=THISTLE,
            foreground=VIOLET,
            font=(self.body_font, 10, "bold"),
        )
        style.configure("Dark.TLabel", background=VIOLET, foreground=IVORY)
        style.configure(
            "DarkTitle.TLabel",
            background=VIOLET,
            foreground=IVORY,
            font=(self.body_font, 9, "bold"),
        )
        style.configure(
            "DarkMuted.TLabel",
            background=VIOLET,
            foreground=THISTLE,
            font=(self.body_font, 8),
        )
        style.configure(
            "Danger.TLabel",
            background=SURFACE,
            foreground=DANGER,
            font=(self.body_font, 9),
        )

        style.configure("TNotebook", background=IVORY, borderwidth=0, tabmargins=0)
        style.configure(
            "TNotebook.Tab",
            background=IVORY,
            foreground=MUTED,
            borderwidth=0,
            padding=(18, 8),
            font=(self.body_font, 9, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", VIOLET), ("active", THISTLE)],
            foreground=[("selected", IVORY), ("active", VIOLET)],
        )

        for field_style in ("TEntry", "TSpinbox", "TCombobox"):
            style.configure(
                field_style,
                foreground=INK,
                fieldbackground=SURFACE,
                background=SURFACE,
                bordercolor=LINE,
                lightcolor=LINE,
                darkcolor=LINE,
                arrowcolor=VIOLET,
                borderwidth=1,
                padding=7,
            )
            style.map(
                field_style,
                bordercolor=[("focus", VIOLET), ("!focus", LINE)],
                lightcolor=[("focus", VIOLET), ("!focus", LINE)],
                darkcolor=[("focus", VIOLET), ("!focus", LINE)],
                fieldbackground=[("disabled", "#ECE7E1"), ("readonly", SURFACE)],
                foreground=[("disabled", "#9A9490"), ("readonly", INK)],
            )

        style.configure(
            "Card.TCheckbutton",
            background=SURFACE,
            foreground=INK,
            font=(self.body_font, 9, "bold"),
            padding=(0, 2),
        )
        style.map(
            "Card.TCheckbutton",
            background=[("active", SURFACE)],
            foreground=[("disabled", MUTED)],
            indicatorcolor=[("selected", VIOLET), ("!selected", SURFACE)],
        )
        style.configure(
            "Tint.TCheckbutton",
            background=THISTLE,
            foreground=VIOLET,
            font=(self.body_font, 9, "bold"),
            padding=(0, 2),
        )
        style.map(
            "Tint.TCheckbutton",
            background=[("active", THISTLE)],
            indicatorcolor=[("selected", VIOLET), ("!selected", IVORY)],
        )

        style.configure(
            "Primary.TButton",
            background=VIOLET,
            foreground=IVORY,
            borderwidth=0,
            padding=(18, 9),
            font=(self.body_font, 9, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("disabled", "#9288A8"), ("pressed", INK), ("active", "#3B2A78")],
            foreground=[("disabled", "#E6E0EC"), ("!disabled", IVORY)],
        )
        style.configure(
            "Secondary.TButton",
            background=INK,
            foreground=IVORY,
            borderwidth=0,
            padding=(14, 9),
            font=(self.body_font, 9, "bold"),
        )
        style.map(
            "Secondary.TButton",
            background=[("disabled", "#8A8788"), ("pressed", VIOLET), ("active", "#2A292B")],
            foreground=[("disabled", "#E5E1DD"), ("!disabled", IVORY)],
        )
        style.configure(
            "Ghost.TButton",
            background=IVORY,
            foreground=VIOLET,
            bordercolor=LINE,
            lightcolor=LINE,
            darkcolor=LINE,
            borderwidth=1,
            padding=(13, 8),
            font=(self.body_font, 9, "bold"),
        )
        style.map(
            "Ghost.TButton",
            background=[("disabled", "#ECE7E1"), ("pressed", THISTLE), ("active", "#EDE3F2")],
            foreground=[("disabled", "#A19A9F"), ("!disabled", VIOLET)],
        )
        style.configure(
            "Danger.TButton",
            background=DANGER_SURFACE,
            foreground=DANGER,
            borderwidth=0,
            padding=(13, 9),
            font=(self.body_font, 9, "bold"),
        )
        style.map(
            "Danger.TButton",
            background=[("disabled", "#EEE7E8"), ("pressed", "#E8CBD2"), ("active", "#F3D9DF")],
            foreground=[("disabled", "#A98E94"), ("!disabled", DANGER)],
        )
        style.configure("TSeparator", background=LINE)

    def _build(self) -> None:
        container = ttk.Frame(self.root, padding=(22, 17, 22, 15))
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 13))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="LOCAL  /  PRIVADO  /  REVERSÍVEL", style="Eyebrow.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, text="Discord Local Proxy", style="Title.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 0)
        )
        ttk.Label(
            header,
            text="Controle a conexão do Discord sem alterar o restante do sistema.",
            style="Subtitle.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(2, 0))
        ttk.Label(header, text=f"v{__version__}", style="Pill.TLabel").grid(
            row=0, column=1, rowspan=2, sticky="ne"
        )

        notebook = ttk.Notebook(container)
        notebook.grid(row=1, column=0, sticky="nsew")

        connection = ttk.Frame(notebook, style="Card.TFrame", padding=(17, 14, 17, 13))
        connection.columnconfigure(0, weight=1)
        notebook.add(connection, text="Conexão")

        section_header = ttk.Frame(connection, style="Card.TFrame")
        section_header.grid(row=0, column=0, sticky="ew")
        section_header.columnconfigure(0, weight=1)
        ttk.Label(section_header, text="APLICATIVOS", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        detected_text = f"{len(self.installations)} detectado" + (
            "" if len(self.installations) == 1 else "s"
        )
        ttk.Label(section_header, text=detected_text, style="Pill.TLabel").grid(
            row=0, column=1, sticky="e"
        )

        row = 1
        if not self.installations:
            ttk.Label(
                connection,
                text="Nenhuma instalação de Discord Stable, PTB ou Canary foi encontrada.",
                style="Danger.TLabel",
            ).grid(row=row, column=0, sticky="w", pady=(9, 2))
            row += 1
        for item in self.installations:
            variable = tk.BooleanVar(value=True)
            self.channel_vars[item.channel] = variable
            note = f"{item.display_path}  [{item.source}]"
            if not item.supports_udp_shim:
                note += " — proxy funciona, ajuste UDP não suportado pelo sandbox"
            channel = ttk.Frame(connection, style="Card.TFrame")
            channel.grid(row=row, column=0, sticky="ew", pady=(7, 0))
            channel.columnconfigure(0, weight=1)
            ttk.Checkbutton(
                channel,
                text=item.label,
                variable=variable,
                style="Card.TCheckbutton",
            ).grid(row=0, column=0, sticky="w")
            ttk.Label(
                channel,
                text=note,
                style="CardMuted.TLabel",
                wraplength=515,
            ).grid(row=1, column=0, sticky="w", padx=(22, 0), pady=(0, 1))
            row += 1

        ttk.Separator(connection).grid(row=row, column=0, sticky="ew", pady=(11, 10))
        row += 1
        ttk.Label(connection, text="PROXY", style="Section.TLabel").grid(
            row=row, column=0, sticky="w"
        )
        row += 1
        ttk.Label(
            connection,
            text="Deixe em Direto para usar somente a compatibilidade de voz.",
            style="CardMuted.TLabel",
        ).grid(row=row, column=0, sticky="w", pady=(2, 8))
        row += 1

        proxy = ttk.Frame(connection, style="Card.TFrame")
        proxy.grid(row=row, column=0, sticky="ew")
        proxy.columnconfigure(1, weight=1)
        proxy.columnconfigure(3, weight=1)
        ttk.Label(proxy, text="Tipo", style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 7)
        )
        kind = ttk.Combobox(
            proxy,
            textvariable=self.proxy_kind_display,
            values=tuple(PROXY_LABELS.values()),
            state="readonly",
            width=11,
        )
        kind.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        kind.bind("<<ComboboxSelected>>", lambda _: self._proxy_kind_changed())
        ttk.Label(proxy, text="Host", style="CardMuted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 7)
        )
        self.host_entry = ttk.Entry(proxy, textvariable=self.host)
        self.host_entry.grid(row=0, column=3, sticky="ew", padx=(0, 12))
        ttk.Label(proxy, text="Porta", style="CardMuted.TLabel").grid(
            row=0, column=4, sticky="w", padx=(0, 7)
        )
        self.port_entry = ttk.Entry(proxy, textvariable=self.port, width=8)
        self.port_entry.grid(row=0, column=5, sticky="ew")

        auth = ttk.Frame(connection, style="Card.TFrame")
        auth.grid(row=row + 1, column=0, sticky="ew", pady=(9, 0))
        for column in range(3):
            auth.columnconfigure(column, weight=1)
        ttk.Label(auth, text="Usuário", style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(auth, text="Senha", style="CardMuted.TLabel").grid(
            row=0, column=1, sticky="w", padx=(10, 0)
        )
        ttk.Label(auth, text="Variável de senha", style="CardMuted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )
        self.user_entry = ttk.Entry(auth, textvariable=self.username)
        self.user_entry.grid(row=1, column=0, sticky="ew")
        self.password_entry = ttk.Entry(auth, textvariable=self.password, show="•")
        self.password_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0))
        self.env_entry = ttk.Entry(auth, textvariable=self.password_env)
        self.env_entry.grid(row=1, column=2, sticky="ew", padx=(10, 0))
        ttk.Label(
            connection,
            text="Prefira uma variável de ambiente para não salvar a senha como texto no INI.",
            style="CardMuted.TLabel",
        ).grid(row=row + 2, column=0, sticky="w", pady=(7, 0))

        voice = ttk.Frame(notebook, style="Card.TFrame", padding=(17, 14, 17, 13))
        voice.columnconfigure(0, weight=1)
        notebook.add(voice, text="Voz e tela")

        ttk.Label(voice, text="VOZ E COMPARTILHAMENTO", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            voice,
            text="Ajuste experimental do primeiro pacote UDP de mídia do Discord.",
            style="CardMuted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 12))

        voice_toggle = ttk.Frame(voice, style="Tint.TFrame", padding=(14, 12))
        voice_toggle.grid(row=2, column=0, sticky="ew")
        voice_toggle.columnconfigure(0, weight=1)
        ttk.Checkbutton(
            voice_toggle,
            text="Ativar compatibilidade de voz",
            variable=self.voice_enabled,
            style="Tint.TCheckbutton",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(voice_toggle, text="Atraso", style="Tint.TLabel").grid(
            row=0, column=1, padx=(16, 6)
        )
        ttk.Spinbox(
            voice_toggle,
            from_=0,
            to=1000,
            textvariable=self.voice_delay,
            width=6,
        ).grid(row=0, column=2)
        ttk.Label(voice_toggle, text="ms", style="Tint.TLabel").grid(
            row=0, column=3, padx=(5, 0)
        )

        packet = ttk.Frame(voice, style="Card.TFrame")
        packet.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        packet.columnconfigure(0, weight=1)
        ttk.Label(packet, text="Pacote inicial opcional", style="Card.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            packet,
            text="Arquivo .bin enviado antes dos pacotes 00/01.",
            style="CardMuted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(1, 7))
        self.packet_entry = ttk.Entry(packet, textvariable=self.voice_packet_file)
        self.packet_entry.grid(row=2, column=0, sticky="ew", padx=(0, 9))
        ttk.Button(
            packet,
            text="Selecionar…",
            command=self._choose_packet_file,
            style="Ghost.TButton",
        ).grid(row=2, column=1, sticky="e")

        notice = ttk.Frame(voice, style="Dark.TFrame", padding=(14, 12))
        notice.grid(row=4, column=0, sticky="ew", pady=(17, 0))
        notice.columnconfigure(0, weight=1)
        ttk.Label(notice, text="IMPORTANTE", style="DarkTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            notice,
            text=(
                "Pode ajudar em filtros por inspeção, mas não cria um túnel. "
                "A voz continua em UDP direto e falha se a rede bloquear todo o UDP."
            ),
            style="DarkMuted.TLabel",
            wraplength=490,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        actions = ttk.Frame(container)
        actions.grid(row=2, column=0, sticky="ew", pady=(13, 0))
        test = ttk.Button(
            actions,
            text="Testar proxy",
            command=self._test_proxy,
            style="Secondary.TButton",
        )
        test.pack(side="left")
        logs_button = ttk.Button(
            actions,
            text="Abrir logs",
            command=self._open_logs,
            style="Ghost.TButton",
        )
        logs_button.pack(side="left", padx=(8, 0))
        uninstall_button = ttk.Button(
            actions,
            text="Desinstalar",
            command=self._uninstall,
            style="Danger.TButton",
        )
        uninstall_button.pack(side="left", padx=(8, 0))
        install_button = ttk.Button(
            actions,
            text="Instalar / atualizar",
            command=self._install,
            style="Primary.TButton",
        )
        install_button.pack(side="right")
        self._action_buttons.extend((test, install_button, uninstall_button))

        status = ttk.Frame(container, style="Tint.TFrame", padding=(12, 8))
        status.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        status.columnconfigure(1, weight=1)
        ttk.Label(
            status,
            text="●",
            style="Tint.TLabel",
            foreground=MAGENTA,
            font=(self.body_font, 8, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 7))
        ttk.Label(
            status,
            textvariable=self.status_text,
            style="Tint.TLabel",
            wraplength=510,
        ).grid(row=0, column=1, sticky="w")

    def _proxy_kind_changed(self) -> None:
        selected = PROXY_KINDS.get(self.proxy_kind_display.get())
        if selected is not None:
            self.proxy_kind.set(selected)
        state = "normal" if self.proxy_kind.get() != "none" else "disabled"
        for widget in (
            self.host_entry,
            self.port_entry,
            self.user_entry,
            self.password_entry,
            self.env_entry,
        ):
            widget.configure(state=state)

    def _selected_channels(self) -> list[str]:
        return [channel for channel, variable in self.channel_vars.items() if variable.get()]

    def _choose_packet_file(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Selecionar pacote UDP inicial",
            filetypes=(("Arquivo binário", "*.bin"), ("Todos os arquivos", "*")),
        )
        if selected:
            self.voice_packet_file.set(selected)

    def _open_logs(self) -> None:
        try:
            path = open_log_directory()
        except OSError as exc:
            record_exception("não foi possível abrir a pasta de logs", exc)
            messagebox.showerror(
                "Logs",
                f"Não foi possível abrir a pasta.\n\n{log_hint()}",
                parent=self.root,
            )
            return
        self.status_text.set(f"Pasta de logs: {path}")

    def _form_config(self) -> AppConfig:
        kind = self.proxy_kind.get()
        packet_text = self.voice_packet_file.get().strip()
        return AppConfig(
            proxy=ProxySettings(
                kind=kind,
                host=self.host.get().strip() if kind != "none" else "",
                port=int(self.port.get().strip() or "0") if kind != "none" else 0,
                username=self.username.get() if kind != "none" else "",
                password=self.password.get() if kind != "none" else "",
                password_env=self.password_env.get().strip() if kind != "none" else "",
            ),
            voice=VoiceSettings(
                enabled=self.voice_enabled.get(),
                delay_ms=int(self.voice_delay.get().strip()),
                packet_file=Path(packet_text) if packet_text else None,
            ),
        )

    def _load_existing_config(self) -> None:
        for installation in self.installations:
            path = default_config_path(installation)
            if not path.is_file():
                continue
            try:
                config = load_config(path)
            except ConfigError as exc:
                record_exception("configuração existente inválida", exc, warning=True)
                self.status_text.set(f"Configuração existente inválida em {path}: {exc}")
                return
            self.proxy_kind.set(config.proxy.kind)
            self.proxy_kind_display.set(PROXY_LABELS[config.proxy.kind])
            self.host.set(config.proxy.host)
            self.port.set(str(config.proxy.port) if config.proxy.enabled else "")
            self.username.set(config.proxy.username)
            self.password.set(config.proxy.password)
            self.password_env.set(config.proxy.password_env)
            self.voice_enabled.set(config.voice.enabled)
            self.voice_delay.set(str(config.voice.delay_ms))
            self.voice_packet_file.set(
                str(config.voice.packet_file) if config.voice.packet_file else ""
            )
            self.status_text.set(f"Configuração carregada de {path}")
            return

    def _test_proxy(self) -> None:
        try:
            config = self._form_config()
        except (ConfigError, ValueError) as exc:
            messagebox.showerror("Configuração inválida", str(exc), parent=self.root)
            return
        self._run_background(
            "Testando o proxy…",
            lambda: probe_proxy(config.proxy),
            lambda result: self._finish_probe(result.ok, result.message),
        )

    def _finish_probe(self, ok: bool, message: str) -> None:
        self.status_text.set(message)
        if ok:
            LOGGER.info("teste de proxy concluído com sucesso")
            messagebox.showinfo("Teste concluído", message, parent=self.root)
        else:
            LOGGER.warning("teste de proxy não concluído: %s", message)
            messagebox.showerror("Falha no proxy", message, parent=self.root)

    def _install(self) -> None:
        try:
            channels = self._selected_channels()
            config = self._form_config()
            if config.voice.enabled:
                unsupported = [
                    item.label
                    for item in self.installations
                    if item.channel in channels and not item.supports_udp_shim
                ]
                if unsupported:
                    raise InstallError(
                        "o ajuste UDP não pode ser instalado em "
                        + ", ".join(unsupported)
                        + "; use o pacote nativo do Discord ou desative a compatibilidade de voz"
                    )
        except (ConfigError, InstallError, ValueError) as exc:
            messagebox.showerror("Configuração inválida", str(exc), parent=self.root)
            return

        def action():
            return install(channels, config, installations=self.installations)

        def done(result) -> None:
            labels = ", ".join(SPECS[item.channel].label for item in result.channels)
            message = f"Instalado para: {labels}. Abra o novo atalho com “(Proxy)”."
            LOGGER.info("instalação concluída | canais=%s", labels)
            self.status_text.set(message)
            messagebox.showinfo("Instalação concluída", message, parent=self.root)

        self._run_background("Instalando…", action, done)

    def _uninstall(self) -> None:
        answer = messagebox.askyesnocancel(
            "Desinstalar",
            "Também remover os arquivos INI?\n\nSim: remove atalhos, componentes e configurações.\nNão: preserva os INIs.",
            parent=self.root,
        )
        if answer is None:
            return

        def done(result) -> None:
            message = f"Desinstalação concluída: {len(result.removed)} item(ns) removido(s)."
            if result.preserved_configs:
                message += f" {len(result.preserved_configs)} configuração(ões) preservada(s)."
            if result.warnings:
                message += " Avisos: " + " | ".join(result.warnings)
            LOGGER.info(
                "desinstalação concluída | removidos=%s | configurações_preservadas=%s | avisos=%s",
                len(result.removed),
                len(result.preserved_configs),
                len(result.warnings),
            )
            self.status_text.set(message)
            messagebox.showinfo("Desinstalação", message, parent=self.root)

        self._run_background("Desinstalando…", lambda: uninstall(purge_config=answer), done)

    def _run_background(self, pending: str, action, done) -> None:
        self.status_text.set(pending)
        LOGGER.info("operação iniciada | %s", pending)
        for button in self._action_buttons:
            button.configure(state="disabled")

        def worker() -> None:
            try:
                result = action()
            except BaseException as exc:
                record_exception("operação da interface não concluída", exc)
                self.root.after(0, lambda error=exc: self._background_error(error))
            else:
                self.root.after(0, lambda value=result: self._background_done(value, done))

        threading.Thread(target=worker, name="discord-proxy-ui-action", daemon=True).start()

    def _background_done(self, result, done) -> None:
        for button in self._action_buttons:
            button.configure(state="normal")
        done(result)

    def _background_error(self, error: BaseException) -> None:
        for button in self._action_buttons:
            button.configure(state="normal")
        self.status_text.set(str(error))
        messagebox.showerror(
            "Operação não concluída",
            f"{error}\n\n{log_hint()}",
            parent=self.root,
        )


def run_gui() -> int:
    root = tk.Tk()

    def report_callback_exception(error_type, error, traceback) -> None:
        LOGGER.error(
            "falha inesperada na interface: %s",
            error,
            exc_info=(error_type, error, traceback),
        )
        messagebox.showerror(
            "Erro inesperado",
            f"{error}\n\n{log_hint()}",
            parent=root,
        )

    root.report_callback_exception = report_callback_exception
    try:
        ttk.Style(root).theme_use("clam")
    except tk.TclError:
        pass
    DiscordProxyGUI(root)
    root.mainloop()
    return 0


def font_diagnostics() -> dict[str, object]:
    root = tk.Tk()
    root.withdraw()
    try:
        app = DiscordProxyGUI(root)
        style = ttk.Style(root)
        title_spec = style.lookup("Title.TLabel", "font")
        body_spec = style.lookup("Card.TLabel", "font") or "TkDefaultFont"
        title_font = tkfont.Font(root=root, font=title_spec)
        body_font = tkfont.Font(root=root, font=body_spec)
        available_families = set(tkfont.families(root))
        title_actual = title_font.actual()
        body_actual = body_font.actual()
        title_proportional = title_font.measure("iiiiiiii") != title_font.measure("WWWWWWWW")
        body_proportional = body_font.measure("iiiiiiii") != body_font.measure("WWWWWWWW")
        healthy = (
            abs(int(title_actual["size"])) >= 18
            and title_proportional
            and body_proportional
            and len(available_families) > 4
        )
        return {
            "healthy": healthy,
            "selected_body": app.body_font,
            "selected_display": app.display_font,
            "title": title_actual,
            "body": body_actual,
            "available_family_count": len(available_families),
            "title_proportional": title_proportional,
            "body_proportional": body_proportional,
        }
    finally:
        root.destroy()
