#!/usr/bin/env python3
"""Monta as páginas do site a partir de um molde comum.

Sem isto, o cabeçalho e o rodapé estariam copiados em cinco arquivos e uma
mudança na navegação exigiria lembrar de editar todos. O HTML gerado fica
versionado e funciona sozinho — este script é conveniência, não dependência.

    python3 gerar.py
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parent

MENU = [
    ("/", "Início", "inicio"),
    ("/como-usar", "Como usar", "como-usar"),
    ("/download", "Download", "download"),
    ("/duvidas", "Dúvidas", "duvidas"),
    ("/seguranca", "Segurança", "seguranca"),
]

GITHUB = "https://github.com/ayunaangel/discord-local-proxy"
BAIXAR_WIN = f"{GITHUB}/releases/latest/download/DiscordProxy-Windows-x64.zip"
BAIXAR_LINUX = f"{GITHUB}/releases/latest/download/DiscordProxy-Linux-x64.tar.gz"

MOLDE = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo}</title>
<meta name="description" content="{descricao}">
<meta name="theme-color" content="#2A1E5C">
<meta property="og:title" content="{titulo}">
<meta property="og:description" content="{descricao}">
<meta property="og:type" content="website">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/style.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🌎</text></svg>">
</head>
<body>

<a class="pular" href="#conteudo">Pular para o conteúdo</a>

<header class="topo">
  <nav class="nav" aria-label="Navegação principal">
    <a class="marca" href="/">
      <span class="marca-ponto" aria-hidden="true"></span>
      Discord Proxy
    </a>
    <div class="nav-links">
{menu}
      <a class="nav-github" href="{github}">GitHub</a>
      <a class="botao botao-pequeno" href="/download">Baixar</a>
    </div>
  </nav>
</header>

<main id="conteudo">
{conteudo}
</main>

<footer class="rodape">
  <div class="envolve rodape-grade">
    <div>
      <a class="marca" href="/">
        <span class="marca-ponto" aria-hidden="true"></span>
        Discord Proxy
      </a>
      <p>Projeto independente, de código aberto, licença MIT.
      Não é afiliado ao Discord.</p>
    </div>
    <nav aria-label="Rodapé">
      <a href="/como-usar">Como usar</a>
      <a href="/download">Download</a>
      <a href="/duvidas">Dúvidas</a>
      <a href="/seguranca">Segurança</a>
      <a href="{github}">Código-fonte</a>
    </nav>
  </div>
</footer>

<script src="/script.js"></script>
</body>
</html>
"""


def montar_menu(ativo: str) -> str:
    linhas = []
    for href, rotulo, chave in MENU:
        atual = ' aria-current="page"' if chave == ativo else ""
        classe = ' class="ativo"' if chave == ativo else ""
        linhas.append(f'      <a href="{href}"{classe}{atual}>{rotulo}</a>')
    return "\n".join(linhas)


# ------------------------------------------------------------------ páginas --

