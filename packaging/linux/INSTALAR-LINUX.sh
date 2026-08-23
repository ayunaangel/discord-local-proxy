#!/usr/bin/env sh
set -eu

visible_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
easy_launcher="$visible_root/INICIAR-LINUX.sh"

show_error() {
    installer_message=$1
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="Discord Local Proxy" --text="$installer_message"
    elif command -v kdialog >/dev/null 2>&1; then
        kdialog --title "Discord Local Proxy" --error "$installer_message"
    else
        printf '%s\n' "$installer_message" >&2
    fi
}

if [ ! -f "$easy_launcher" ]; then
    show_error "INICIAR-LINUX.sh nao foi encontrado ao lado deste instalador."
    exit 1
fi

if [ ! -x "$easy_launcher" ]; then
    chmod u+x "$easy_launcher" 2>/dev/null || true
fi

if [ "$#" -eq 0 ]; then
    set -- gui
fi

if [ -x "$easy_launcher" ]; then
    exec "$easy_launcher" "$@"
fi
exec sh "$easy_launcher" "$@"
