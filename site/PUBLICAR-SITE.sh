#!/usr/bin/env bash
# Publica o site na Vercel.
set -euo pipefail
cd "$(dirname "$0")"

azul()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
verde() { printf '\033[1;32m%s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33m%s\033[0m\n' "$*"; }

azul "Conferindo os links de download antes de publicar"
faltando=0
for arquivo in DiscordProxy-Windows-x64.zip DiscordProxy-Linux-x64.tar.gz; do
    url="https://github.com/ayunaangel/discord-local-proxy/releases/latest/download/$arquivo"
    codigo=$(curl -s -o /dev/null -w '%{http_code}' -L "$url" || echo 000)
    if [ "$codigo" = "200" ]; then
        verde "   ok   $arquivo"
    else
        aviso "   FALTA $arquivo (HTTP $codigo)"
        faltando=1
    fi
done

if [ "$faltando" = "1" ]; then
    echo
    aviso "Publique a release no GitHub antes (../PUBLICAR.sh), senão os botões"
    aviso "do site levam a uma página de erro."
    printf 'Publicar mesmo assim? [s/N] '
    read -r resposta
    [ "$resposta" = "s" ] || [ "$resposta" = "S" ] || exit 1
fi

azul "Publicando na Vercel"
if ! command -v vercel > /dev/null 2>&1; then
    aviso "O comando 'vercel' não está instalado. Instale com:"
    echo "     npm i -g vercel"
    echo "  (ou, sem instalar nada: npx vercel --prod)"
    exit 1
fi

vercel --prod
echo
verde "No painel da Vercel, confira se o projeto está apontado para esta pasta"
verde "e se o Framework Preset está em 'Other' (o site não tem build)."