INICIO = """
<section class="hero" id="inicio">
  <div class="hero-fundo" aria-hidden="true"></div>
  <div class="envolve hero-grade">
    <div class="hero-texto">
      <p class="selo">Grátis · código aberto · Windows e Linux</p>
      <h1>Sua câmera e sua tela<br><span class="destaque">de volta no Discord.</span></h1>
      <p class="chamada">
        Quando a câmera e o compartilhamento de tela param de funcionar por causa
        da sua região, é porque o Discord te entregou um servidor daqui. Este
        programa faz o Discord — <strong>e só ele</strong> — sair por outro país.
      </p>
      <div class="hero-acoes">
        <a class="botao botao-grande" href="/download" id="botao-principal">
          <span id="botao-principal-texto">Baixar agora</span>
          <span class="botao-sub" id="botao-principal-sub">grátis, sem cadastro</span>
        </a>
        <a class="botao botao-vazado botao-grande" href="/como-usar">Ver como funciona</a>
      </div>
      <ul class="hero-marcas">
        <li>Não mexe no resto do computador</li>
        <li>Não precisa de administrador</li>
        <li>Sai quando você quiser</li>
      </ul>
    </div>

    <div class="hero-janela" role="img" aria-label="Prévia da janela do programa">
      <div class="janela">
        <div class="janela-barra"><span></span><span></span><span></span>
          <p>Discord Proxy</p>
        </div>
        <div class="janela-corpo">
          <p class="janela-passo">PASSO 1 — De onde você quer sair</p>
          <div class="janela-campo">
            <span>Tor · Holanda</span>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
          </div>
          <p class="janela-nota">O Tor é ligado sozinho, sem abrir nada.</p>
          <p class="janela-passo">PASSO 2 — Abrir o Discord</p>
          <div class="janela-botao">Abrir o Discord</div>
          <div class="janela-log">
            <p><b>Discord</b> encontrado</p>
            <p><b>Tor</b> pronto — saindo pela Holanda</p>
            <p class="ok">Discord aberto. Câmera e tela liberadas.</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<section class="faixa" aria-label="Resumo">
  <div class="envolve faixa-grade">
    <div><b>1</b><span>programa afetado: o Discord</span></div>
    <div><b>0</b><span>mudanças no seu sistema</span></div>
    <div><b>15</b><span>países para escolher</span></div>
    <div><b>MIT</b><span>código aberto</span></div>
  </div>
</section>

<section class="secao" id="problema">
  <div class="envolve">
    <p class="olho">Para quem é</p>
    <h2>Você reconhece algum destes?</h2>
    <div class="cartoes">
      <article class="cartao">
        <div class="cartao-icone" aria-hidden="true">📷</div>
        <h3>A câmera e a tela sumiram</h3>
        <p>O botão está lá, mas não funciona — ou funciona para os outros e não
        para você. Quem usa o Discord para trabalhar fica sem reunião.</p>
      </article>
      <article class="cartao">
        <div class="cartao-icone" aria-hidden="true">📉</div>
        <h3>A transmissão corta o tempo todo</h3>
        <p>A chamada cai em um servidor com rota ruim até a sua operadora. Trocar
        a região resolve sem mexer em mais nada.</p>
      </article>
      <article class="cartao">
        <div class="cartao-icone" aria-hidden="true">🚫</div>
        <h3>VPN atrapalha o resto</h3>
        <p>Ligar uma VPN muda o computador inteiro: banco, streaming, jogos. Aqui
        só o Discord muda de lugar.</p>
      </article>
    </div>
  </div>
</section>

<section class="secao secao-escura">
  <div class="envolve">
    <p class="olho">Em resumo</p>
    <h2>Três passos e pronto.</h2>
    <ol class="passos">
      <li><span class="passo-numero">1</span><h3>Baixe e extraia</h3>
        <p>Um arquivo compactado. Nada de instalador.</p></li>
      <li><span class="passo-numero">2</span><h3>Escolha o país</h3>
        <p>O programa liga o Tor sozinho, escondido.</p></li>
      <li><span class="passo-numero">3</span><h3>Abra o Discord por ele</h3>
        <p>Feche o Discord antes e clique no botão.</p></li>
    </ol>
    <p class="passos-nota">
      <a class="botao botao-claro" href="/como-usar">Ver o passo a passo completo</a>
    </p>
  </div>
</section>

<section class="cta">
  <div class="envolve">
    <h2>Leva dois minutos.</h2>
    <p>Baixe, extraia, escolha um país e abra o Discord.</p>
    <a class="botao botao-grande botao-claro" href="/download">Baixar o programa</a>
  </div>
</section>
"""

