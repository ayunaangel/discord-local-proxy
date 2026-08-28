# Discord Proxy

Faz o Discord — **e só ele** — sair pela internet por outro lugar. É isso que
muda a região do servidor de voz que o Discord te entrega, e é por esse mesmo
servidor que passam a câmera e o compartilhamento de tela.

Serve para quando esses recursos ficam indisponíveis ou instáveis por causa da
sua região, e você precisa deles para trabalhar. Não mexe em proxy do sistema,
DNS, firewall, rotas nem certificados: o resto do computador continua saindo
normalmente.

Funciona no Windows, no Linux e no macOS.

> **Para quem não usa terminal:** abra o programa e siga a janela. O passo a
> passo completo está em [COMO-USAR.txt](COMO-USAR.txt).

## Em um minuto

Se você tem o Tor Browser instalado, o programa liga o Tor sozinho — sem abrir
navegador nenhum, sem janela preta, numa porta só dele:

```bash
python -m discord_proxy config --proxy tor --pais nl
```

Veja de onde você passa a parecer vir:

```bash
python -m discord_proxy exit-ip
```

Feche o Discord por inteiro, inclusive o ícone da bandeja, e abra por aqui:

```bash
python -m discord_proxy run
```

Entre numa chamada e confirme para onde ela foi:

```bash
python -m discord_proxy region
```

Ou faça tudo pela janela:

```bash
python -m discord_proxy
```

No Linux dê dois cliques em `INICIAR-LINUX.sh`; no Windows, em
`INICIAR-WINDOWS.cmd`.

## Como isso muda a região

O Discord decide qual servidor de voz te dar durante o handshake do gateway, que
é TCP e passa pelo proxy. Ele vê o IP de saída do proxy, conclui que você está
naquele país e devolve um servidor de lá. A partir daí, voz, câmera e Go Live
falam com esse servidor.

**Com o proxy ligado, a mídia também passa por ele.** Isso foi medido: com um
proxy configurado, o Discord não abre um único socket UDP — o WebRTC cai para
TCP/TLS e voz, câmera e tela sobem pelo mesmo túnel. Numa transmissão de tela de
teste, 13 MB subiram pela ponte e a tabela UDP do sistema ficou vazia.

Duas consequências:

- No modo com proxy, o seu IP não aparece para o servidor de mídia. Ainda assim,
  **isto não é uma VPN**: só o Discord sai por ali, e o proxy vê o volume do seu
  tráfego. No modo direto (sem proxy), a mídia volta a sair por UDP do seu IP.
- Como a mídia divide o túnel com o resto, a qualidade depende da banda e da
  latência do proxy. Um circuito Tor costuma ser ruim para vídeo; uma VPS
  própria, não.

**Se a decisão não for pelo IP, isto não resolve.** Se o que limita o recurso for
a conta e não a região de rede, trocar a saída não muda nada. O jeito de saber é
medir: `exit-ip` mostra o país que você passou a apresentar, `region` mostra para
onde a chamada foi. Se os dois mudarem e o recurso continuar indisponível, a
decisão não é pelo IP.

## Confirmar que funcionou

```bash
python -m discord_proxy exit-ip
```

Mostra o IP e o país que o Discord enxerga. Sem proxy, é o seu IP de verdade.

```bash
python -m discord_proxy region
```

Com uma chamada em andamento, mostra o servidor em uso — algo como
`c-iad10-b19ce4e8.discord.media:2053 — Washington, EUA (US East)`. A região sai
do próprio nome do servidor: o Discord usa o código IATA do aeroporto mais
próximo do datacenter (`gru` é São Paulo, `iad` é a Virgínia, `ams` é Amsterdã).

De onde vem essa informação, em ordem:

1. **Com proxy** — da ponte, que vê o nome que o Discord pediu. Funciona em
   qualquer sistema e é a fonte mais precisa.
2. **Sem proxy, no Linux** — do `/proc`, cruzando os sockets UDP do Discord com
   a tabela do kernel. Não precisa de nada instalado.
