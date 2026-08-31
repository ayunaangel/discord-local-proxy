#!/bin/sh
# Abre o Discord Proxy. Sem argumentos, abre a janela.
# Com argumentos, funciona como linha de comando: ./INICIAR-LINUX.sh relatorio
set -e
aqui=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# a pasta com ponto na frente é de pacotes antigos, mantida por compatibilidade
for candidato in "$aqui/DiscordProxy" "$aqui/programa/DiscordProxy" \
                 "$aqui/.discord-proxy/DiscordProxy"; do
    if [ -f "$candidato" ]; then
        [ -x "$candidato" ] || chmod +x "$candidato" 2>/dev/null || true
        exec "$candidato" "$@"
    fi
done

if [ -d "$aqui/discord_proxy" ]; then
    if ! command -v python3 > /dev/null 2>&1; then
        echo "O Python 3 não está instalado."
        echo "  Fedora:        sudo dnf install python3 python3-tkinter"
        echo "  Debian/Ubuntu: sudo apt install python3 python3-tk"
        exit 1
    fi
    cd "$aqui"
    exec python3 -m discord_proxy "$@"
fi

echo "Não encontrei o programa nesta pasta."
echo "Extraia o pacote inteiro antes de abrir, e não mova este arquivo para fora."
exit 1
