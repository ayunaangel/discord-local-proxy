# Arquitetura

O sistema tem quatro módulos com limites pequenos:

1. `discovery.py` encontra Stable/PTB/Canary apenas em locais conhecidos.
2. `proxy_bridge.py` oferece um HTTP CONNECT local sem autenticação e traduz para
   HTTP Basic ou SOCKS5 (RFC 1928/1929) no upstream.
3. `launcher.py` testa o upstream, inicia a ponte, acrescenta os switches do
   Electron e aplica o shim de voz somente ao processo Discord.
4. `installer.py` grava INIs, componente nativo e atalhos por usuário, com nomes
   e destinos determinísticos.

```text
Discord/Electron
      │ HTTP(S), WebSocket
      ▼
127.0.0.1:porta efêmera
      │ autenticação ocorre aqui
      ▼
proxy HTTP ou SOCKS5 upstream
      │
      ▼
Internet

Discord voice UDP ── shim [arquivo] + 00/01 + 50 ms ──► servidor de voz (direto)
```

As credenciais são usadas apenas dentro do processo do launcher. O argumento do
Discord contém `http://127.0.0.1:porta`, nunca a URL upstream.

No Windows, `version.dll` encaminha todas as exports públicas para a DLL real em
`System32` e usa MinHook somente em `send`, `sendto`, `WSASend`, `WSASendTo` e
`closesocket`. No Linux, a biblioteca `LD_PRELOAD` intercepta `send`, `sendto`,
`sendmsg` e `close`. Ambos verificam socket UDP e a assinatura completa de
descoberta antes de interferir.

O estado é por socket e destino. A entrada é removida no fechamento para impedir
que reutilização de descritor/herança aplique estado antigo. Pacotes que não
correspondem à assinatura não são alterados.

Quando configurado, o pacote binário é aberto novamente no primeiro envio de cada
novo socket/destino, validado como arquivo regular de 1–65.507 bytes e transmitido
para o mesmo servidor UDP antes dos bytes `00/01`. A ausência ou falha de leitura
do arquivo preserva o preparo básico.
