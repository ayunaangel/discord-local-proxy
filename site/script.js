/* Discord Proxy — o mínimo de JavaScript: o site funciona sem ele.
   Faz três coisas: sombra no topo ao rolar, destaque do download certo para o
   sistema de quem visita, e uma entrada suave nas seções. */

(function () {
  "use strict";

  // ---- sombra no topo quando a página sai do começo
  var topo = document.querySelector(".topo");
  if (topo) {
    var marcarRolagem = function () {
      topo.classList.toggle("rolou", window.scrollY > 8);
    };
    marcarRolagem();
    window.addEventListener("scroll", marcarRolagem, { passive: true });
  }

  // ---- qual sistema está visitando
  function sistemaProvavel() {
    var dados = navigator.userAgentData;
    var plataforma = (dados && dados.platform) || navigator.platform || "";
    var agente = navigator.userAgent || "";
    var texto = (plataforma + " " + agente).toLowerCase();

    if (texto.indexOf("android") >= 0) return "android";
    if (/iphone|ipad|ipod/.test(texto)) return "ios";
    if (texto.indexOf("win") >= 0) return "windows";
    if (texto.indexOf("mac") >= 0) return "mac";
    if (texto.indexOf("linux") >= 0 || texto.indexOf("x11") >= 0) return "linux";
    return "";
  }

  var sistema = sistemaProvavel();
  var alvos = {
    windows: document.getElementById("dl-windows"),
    linux: document.getElementById("dl-linux")
  };
  var recomendado = alvos[sistema];
  if (recomendado) {
    recomendado.classList.add("recomendado");
    // o download do sistema certo vem primeiro na lista
    var lista = recomendado.parentNode;
    if (lista && lista.firstChild !== recomendado) {
      lista.insertBefore(recomendado, lista.firstChild);
    }
  }

  // ---- o botão principal fala o nome do sistema
  var rotulos = {
    windows: ["Baixar para Windows", "arquivo .zip · grátis"],
    linux: ["Baixar para Linux", "arquivo .tar.gz · grátis"],
    mac: ["Baixar o programa", "no macOS, só a troca de região"],
    android: ["Ver os downloads", "o programa é para computador"],
    ios: ["Ver os downloads", "o programa é para computador"]
  };
  var rotulo = rotulos[sistema];
  if (rotulo) {
    var texto = document.getElementById("botao-principal-texto");
    var sub = document.getElementById("botao-principal-sub");
    if (texto) texto.textContent = rotulo[0];
    if (sub) sub.textContent = rotulo[1];
  }

  // ---- entrada suave, respeitando quem pediu menos movimento
  var querMenosMovimento =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (!querMenosMovimento && "IntersectionObserver" in window) {
    var alvosAnimados = document.querySelectorAll(
      ".cartao, .passos li, .download, .honesto-grade > div, .faixa-grade > div"
    );
    var observador = new IntersectionObserver(
      function (entradas) {
        entradas.forEach(function (entrada, indice) {
          if (!entrada.isIntersecting) return;
          var atraso = Math.min(indice * 70, 280);
          setTimeout(function () {
            entrada.target.classList.add("visivel");
          }, atraso);
          observador.unobserve(entrada.target);
        });
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.1 }
    );
    alvosAnimados.forEach(function (alvo) {
      alvo.classList.add("aparece");
      observador.observe(alvo);
    });
  }
})();
