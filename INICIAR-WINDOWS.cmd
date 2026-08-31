@echo off
chcp 65001 > nul
title Discord Proxy
setlocal enabledelayedexpansion
set "AQUI=%~dp0"

rem ---- programa pronto (pacote da pagina de releases) ----
if exist "%AQUI%DiscordProxy.exe" (
    start "" "%AQUI%DiscordProxy.exe" %*
    goto :fim
)
if exist "%AQUI%programa\DiscordProxy.exe" (
    start "" "%AQUI%programa\DiscordProxy.exe" %*
    goto :fim
)
rem pacotes antigos guardavam o programa numa pasta com ponto na frente
if exist "%AQUI%.discord-proxy\DiscordProxy.exe" (
    start "" "%AQUI%.discord-proxy\DiscordProxy.exe" %*
    goto :fim
)

rem ---- rodando pelo codigo-fonte: precisa do Python ----
if exist "%AQUI%discord_proxy\__main__.py" (
    where python >nul 2>nul
    if errorlevel 1 (
        echo.
        echo  O Python nao foi encontrado neste computador.
        echo.
        echo  Baixe em https://www.python.org/downloads/
        echo  Na instalacao, marque a caixa "Add Python to PATH".
        echo.
        pause
        exit /b 1
    )
    pushd "%AQUI%"
    python -m discord_proxy %*
    set "CODIGO=!errorlevel!"
    popd
    if not "!CODIGO!"=="0" (
        echo.
        echo  O programa terminou com erro ^(codigo !CODIGO!^).
        echo  Para gerar um relatorio, rode:  INICIAR-WINDOWS.cmd relatorio
        echo.
        pause
    )
    goto :fim
)

echo.
echo  Nao encontrei o programa nesta pasta.
echo  Extraia o arquivo .zip INTEIRO antes de abrir, e nao mova este arquivo
echo  para fora da pasta.
echo.
pause
exit /b 1

:fim
endlocal
