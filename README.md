# Discord Local Proxy

[![Build e releases](https://github.com/ayunaangel/discord-local-proxy/actions/workflows/release.yml/badge.svg)](https://github.com/ayunaangel/discord-local-proxy/actions/workflows/release.yml)
[![Licença MIT](https://img.shields.io/badge/licen%C3%A7a-MIT-3c1765.svg)](LICENSE)

**[Baixar a versão mais recente](https://github.com/ayunaangel/discord-local-proxy/releases/latest)** · **[Abrir tutorial completo](https://discord-local-proxy.vercel.app/tutorial)** · **[Conhecer o site](https://discord-local-proxy.vercel.app/)**

Uma ferramenta pequena, por usuário, para iniciar Discord Stable, PTB ou Canary
com um proxy HTTP/SOCKS5 **apenas no próprio Discord**. Ela não altera proxy do
sistema, DNS, firewall, rotas ou certificados.

O executável abre um instalador gráfico: escolha os canais detectados, informe o
proxy e clique em **Instalar / Atualizar**. Isso cria atalhos separados, como
`Discord (Proxy)`. Para remover os atalhos e componentes, abra o instalador de
novo e clique em **Desinstalar**.

## Download recomendado

Use a área [Releases](https://github.com/ayunaangel/discord-local-proxy/releases/latest)
e baixe o pacote completo da sua plataforma:

- `DiscordLocalProxy-Windows-x64.zip`
- `DiscordLocalProxy-Linux-x64.tar.gz`

As releases são compiladas pelo GitHub Actions a partir do código público deste
repositório. Extraia tudo antes de abrir o instalador; não execute apenas o
binário isolado.

## O que funciona

- HTTP, HTTPS e WebSocket do Electron passam por um proxy HTTP local em
  `127.0.0.1`, encaminhado para um upstream HTTP ou SOCKS5.
- Proxy HTTP Basic e SOCKS5 usuário/senha são aceitos sem colocar a senha nos
  argumentos do Discord, no ambiente do processo ou no atalho.
- Stable, PTB e Canary são descobertos separadamente no Windows e Linux.
- Sem proxy (`type = none`), o Discord inicia direto e o modo experimental de voz
  continua disponível.
- Um pacote UDP binário opcional pode ser enviado antes do preparo `00/01` para
  filtros mais exigentes, inclusive no modo sem proxy.
- Instalação é por usuário e reversível. O aplicativo não exige administrador ou
  root.

## Limite importante da voz

O Discord documenta a mídia de voz como uma conexão UDP separada. O proxy do
Electron cobre HTTP(S)/WebSocket, **não UDP**. Por isso, esta ferramenta usa uma
técnica experimental e bem limitada: no primeiro pacote de descoberta UDP do
Discord (assinatura de 74 bytes), envia primeiro o pacote personalizado opcional,
depois `0x00`, `0x01`, espera 50 ms e só então envia o pacote original.

O arquivo personalizado não é obrigatório nem incluído no projeto. Na interface,
use **Selecionar…** em “Pacote inicial”. Manualmente, defina `packet_file` no INI
ou coloque `discord-local-proxy-packet.bin`/`drover-packet.bin` ao lado do INI ou
do Discord. O arquivo precisa ser regular, não vazio e ter no máximo 65.507 bytes.
Ele é relido a cada novo socket/destino de mídia, permitindo trocar seu conteúdo
entre chamadas sem reinstalar a ferramenta.

Esse preparo pode contornar alguns filtros locais por inspeção de pacotes. Ele
não cria um túnel, não envia voz pelo proxy e não vence uma rede que bloqueie todo
UDP. O IP público usado pela voz permanece visível ao servidor Discord. Para uma
garantia real sob bloqueio total de UDP, use uma VPN/TUN com suporte a UDP.

## Início rápido

Baixe e extraia **todo** o artefato da sua plataforma. Na primeira utilização,
abra o instalador fácil:

- Windows: dê dois cliques em `INSTALAR-WINDOWS.cmd`.
- Linux: dê dois cliques em `INSTALAR-LINUX.sh` e escolha **Executar**, ou rode
  `./INSTALAR-LINUX.sh` no terminal.

Ele abre a interface gráfica para escolher Stable/PTB/Canary, configurar o proxy
e usar **Instalar / Atualizar**. A instalação não ocorre silenciosamente. Depois,
inicie o Discord pelo atalho `Discord (Proxy)` criado no menu de aplicativos ou
na área de trabalho.

Para abrir novamente o gerenciador, use o arquivo de início fácil:

- Windows: dê dois cliques em `INICIAR-WINDOWS.cmd`.
- Linux: dê dois cliques em `INICIAR-LINUX.sh` e escolha **Executar**, ou rode
  `./INICIAR-LINUX.sh` no terminal.

Sem argumentos, esses arquivos abrem a interface gráfica. Eles encontram o
executável automaticamente e, na pasta do código-fonte, ainda conseguem iniciar
pelo Python como alternativa. No Linux, o arquivo também tenta corrigir a
permissão de execução do binário. Os comandos de terminal continuam disponíveis,
por exemplo `./INICIAR-LINUX.sh status` ou `INICIAR-WINDOWS.cmd status`.

Também é possível abrir `DiscordLocalProxy.exe`/`DiscordLocalProxy` diretamente.

## Logs e diagnóstico

O instalador e todos os atalhos criados registram inicialização, operações e
erros sem incluir senhas, conteúdo do INI ou variáveis de ambiente. O arquivo
principal é rotacionado ao atingir 1 MiB; até quatro cópias anteriores são
preservadas.

- Windows: `%LOCALAPPDATA%\discord-local-proxy\logs\discord-local-proxy.log`
- Linux: `~/.local/state/discord-local-proxy/logs/discord-local-proxy.log`
  (ou `$XDG_STATE_HOME/discord-local-proxy/logs/discord-local-proxy.log`)

Na interface, clique em **Abrir logs**. Pelo terminal, use
`DiscordLocalProxy logs` para mostrar o caminho ou `DiscordLocalProxy logs --open`
para abrir a pasta. A desinstalação preserva os logs para facilitar reparos.

## Pacotes para compartilhar

Cada plataforma possui um pacote próprio. Não envie somente o executável: envie
o arquivo compactado completo e peça para a pessoa extrair tudo antes de abrir o
instalador.

- Linux: `release/DiscordLocalProxy-Linux-x64.tar.gz`
- Windows: `release/DiscordLocalProxy-Windows-x64.zip`

Depois da extração, ficam visíveis apenas `INSTALAR-LINUX.sh` e
`INICIAR-LINUX.sh` no Linux, ou `INSTALAR-WINDOWS.cmd` e
`INICIAR-WINDOWS.cmd` no Windows. Os componentes internos ficam em
`.discord-local-proxy`; os iniciadores do Windows marcam essa pasta como oculta
na primeira execução.

Feche o Discord completamente, inclusive o ícone da bandeja, antes de usar o
novo atalho. Uma instância já aberta ignora os novos argumentos de proxy.

No Windows, o componente de voz é uma `version.dll` própria e identificada por
hash, instalada ao lado do `Discord.exe` da versão ativa. O launcher a sincroniza
depois de atualizações. Ele recusa sobrescrever outra `version.dll` não gerenciada.
Alguns antivírus podem alertar sobre carregamento lateral de DLL; confira a
assinatura/hash do release e o código-fonte antes de permitir.

No Linux, o componente de voz é carregado com `LD_PRELOAD` somente no processo
iniciado pelo atalho. Flatpak e Snap normalmente impedem bibliotecas externas;
nesses pacotes o proxy TCP pode funcionar, mas o modo de voz é recusado com uma
mensagem clara. Prefira o pacote `.deb`/`.tar.gz` oficial para o recurso completo.

## Configuração manual

Crie um INI a partir do modelo:

```bash
python -m discord_local_proxy init-config ./discord-local-proxy.ini
```

Edite-o:

```ini
[proxy]
type = socks5
host = 127.0.0.1
port = 1080
username = usuario
password =
password_env = DISCORD_PROXY_PASSWORD
connect_timeout = 10

[voice]
enabled = true
delay_ms = 50
packet_file =

[discord]
executable =
```

Depois inicie o canal desejado:

```bash
python -m discord_local_proxy launch --channel stable --config ./discord-local-proxy.ini
```

Precedência do INI: `--config` explícito, arquivo ao lado do executável Discord,
arquivo na raiz do canal e, por fim, o arquivo do canal em `~/.config` (ou
`XDG_CONFIG_HOME`) criado pelo instalador. Um arquivo é lido por completo;
configurações não são mescladas.

Se usar `password`, o INI deve ter permissão `0600` no Linux. A alternativa
recomendada é deixar `password` vazio, preencher `password_env` e fornecer a
senha fora do arquivo. O arquivo completo de exemplo está em
[`discord-local-proxy.ini.example`](discord-local-proxy.ini.example).

`voice.packet_file` aceita caminho absoluto ou relativo ao INI. Se estiver vazio,
a ferramenta procura os dois nomes compatíveis mencionados acima. Nenhum pacote
de terceiros é baixado ou instalado automaticamente.

### Modo manual no Windows

Para o preparo de voz sem o launcher, é possível colocar o `version.dll` do
release e o INI ao lado do `Discord.exe`; a DLL lê apenas `[voice]`. O proxy ainda
exige o launcher/atalho porque é aplicado por argumento suportado do Electron.
Não copie a DLL se já existir outra `version.dll` nesse diretório.

## Locais detectados (confirmados em agosto de 2026)

Windows:

```text
%LOCALAPPDATA%\Discord\app-*\Discord.exe
%LOCALAPPDATA%\DiscordPTB\app-*\DiscordPTB.exe
%LOCALAPPDATA%\DiscordCanary\app-*\DiscordCanary.exe
```

Linux, pacotes oficiais atuais:

```text
/usr/bin/discord         ~/.config/discord/Discord
/usr/bin/discord-ptb     ~/.config/discordptb/DiscordPTB
/usr/bin/discord-canary  ~/.config/discordcanary/DiscordCanary
```

Também há descoberta limitada de AppImage em `~/Applications`, Flatpak e Snap.
Não há varredura do disco inteiro. Um caminho personalizado pode ser colocado em
`[discord] executable`.

## Comandos úteis

```bash
python -m discord_local_proxy detect
python -m discord_local_proxy check-gui
python -m discord_local_proxy check-font
python -m discord_local_proxy check --config ./discord-local-proxy.ini
python -m discord_local_proxy install --channels stable --from-config ./discord-local-proxy.ini
python -m discord_local_proxy install --channels stable --voice-packet-file ./pacote.bin
python -m discord_local_proxy uninstall
python -m discord_local_proxy uninstall --purge-config
```

Quando um proxy está configurado, o launcher o testa antes de abrir o Discord. Se
o teste ou autenticação falhar, o Discord não é iniciado e não há fallback direto
silencioso. `--disable-quic` também é passado para impedir que o tráfego normal do
Chromium contorne o proxy via QUIC/UDP.

## Build a partir do código

Requisitos: Python 3.11+, CMake 3.20+, compilador C/C++ e PyInstaller. No Windows,
use Visual Studio 2022 com o workload C++; no Linux, GCC ou Clang, os headers de
desenvolvimento da libc e Tk com Fontconfig/Xft (normalmente `python3-tk`). Para o
binário Linux, prefira o Python da distribuição; alguns runtimes portáteis de
Python/uv trazem um Tk sem antialiasing e o build agora os rejeita em `check-font`.

```bash
python -m pip install pyinstaller
bash scripts/build-linux.sh
```

No PowerShell do Visual Studio:

```powershell
python -m pip install pyinstaller
./scripts/build-windows.ps1
```

O build Windows baixa MinHook 1.3.4 pelo CMake, fixado no commit indicado em
[`NOTICE.md`](NOTICE.md). O CI compila e testa cada plataforma antes de produzir
o binário em `dist/` e o pacote compartilhável em `release/`.

## Modelo de segurança

- A ponte autenticada escuta somente em `127.0.0.1` e existe enquanto o Discord
  usa o proxy.
- Credenciais nunca entram na linha de comando, atalhos ou logs.
- INIs são gravados atomicamente e links simbólicos inesperados são recusados.
- O desinstalador remove somente nomes próprios conhecidos e valida o hash da DLL
  antes de apagá-la.
- Nenhuma validação TLS do Discord é desativada.

Veja [`docs/SECURITY.md`](docs/SECURITY.md) e
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) para detalhes.

## Fontes técnicas atuais

- [Electron: `--proxy-server`](https://www.electronjs.org/docs/latest/api/command-line-switches#--proxy-serveraddressport)
  — cobre HTTP, HTTPS e WebSocket e não aceita credenciais na URL.
- [Chromium: configuração de proxy](https://chromium.googlesource.com/chromium/src/+/master/net/docs/proxy.md)
  — SOCKS5 resolve DNS no proxy, mas não implementa autenticação nem UDP; a ponte
  local cobre a autenticação.
- [Discord: Voice Connections](https://docs.discord.com/developers/topics/voice-connections)
  — mídia separada por UDP e formato de descoberta de IP.
- [Discord: instalação corrompida no Windows](https://support.discord.com/hc/en-us/articles/115004307527--Windows-Corrupt-Installation)
  — confirma `%LocalAppData%/Discord` e `%AppData%/Discord`.
- [Discord: clientes de teste](https://support.discord.com/hc/en-us/articles/360035675191-Discord-Testing-Clients)
  — canais PTB e Canary atuais.

Este projeto é uma implementação independente. Ele não altera `app.asar`, não
injeta certificados e não contém código do Discord ou de ferramentas sem licença.
