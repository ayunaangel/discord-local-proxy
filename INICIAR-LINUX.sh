#!/bin/sh
# Abre o Discord Proxy. Sem argumentos, abre a janela de configuração.
# Com argumentos, funciona como a linha de comando: ./INICIAR-LINUX.sh run
set -e
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

for candidate in "$here/DiscordProxy" "$here/.discord-proxy/DiscordProxy"; do
    if [ -f "$candidate" ]; then
        [ -x "$candidate" ] || chmod +x "$candidate" 2>/dev/null || true
        exec "$candidate" "$@"
    fi
done

if [ -d "$here/discord_proxy" ]; then
    cd "$here"
    exec python3 -m discord_proxy "$@"
fi

echo "Não encontrei o programa. Extraia o pacote inteiro antes de abrir." >&2
exit 1