COMO_USAR = """
<section class="cabeca">
  <div class="envolve envolve-estreito">
    <p class="olho">Como usar</p>
    <h1>Do download à primeira chamada.</h1>
    <p class="chamada">Sem terminal, sem instalador. Se algum passo não bater com
    o que você está vendo, a página de <a href="/duvidas">dúvidas</a> cobre os
    casos mais comuns.</p>
  </div>
</section>

<section class="secao">
  <div class="envolve envolve-estreito">
    <ol class="guia">
      <li>
        <h2>1. Baixe e extraia</h2>
        <p>Pegue o arquivo da sua plataforma na
        <a href="/download">página de download</a> e extraia <b>tudo</b> numa
        pasta — não abra de dentro do compactador.</p>
        <p>Depois de extrair você vê dois itens: o arquivo de início e o
        <code>COMO-USAR.txt</code>.</p>
      </li>
      <li>
        <h2>2. Abra o programa</h2>
        <p><b>Windows:</b> clique duas vezes em <code>INICIAR-WINDOWS.cmd</code>.</p>
        <p><b>Linux:</b> clique duas vezes em <code>INICIAR-LINUX.sh</code> e
        escolha “Executar”. Se preferir o terminal:
        <code>./INICIAR-LINUX.sh</code>.</p>
      </li>
      <li>
        <h2>3. Escolha de onde sair</h2>
        <p>Na lista do <b>Passo 1</b>, escolha uma opção:</p>
        <ul class="lista">
          <li><b>Daqui mesmo</b> — nada é trocado.</li>
          <li><b>Tor · Automático</b> — o programa liga o Tor e deixa ele escolher.</li>
          <li><b>Tor · um país</b> — força a saída por ali. Se não houver servidor
          disponível no momento, o programa avisa e você tenta outro.</li>
          <li><b>Meu próprio proxy</b> — se você tem uma VPS ou um proxy da empresa.</li>
        </ul>
        <p>Para usar o Tor, instale o
        <a href="https://www.torproject.org/pt-BR/download/">Tor Browser</a> e deixe
        a pasta em Downloads. Você <b>não</b> precisa abrir o navegador: o programa
        usa só a parte de rede dele, sem janela nenhuma.</p>
      </li>
      <li>
        <h2>4. Feche o Discord por inteiro</h2>
        <p>Isso inclui o ícone ao lado do relógio — clique com o botão direito e
        escolha sair. Um Discord já aberto ignora a troca, e o programa recusa
        continuar até que ele esteja fechado.</p>
      </li>
      <li>
        <h2>5. Clique em “Abrir o Discord”</h2>
        <p>O programa liga a saída, confere se ela responde e só então abre o
        Discord. Se a saída falhar, ele não abre nada — melhor isso do que abrir
        e você achar que está protegido quando não está.</p>
        <p><b>Deixe a janela do programa aberta</b> enquanto estiver usando. É ela
        que mantém a ponte de conexão viva.</p>
      </li>
      <li>
        <h2>6. Confira se funcionou</h2>
        <p>Clique em <b>Onde estou saindo</b>: aparece o IP e o país que o Discord
        enxerga. Dentro de uma chamada, clique em <b>Região da chamada</b> para ver
        o servidor em uso — algo como
        <code>c-ams02-1a2b3c4d.discord.media — Amsterdã, Holanda</code>.</p>
      </li>
    </ol>

    <div class="aviso">
      <h3>Se algo travar</h3>
      <p>Clique em <b>Encerrar sessão</b>. Isso fecha o Discord e desliga a saída;
      depois é só abrir o Discord normalmente.</p>
      <p>Enquanto você usa, se alguma conexão passar de 25 segundos, a janela avisa
      que a saída está lenta. É o sinal de que enviar imagem vai falhar.</p>
    </div>

    <div class="aviso aviso-forte">
      <h3>Sobre o Tor e o envio de imagens</h3>
      <p>O Tor é lento para <b>enviar</b> arquivos: numa medição, 2 MB levaram 37
      segundos, contra menos de um segundo na conexão direta. Na prática, a imagem
      que você manda no Discord simplesmente some, sem mensagem de erro.</p>
      <p>Para conversar, para a câmera e para transmitir tela, funciona. Para
      trabalhar o dia todo mandando arquivos, use um proxy próprio — uma VPS com
      <code>ssh -D 1080</code> resolve.</p>
    </div>
  </div>
</section>
"""

