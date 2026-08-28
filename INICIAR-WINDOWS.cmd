@echo off
rem Abre o Discord Proxy. Sem argumentos, abre a janela de configuracao.
setlocal
set "AQUI=%~dp0"

if exist "%AQUI%DiscordProxy.exe" (
    "%AQUI%DiscordProxy.exe" %*
    goto :fim
)
if exist "%AQUI%.discord-proxy\DiscordProxy.exe" (
    "%AQUI%.discord-proxy\DiscordProxy.exe" %*
    goto :fim
)
if exist "%AQUI%discord_proxy\__main__.py" (
    pushd "%AQUI%"
    python -m discord_proxy %*
    popd
    goto :fim
)

echo Nao encontrei o programa. Extraia o pacote inteiro antes de abrir.
pause
exit /b 1

:fim
if errorlevel 1 pause
endlocal
