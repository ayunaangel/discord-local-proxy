#!/usr/bin/env sh
set -eu

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

show_error() {
    launcher_message=$1
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="Discord Local Proxy" --text="$launcher_message"
    elif command -v kdialog >/dev/null 2>&1; then
        kdialog --title "Discord Local Proxy" --error "$launcher_message"
    else
        printf '%s\n' "$launcher_message" >&2
    fi
}

run_binary() {
    launcher_binary=$1
    shift
    if [ -f "$launcher_binary" ]; then
        if [ ! -x "$launcher_binary" ]; then
            chmod u+x "$launcher_binary" 2>/dev/null || true
        fi
        if [ -x "$launcher_binary" ]; then
            exec "$launcher_binary" "$@"
        fi
    fi
}

run_binary "$launcher_dir/DiscordLocalProxy" "$@"
run_binary "$launcher_dir/dist/DiscordLocalProxy" "$@"

if [ -f "$launcher_dir/discord_local_proxy/__main__.py" ] && command -v python3 >/dev/null 2>&1; then
    cd -- "$launcher_dir"
    exec python3 -m discord_local_proxy "$@"
fi

show_error "DiscordLocalProxy ou Python 3 nao foi encontrado nesta pasta. Extraia todo o pacote antes de iniciar."
exit 1
