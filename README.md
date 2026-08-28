# Discord Proxy

Faz o Discord usar um proxy HTTP ou SOCKS5 **só ele**, e ajusta o primeiro
pacote UDP da voz para passar por alguns filtros de rede. Não mexe em proxy do
sistema, DNS, firewall, rotas nem certificados. Funciona no Windows, no Linux e
— só na parte de proxy — no macOS.

É a mesma ideia do [discord-drover](https://github.com/hdrover/discord-drover),
com três diferenças: roda nas três plataformas, aceita SOCKS5 com usuário e
senha, e a senha nunca aparece na linha de comando do Discord.

## Em um minuto

```bash
python -m discord_proxy config --proxy socks5://usuario:senha@servidor:1080
python -m discord_proxy run
```

Ou abra a janela e preencha um campo só:

```bash
python -m discord_proxy
```

No Linux dê dois cliques em `INICIAR-LINUX.sh`; no Windows, em
`INICIAR-WINDOWS.cmd`.

## Configuração

Um arquivo, uma seção, cinco linhas — nenhuma obrigatória:

```ini
[discord-proxy]
proxy = socks5://usuario:senha@servidor:1080
voice = on
delay = 50
packet =
discord =
```

| Chave | O que faz |
|---|---|
| `proxy` | URL do proxy. Vazio = modo direto (sem proxy, só o ajuste de voz). Aceita `http://` e `socks5://`, com ou sem usuário e senha. |
| `voice` | Liga o ajuste de voz por UDP. `on` ou `off`. |
| `delay` | Pausa em milissegundos entre o preparo e o pacote real. 0 a 1000. |
| `packet` | Arquivo `.bin` opcional enviado antes do preparo. Não vem no projeto. |
| `discord` | Caminho manual do executável, para quando a detecção automática não achar. |

Para deixar a senha fora do arquivo, use uma variável de ambiente:

```ini
proxy = socks5://usuario:${MINHA_SENHA}@servidor:1080
```

Onde o arquivo fica:

| Sistema | Caminho |
|---|---|
| Windows | `%LOCALAPPDATA%\discord-proxy\discord-proxy.ini` |
| Linux | `~/.local/share/discord-proxy/discord-proxy.ini` |
| macOS | `~/Library/Application Support/discord-proxy/discord-proxy.ini` |

Um `discord-proxy.ini` ao lado do `Discord.exe` tem prioridade — é o modo manual
do drover, e continua funcionando. Um `drover.ini` antigo também é lido.

## Comandos

```bash
python -m discord_proxy detect      # o que está instalado e se a voz é possível
python -m discord_proxy plan        # o que seria feito, sem abrir nada
python -m discord_proxy test        # só testa o proxy
python -m discord_proxy run         # abre o Discord
python -m discord_proxy shortcut    # cria o atalho "Discord (Proxy)"
python -m discord_proxy clean       # remove atalho e componente nativo
```

Todos aceitam `--channel stable|ptb|canary` e `--config CAMINHO`.

## Como funciona

**A parte fácil (chat, login, updates).** O Electron aceita
`--proxy-server=host:porta`, mas não aceita usuário e senha, e não fala SOCKS5
autenticado. Então o launcher sobe uma ponte HTTP em `127.0.0.1` numa porta
sorteada, aponta o Discord para ela e é a ponte que se autentica no proxy de
verdade. A senha nunca vira argumento, nunca entra no atalho e nunca aparece no
log. `--disable-quic` entra junto para o Chromium não escapar por UDP.

**A parte difícil (voz).** A mídia de voz é UDP e não passa pelo proxy do
Electron — isso é do protocolo do Discord, não uma limitação daqui. O que dá
para fazer de dentro do processo é mexer no primeiro pacote: quando o Discord
manda a descoberta de IP (74 bytes, tipo `0x0001`), o componente nativo envia
antes o conteúdo do seu `.bin` (se houver), depois `0x00`, depois `0x01`, espera
o `delay` e só então deixa o pacote original seguir.

Isso engana alguns filtros que decidem pelos primeiros bytes de cada fluxo. Não
é um túnel: a voz continua saindo do seu IP, e uma rede que bloqueie todo UDP
continua bloqueando. Para isso só uma VPN com UDP resolve.

| Plataforma | Componente | Como entra |
|---|---|---|
| Windows | `version.dll` | Fica ao lado do `Discord.exe` (carregamento lateral) e troca os ponteiros de `sendto`/`WSASendTo` na tabela de imports do processo. |
| Linux | `libdiscordproxy.so` | `LD_PRELOAD` apenas no processo que o launcher abre. |
| macOS | — | O app é assinado e o sistema ignora bibliotecas injetadas. Só o proxy TCP funciona. |
| Flatpak / Snap | — | O sandbox recusa bibliotecas externas. Só o proxy TCP funciona. |

O `version.dll` nunca sobrescreve um `version.dll` que não seja nosso: a
instalação guarda o hash do arquivo e a remoção só apaga se bater. Alguns
antivírus reclamam de qualquer DLL ao lado de um `.exe` — confira o hash do
release e o código antes de liberar.

## Compilar

```bash
python build.py
```

Só isso. Precisa de `gcc` ou `clang`; nada de CMake, nada baixado durante o
build. Para gerar o `version.dll` do Windows a partir do Linux:

```bash
sudo dnf install mingw64-gcc     # Fedora
sudo apt install gcc-mingw-w64-x86-64   # Debian/Ubuntu
python build.py --target windows
```

Para montar o pacote da página de releases (precisa do PyInstaller):

```bash
python -m pip install pyinstaller
python package.py
```

Testes:

```bash
python -m unittest discover -s tests -t .
```

Os testes da ponte sobem proxies HTTP e SOCKS5 de mentira e conferem o
handshake de verdade. Os testes do componente nativo rodam sozinhos assim que
existir um `build/libdiscordproxy.so`.

## O que isto não é

- Não é VPN. O IP da voz continua sendo o seu.
- Não desliga verificação de TLS, não mexe no `app.asar`, não injeta
  certificado e não contém código do Discord.
- Não precisa de administrador, root nem driver. Tudo é por usuário e reversível
  com `clean`.

## Licença

MIT. Veja [LICENSE](LICENSE).