DOWNLOAD = """
<section class="cabeca">
  <div class="envolve">
    <p class="olho">Download</p>
    <h1>Baixe e use hoje.</h1>
    <p class="chamada">Nada de instalador, nada de cadastro. Extraia e abra.</p>
  </div>
</section>

<section class="secao">
  <div class="envolve">
    <div class="downloads">
      <a class="download" id="dl-windows" href="{win}">
        <span class="download-so" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M3 5.5l7.5-1v7H3v-6zM11.5 4.3L21 3v8.5h-9.5v-7.2zM3 12.5h7.5v7L3 18.5v-6zM11.5 12.5H21V21l-9.5-1.3v-7.2z"/></svg>
        </span>
        <span class="download-texto"><b>Baixar para Windows</b><span>x64 · arquivo .zip</span></span>
        <span class="download-seta" aria-hidden="true">↓</span>
      </a>
      <a class="download" id="dl-linux" href="{linux}">
        <span class="download-so" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M12 2c2.2 0 3.5 1.8 3.5 4.2 0 1.6.4 2.4 1.4 3.9 1.2 1.8 2.1 3.2 2.1 5.1 0 2.6-1.6 4.3-3.2 4.3-1 0-1.4-.4-2-1-.5.4-1.1.6-1.8.6s-1.3-.2-1.8-.6c-.6.6-1 1-2 1-1.6 0-3.2-1.7-3.2-4.3 0-1.9.9-3.3 2.1-5.1 1-1.5 1.4-2.3 1.4-3.9C8.5 3.8 9.8 2 12 2z"/></svg>
        </span>
        <span class="download-texto"><b>Baixar para Linux</b><span>x64 · arquivo .tar.gz</span></span>
        <span class="download-seta" aria-hidden="true">↓</span>
      </a>
      <a class="download download-fonte" href="{github}">
        <span class="download-so" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M9 18l-5-6 5-6M15 6l5 6-5 6"/></svg>
        </span>
        <span class="download-texto"><b>Ver o código-fonte</b><span>tudo aberto, licença MIT</span></span>
        <span class="download-seta" aria-hidden="true">→</span>
      </a>
    </div>

    <p class="downloads-nota">
      <a href="{github}/releases">Todas as versões e notas de lançamento</a>
    </p>
  </div>
</section>

<section class="secao secao-clara">
  <div class="envolve envolve-estreito">
    <h2>O que você precisa ter</h2>
    <div class="requisitos">
      <div>
        <h3>Windows</h3>
        <p>Windows 10 ou 11, 64 bits. Nada mais — o programa vem pronto.</p>
      </div>
      <div>
        <h3>Linux</h3>
        <p>Qualquer distribuição 64 bits. Se for rodar pelo código-fonte em vez do
        pacote pronto, precisa do Tk:
        <code>sudo dnf install python3-tkinter</code> ou
        <code>sudo apt install python3-tk</code>.</p>
      </div>
      <div>
        <h3>macOS</h3>
        <p>A troca de região funciona pelo código-fonte. Não há pacote pronto, e o
        ajuste de voz por UDP não funciona — o sistema ignora bibliotecas
        injetadas em aplicativos assinados.</p>
      </div>
      <div>
        <h3>Para usar o Tor</h3>
        <p>Instale o <a href="https://www.torproject.org/pt-BR/download/">Tor
        Browser</a> e deixe a pasta em Downloads. Não precisa abrir o navegador.
        Ou use um proxy próprio, se tiver.</p>
      </div>
    </div>
    <p class="secao-chamada" style="margin-top:28px">
      Depois de baixar, siga o <a href="/como-usar">passo a passo</a>.
    </p>
  </div>
</section>
"""

