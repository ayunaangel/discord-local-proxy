# Segurança e privacidade

## Propriedades intencionais

- Instalação por usuário, sem privilégios elevados e sem alterações globais.
- Execução com listas de argumentos e `shell=False`.
- Proxy configurado falha fechado: sem upstream funcional, Discord não inicia.
- Ponte vinculada apenas a IPv4 loopback, em porta aleatória, e encerrada com o
  processo Discord.
- Senhas removidas de argumentos, ambiente do Discord, atalhos e manifesto.
- Escritas atômicas com modo `0600`; INI manual com senha e permissões mais
  abertas é recusado no POSIX.
- Links simbólicos, arquivos especiais e DLL preexistente sem recibo/hash são
  preservados em vez de sobrescritos.
- O pacote UDP opcional é limitado ao tamanho máximo de um datagrama, não segue
  links simbólicos e só é enviado ao mesmo destino da descoberta do Discord.

## Limites

Qualquer processo local que descubra a porta efêmera durante a execução pode
tentar usar a ponte; Chromium não permite fornecer credenciais na URL de proxy,
então autenticar o listener sem diálogo do Discord não é viável. A porta não é
publicada e não aceita conexões da LAN.

Uma senha escrita no INI continua sendo texto simples. `password_env` reduz esse
risco, mas o ambiente que inicia o launcher precisa conter a variável. Integração
com Credential Manager/Secret Service não faz parte desta versão.

O shim Windows usa carregamento lateral de `version.dll`, técnica legítima mas
também usada por malware. Isso pode gerar alerta de antivírus. Releases de
produção devem ser assinados e acompanhados de SHA-256. Nunca permita uma DLL de
origem não verificada.

O modo de voz não anonimiza nem encapsula UDP. Ele é experimental, regional e
pode parar de funcionar após mudanças no Discord ou no filtro de rede.

O conteúdo de um pacote personalizado é transmitido sem interpretação. Use apenas
um arquivo cuja origem e conteúdo você compreenda; o projeto não distribui nem
baixa automaticamente pacotes de terceiros.

## Relato de vulnerabilidade

Não inclua INIs, senhas, endereços privados de proxy ou capturas com tokens em
issues públicas. Forneça uma reprodução com dados fictícios e os hashes dos
binários envolvidos.
