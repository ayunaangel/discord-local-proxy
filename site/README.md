# Site do Discord Proxy

Cinco páginas em HTML, CSS e JavaScript puros. Sem dependências, sem
`node_modules`.

```
gerar.py         monta as páginas a partir de um molde comum
index.html       início
como-usar.html   passo a passo
download.html    downloads e requisitos
duvidas.html     perguntas frequentes
seguranca.html   o que faz, o que não faz, senha e privacidade
style.css        todo o visual (as cores ficam no topo, em :root)
script.js        sombra no topo, download do sistema certo, animação
vercel.json      redirecionamentos das rotas antigas e cabeçalhos
```

## Como editar

Os textos ficam no `gerar.py`, em blocos com o nome de cada página. Edite lá e
rode:

```bash
python3 gerar.py
```

Isso reescreve os cinco `.html`. Existe por um motivo simples: cabeçalho e
rodapé são iguais nas cinco páginas, e mantê-los copiados à mão significa que
uma mudança na navegação exige lembrar de editar todos — mais cedo ou mais tarde
uma página fica para trás.

O HTML gerado é versionado e funciona sozinho: quem só quer hospedar não precisa
rodar nada, e dá para editar um `.html` direto se for um ajuste pontual (só
lembre que o próximo `gerar.py` sobrescreve).

O JavaScript é opcional: com ele desligado a página continua completa e os
downloads continuam funcionando.

## Ver no computador antes de publicar

```bash
python3 -m http.server 4321 --directory site
```

Depois abra <http://localhost:4321>.

## Publicar na Vercel

O projeto atual da Vercel foi criado a partir de outro código. Há dois caminhos:

**Substituir o projeto que já existe** (mantém o endereço
`discord-local-proxy.vercel.app`):

```bash
npm i -g vercel     # só na primeira vez
cd site
vercel --prod
```

Quando ele perguntar, aponte para o projeto existente. Como não há build, deixe
o *framework preset* em **Other** e o diretório de saída em branco.

**Ou publicar em qualquer outro lugar**: são três arquivos estáticos. Funciona
em GitHub Pages, Netlify, Cloudflare Pages ou numa pasta de qualquer servidor.

## Antes de publicar, confira os downloads

Os botões apontam para a última release do GitHub, com estes nomes:

- `DiscordProxy-Windows-x64.zip`
- `DiscordProxy-Linux-x64.tar.gz`

São os nomes que o `package.py` gera. Enquanto não existir uma release com
esses arquivos, os links vão para uma página de erro do GitHub — publique a
release antes, ou troque os `href` no `index.html` para
`https://github.com/ayunaangel/discord-local-proxy/releases`.

## O que mudou em relação ao site anterior

- Seis páginas viraram uma. Todas as rotas antigas redirecionam para as seções
  novas, então nenhum link que alguém já tenha salvo quebra.
- O texto passou a falar do problema (câmera e tela que pararam de funcionar)
  em vez de descrever a tecnologia. Quem chega entende em cinco segundos se o
  programa resolve o caso dele.
- O download detecta o sistema de quem visita, destaca o arquivo certo e o move
  para o começo da lista.
- Uma seção "o que este programa não é", porque prometer menos e cumprir vale
  mais do que o contrário — inclusive o aviso de que enviar imagem pelo Tor
  falha.
- Contraste conferido nos dois temas (claro e escuro), todos acima de 4.5:1.