3. **Sem proxy, no Windows** — do componente nativo, que só está ativo com
   `voice = on`.

Quando só há IP e nenhum nome, `--online` pergunta o país a um serviço externo.

O `exit-ip` consulta um serviço externo (`ipinfo.io`, com `ifconfig.co` e
`check.torproject.org` de reserva — o primeiro recusa saídas do Tor) e só roda
quando você o chama. O `region` não consulta nada, a menos que você peça
`--online`.

## Configuração

Um arquivo, uma seção:

```ini
[discord-proxy]
proxy = socks5://127.0.0.1:9150
voice = off
```

| Chave | O que faz |
|---|---|
| `proxy` | Por onde o Discord sai. Vazio = direto. `tor` = liga o Tor sozinho. Ou uma URL `http://`/`socks5://`, com ou sem usuário e senha. **É esta a chave que muda a região.** |
| `pais` | Só com `proxy = tor`: país de saída (`us`, `nl`, `de`, `fr`, `gb`…). Vazio = o Tor escolhe. |
| `tor` | Pasta do Tor Browser, se ele não estiver num lugar comum. |
| `voice` | Coisa diferente: mexe no primeiro pacote UDP para furar filtro de DPI. Não tem efeito nenhum sobre região. Deixe `off` a menos que a voz esteja bloqueada na sua rede. |
| `delay` | Pausa em milissegundos usada pelo ajuste de voz. 0 a 1000. |
| `packet` | Arquivo `.bin` opcional enviado antes do preparo de voz. Não vem no projeto. |
| `discord` | Caminho manual do executável, se a detecção automática não achar. |