DUVIDAS = """
<section class="cabeca">
  <div class="envolve envolve-estreito">
    <p class="olho">Dúvidas</p>
    <h1>Perguntas que todo mundo faz.</h1>
    <p class="chamada">Se a sua não estiver aqui, o programa gera um relatório
    <code>.txt</code> com o diagnóstico — dá para mandar para quem for te ajudar.</p>
  </div>
</section>

<section class="secao">
  <div class="envolve envolve-estreito">
    <details><summary>Preciso saber usar terminal?</summary>
      <p>Não. Você clica no arquivo de início e usa a janela. Os comandos de
      terminal existem para quem prefere, mas nada depende deles.</p></details>

    <details><summary>Preciso instalar o Tor?</summary>
      <p>Só se quiser usar a saída pelo Tor. Instale o
      <a href="https://www.torproject.org/pt-BR/download/">Tor Browser</a> e deixe
      a pasta em Downloads — o programa acha sozinho e usa apenas a parte de rede,
      sem abrir navegador nenhum. Você também pode usar um proxy próprio.</p></details>

    <details><summary>A imagem some quando eu envio. Por quê?</summary>
      <p>Porque a saída está lenta demais. Pelo Tor, enviar 2 MB leva uns 37
      segundos, e o Discord desiste antes de terminar — sem avisar. A janela do
      programa avisa quando uma conexão passa de 25 segundos: é esse o sinal.</p>
      <p>Enquanto precisar mandar arquivos, clique em <b>Encerrar sessão</b> e use
      o Discord normalmente, ou troque para um proxy mais rápido.</p></details>

    <details><summary>O Discord diz que já está aberto</summary>
      <p>Feche pelo ícone ao lado do relógio, com o botão direito, não só pela
      janela. Um Discord vivo ignora a troca de região, por isso o programa recusa
      continuar.</p></details>

    <details><summary>“O Tor não conseguiu abrir um circuito”</summary>
      <p>Se você escolheu um país, tente <b>Automático</b>: pode não haver servidor
      de saída disponível naquele país no momento. Na primeira vez o Tor também
      demora mais, porque baixa a lista de servidores.</p></details>

    <details><summary>O programa não achou meu Discord</summary>
      <p>Em <b>Ajustes avançados</b>, informe o caminho do programa. Isso acontece
      quando o Discord está instalado num lugar fora do comum.</p></details>

    <details><summary>Funciona com Flatpak ou Snap?</summary>
      <p>A troca de região sim. O ajuste de voz por UDP não: esses formatos isolam
      bibliotecas externas. O programa detecta e avisa em vez de fingir que
      funciona.</p></details>

    <details><summary>Posso ser banido por usar?</summary>
      <p>O programa não automatiza nada dentro do Discord, não altera o cliente e
      não burla limites da plataforma — ele só muda por onde a conexão passa, como
      faria uma VPN comum. Ainda assim, quem decide o que é permitido são os termos
      do Discord; a escolha é sua.</p></details>

    <details><summary>Como eu desfaço tudo?</summary>
      <p>Feche a janela do programa: o Discord volta a sair pela sua conexão normal
      na próxima vez que abrir. Para apagar atalhos e componentes, use o comando
      <code>clean</code>. Nada fica no sistema.</p></details>

    <details><summary>Deu erro e eu não entendi</summary>
      <p>Clique em <b>Salvar relatório (.txt)</b>. O arquivo vai para a sua Área de
      Trabalho com o sistema, o Discord encontrado, a configuração e o que
      aconteceu nas conexões. Sua senha aparece trocada por <code>***</code>.</p></details>
  </div>
</section>
"""

