#!/usr/bin/env sh
set -eu

installer_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
easy_launcher="$installer_dir/INICIAR-LINUX.sh"

if [ ! -f "$easy_launcher" ]; then
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="Discord Local Proxy" \
            --text="INICIAR-LINUX.sh nao foi encontrado. Extraia todo o pacote antes de instalar."
    elif command -v kdialog >/dev/null 2>&1; then
        kdialog --title "Discord Local Proxy" --error \
            "INICIAR-LINUX.sh nao foi encontrado. Extraia todo o pacote antes de instalar."
    else
        printf '%s\n' \
            "INICIAR-LINUX.sh nao foi encontrado. Extraia todo o pacote antes de instalar." >&2
    fi
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