Para deixar a senha fora do arquivo:

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
do [drover](https://github.com/hdrover/discord-drover), e um `drover.ini` antigo
também é lido.

## Que saída usar

- **Tor embutido (`proxy = tor`)** — o programa acha o Tor dentro do Tor Browser
  instalado e liga só a parte de rede dele: sem navegador, sem janela, numa
  porta própria, com os dados numa pasta nossa. Dá para fixar o país com `pais`.
  Bom para testar se a troca de região resolve o seu caso; **ruim para o dia a
  dia**. A subida pelo Tor é muito lenta: numa medição aqui, 2 MB levaram 37
  segundos (55 KB/s) contra 0,2 segundo na conexão direta. Na prática, o envio
  de imagens estoura o tempo do Discord e a imagem some sem aviso. O login
  também costuma cair em captcha.

  Escolher país exige as tabelas de GeoIP, que vêm no Tor Browser. Um `tor`
  avulso instalado pelo sistema pode não trazê-las — nesse caso só o modo
  automático funciona, e o programa avisa.
- **Um servidor SOCKS5 ou HTTP seu** — uma VPS com túnel SSH
  (`ssh -D 1080 usuario@servidor`) dá um SOCKS5 estável em `127.0.0.1:1080`,
  com país fixo, sem captcha e com a banda da VPS. É a opção para usar
  trabalhando.

A banda importa mais do que parece: com o proxy ligado, **a mídia da chamada
passa por ele**, e o upload de anexos também. Se as imagens somem ao enviar ou a
transmissão trava, olhe o tempo dos túneis:

```bash
python -m discord_proxy log --problemas
```

## Comandos

```bash
python -m discord_proxy detect      # o que está instalado
python -m discord_proxy plan        # o que seria feito, sem abrir nada
python -m discord_proxy test        # só testa se o proxy responde
python -m discord_proxy exit-ip     # de onde você parece vir
python -m discord_proxy region      # para onde a chamada está indo
python -m discord_proxy run         # abre o Discord
python -m discord_proxy shortcut    # cria o atalho "Discord (Proxy)"
python -m discord_proxy tor         # testa só o Tor embutido (--pais nl)
python -m discord_proxy log         # o que passou pela ponte, com volume e desfecho
python -m discord_proxy relatorio   # gera o .txt de diagnóstico
python -m discord_proxy clean       # remove atalho e componente nativo
```

`run`, `plan`, `test`, `region` e `shortcut` aceitam
`--channel stable|ptb|canary` e `--config CAMINHO`.

## Como funciona por dentro

O Electron aceita `--proxy-server=host:porta`, mas não aceita usuário e senha e
não fala SOCKS5 autenticado. Então o launcher sobe uma ponte HTTP em `127.0.0.1`
numa porta sorteada, aponta o Discord para ela, e é a ponte que se autentica no
proxy de verdade. A senha nunca vira argumento, nunca entra no atalho e nunca
aparece no log. `--disable-quic` entra junto, senão o Chromium escapa por UDP e
passa ao largo do proxy.

Enquanto o Discord estiver aberto com proxy, o processo do launcher precisa
continuar vivo: a ponte morre junto com ele.

### O ajuste de voz (opcional, desligado por padrão)

Coisa separada, para outro problema: rede que bloqueia voz por inspeção de
pacote. Quando o Discord manda a descoberta de IP (74 bytes, tipo `0x0001`), o
componente nativo envia antes um `0x00`, um `0x01` e, se você indicar, o
conteúdo de um `.bin` seu. Isso engana alguns filtros que decidem pelos
primeiros bytes do fluxo. Não cria túnel e não muda região.

| Plataforma | Componente | Como entra |
|---|---|---|
| Windows | `version.dll` | Ao lado do `Discord.exe` (carregamento lateral), trocando os ponteiros de `sendto`/`WSASendTo` na tabela de imports. |
| Linux | `libdiscordproxy.so` | `LD_PRELOAD` só no processo que o launcher abre. |
| macOS | — | O app é assinado e o sistema ignora bibliotecas injetadas. Só o proxy funciona. |
| Flatpak / Snap | — | O sandbox recusa bibliotecas externas. Só o proxy funciona. |

O `version.dll` nunca sobrescreve um `version.dll` que não seja nosso: a
instalação guarda o hash e a remoção só apaga se bater, e a arquitetura do PE é
conferida antes (uma DLL de arquitetura errada impediria o Discord de abrir).
Alguns antivírus reclamam de qualquer DLL ao lado de um `.exe` — confira o hash
do release e o código antes de liberar.

## Compilar

Só é necessário se você for usar o ajuste de voz.

```bash
python build.py
```

Precisa de `gcc` ou `clang`; nada de CMake, nada baixado durante o build. Para
gerar o `version.dll` do Windows a partir do Linux:

```bash
sudo dnf install mingw64-gcc            # Fedora
sudo apt install gcc-mingw-w64-x86-64   # Debian/Ubuntu
python build.py --target windows
```

Ou, sem instalar nada no sistema, um compilador que vem por pip e gera as duas
plataformas sozinho:

```bash
python -m pip install ziglang
python build.py --all
```

Pacote para a página de releases (precisa do PyInstaller):

```bash
python -m pip install pyinstaller
python package.py
```

Testes:

```bash
python -m unittest discover -s tests -t .
```

## Quando algo dá errado

```bash
python -m discord_proxy relatorio
```

Gera um `discord-proxy-relatorio.txt` na Área de Trabalho com o sistema, o
Discord encontrado, a configuração, o registro da ponte e as últimas mensagens
do Tor. A senha aparece como `***`. É o arquivo para mandar a quem for ajudar.

Na janela, o mesmo está no botão **Salvar relatório (.txt)**.

## O que isto não é

- Não é VPN. Só o Discord sai pelo proxy; o resto do computador, não. No modo
  direto (sem proxy), a mídia sai do seu IP real por UDP.
- Não desliga verificação de TLS, não mexe no `app.asar`, não injeta
  certificado e não contém código do Discord.
- Não precisa de administrador, root nem driver. Tudo é por usuário e reversível
  com `clean`.

## Licença

MIT. Veja [LICENSE](LICENSE).