SEGURANCA = """
<section class="cabeca">
  <div class="envolve envolve-estreito">
    <p class="olho">Segurança</p>
    <h1>O que o programa faz — e o que ele <span class="destaque">não</span> faz.</h1>
    <p class="chamada">Prometer menos e cumprir vale mais que o contrário. Todo o
    código é aberto: dá para conferir cada afirmação desta página.</p>
  </div>
</section>

<section class="secao">
  <div class="envolve envolve-estreito">
    <h2>Não é o que parece</h2>
    <div class="honesto-grade">
      <div>
        <h3>Não é VPN</h3>
        <p>Só o Discord sai pelo outro país. Banco, streaming e jogos continuam na
        sua conexão de sempre — e é isso que a maioria das pessoas quer.</p>
      </div>
      <div>
        <h3>Não é anônimo</h3>
        <p>Trocar a região não te esconde de ninguém. A conta continua sendo a sua,
        e o servidor de saída vê o tráfego do seu Discord passar.</p>
      </div>
      <div>
        <h3>Não resolve tudo</h3>
        <p>Se a limitação vier da conta e não do IP, trocar o país não muda nada.
        Por isso o programa te mostra de onde você está saindo: para conferir em vez
        de adivinhar.</p>
      </div>
      <div>
        <h3>Não mexe no Discord</h3>
        <p>Não altera arquivos internos, não injeta certificado, não desliga
        verificação de segurança. Ele só abre o Discord com uma configuração de
        rede diferente.</p>
      </div>
    </div>
  </div>
</section>

<section class="secao secao-clara">
  <div class="envolve envolve-estreito">
    <h2>O cuidado com a sua senha</h2>
    <p class="secao-chamada">Se você usa um proxy com usuário e senha, ela precisa
    chegar ao servidor sem passar por onde não deve.</p>
    <ul class="lista">
      <li>A senha <b>nunca</b> vai para a linha de comando do Discord, nem para
      atalhos, nem para os registros.</li>
      <li>O Discord fala com uma ponte local em <code>127.0.0.1</code>, e é a ponte
      que se autentica no proxy de verdade.</li>
      <li>O arquivo de configuração é gravado com permissão só sua.</li>
      <li>O relatório de diagnóstico troca a senha por <code>***</code> — há um
      teste automatizado que garante isso.</li>
      <li>Você pode deixar a senha fora do arquivo, usando uma variável de
      ambiente: <code>socks5://usuario:${{MINHA_SENHA}}@servidor:1080</code>.</li>
    </ul>
  </div>
</section>

<section class="secao">
  <div class="envolve envolve-estreito">
    <h2>O que sai do seu computador</h2>
    <ul class="lista">
      <li><b>Nada é enviado para nós.</b> Não há servidor nosso, não há telemetria,
      não há conta para criar.</li>
      <li>Ao clicar em <b>Onde estou saindo</b>, o programa consulta um serviço
      público de geolocalização de IP para descobrir o país. Só quando você
      clica.</li>
      <li>O ajuste de voz por UDP vem <b>desligado</b>. Ele é para outro problema —
      rede que bloqueia voz por inspeção de pacote — e não tem efeito sobre
      região.</li>
      <li>No Windows, o ajuste de voz coloca um <code>version.dll</code> ao lado do
      Discord. A instalação guarda o hash do arquivo e a remoção só apaga se
      bater; um <code>version.dll</code> que não seja nosso nunca é
      sobrescrito.</li>
    </ul>

    <div class="aviso">
      <h3>Antivírus pode reclamar</h3>
      <p>Qualquer DLL ao lado de um executável chama atenção de antivírus, e o
      nosso não é exceção. Você pode conferir o hash publicado na release e ler o
      código antes de liberar — ou simplesmente deixar o ajuste de voz desligado,
      que é o padrão.</p>
    </div>

    <p class="secao-chamada" style="margin-top:32px">
      <a class="botao" href="{github}">Ler o código no GitHub</a>
    </p>
  </div>
</section>
"""

PAGINAS = [
    ("index.html", "inicio", "Discord Proxy — sua câmera e sua tela de volta",
     "Faz o Discord sair por outro país e recupera a câmera e o compartilhamento de tela. Grátis, código aberto, para Windows e Linux.",
     INICIO),
    ("como-usar.html", "como-usar", "Como usar — Discord Proxy",
     "Passo a passo, do download à primeira chamada. Sem terminal e sem instalador.",
     COMO_USAR),
    ("download.html", "download", "Download — Discord Proxy",
     "Baixe para Windows ou Linux. Extraia e abra: não há instalador nem cadastro.",
     DOWNLOAD),
    ("duvidas.html", "duvidas", "Dúvidas — Discord Proxy",
     "As perguntas mais comuns: Tor, imagens que somem, Discord já aberto e como desfazer tudo.",
     DUVIDAS),
    ("seguranca.html", "seguranca", "Segurança — Discord Proxy",
     "O que o programa faz e o que ele não faz, como a sua senha é tratada e o que sai do seu computador.",
     SEGURANCA),
]


def main() -> int:
    for arquivo, ativo, titulo, descricao, conteudo in PAGINAS:
        corpo = conteudo.format(github=GITHUB, win=BAIXAR_WIN, linux=BAIXAR_LINUX)
        html = MOLDE.format(
            titulo=titulo,
            descricao=descricao,
            menu=montar_menu(ativo),
            conteudo=corpo,
            github=GITHUB,
        )
        (RAIZ / arquivo).write_text(html, encoding="utf-8")
        print(f"  {arquivo}  ({len(html.splitlines())} linhas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
