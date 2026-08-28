#!/usr/bin/env bash
# Publica a reescrita no GitHub. Roda os passos um a um, pedindo confirmação
# no único que é irreversível.
set -euo pipefail

REPO="https://github.com/ayunaangel/discord-local-proxy"
BRANCH="reescrita"
TAG="v1.0.0"

azul()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
verde() { printf '\033[1;32m%s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33m%s\033[0m\n' "$*"; }

cd "$(dirname "$0")"

ajuda_credencial() {
    aviso "O GitHub recusou a autenticação."
    echo
    echo "Escolha um caminho e rode de novo:"
    echo
    echo "  a) GitHub CLI (mais fácil)"
    echo "       sudo dnf install gh   # ou: sudo apt install gh"
    echo "       gh auth login"
    echo
    echo "  b) Chave SSH"
    echo "       ssh-keygen -t ed25519 -C 'seu-email'"
    echo "       cat ~/.ssh/id_ed25519.pub    # cole em github.com/settings/keys"
    echo "       git remote set-url origin git@github.com:ayunaangel/discord-local-proxy.git"
    echo
    echo "  c) Token pessoal"
    echo "       crie em github.com/settings/tokens (escopo: repo)"
    echo "       git config --global credential.helper store"
    echo "       # o primeiro push vai pedir usuário e o token como senha"
    exit 1
}

# Leitura em repositório público funciona sem credencial nenhuma, então o teste
# precisa ser de escrita: --dry-run autentica sem enviar nada.
azul "1. Conferindo se dá para escrever no repositório"
git push --dry-run origin "$BRANCH" > /dev/null 2>&1 || ajuda_credencial
verde "   ok, tenho permissão de escrita"

azul "2. Enviando a branch '$BRANCH' (não toca na main)"
git push -u origin "$BRANCH"
verde "   pronto: $REPO/tree/$BRANCH"

azul "3. Enviando a tag '$TAG'"
echo "   Isso dispara o GitHub Actions, que compila Windows e Linux e cria a"
echo "   release com os dois pacotes. Leva uns 5 minutos."
git push origin "$TAG"
verde "   pronto: $REPO/actions"

echo
azul "4. E a branch principal?"
echo "   A 'main' continua com o projeto antigo, intacta. Quando quiser que a"
echo "   reescrita vire a versão oficial, há duas formas:"
echo
echo "   Preservando o histórico antigo (recomendado):"
echo "     abra $REPO/compare/main...$BRANCH e crie um pull request"
echo
echo "   Ou trocando de vez (apaga o histórico antigo da main):"
echo "     git push --force origin $BRANCH:main"
echo
aviso "   Não faço isso por você: é irreversível."
echo
verde "Depois que a release aparecer em $REPO/releases,"
verde "os botões do site novo passam a funcionar. Aí publique o site:"
echo "     ./site/PUBLICAR-SITE.sh"
