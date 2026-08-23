@echo off
setlocal EnableExtensions

set "DLP_ROOT=%~dp0"
set "DLP_INTERNAL=%DLP_ROOT%.discord-local-proxy"
set "DLP_BINARY=%DLP_INTERNAL%\DiscordLocalProxy.exe"

if exist "%DLP_INTERNAL%" attrib +h "%DLP_INTERNAL%" >nul 2>&1
if not exist "%DLP_BINARY%" goto missing

if "%~1"=="" (
    start "" "%DLP_BINARY%"
    exit /b 0
)
"%DLP_BINARY%" %*
exit /b %ERRORLEVEL%

:missing
powershell.exe -NoProfile -NonInteractive -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('Os arquivos internos do Discord Local Proxy nao foram encontrados. Mantenha a pasta .discord-local-proxy ao lado deste iniciador.', 'Discord Local Proxy', [System.Windows.MessageBoxButton]::OK, [System.Windows.MessageBoxImage]::Error)" >nul 2>&1
if errorlevel 1 (
    echo Os arquivos internos do Discord Local Proxy nao foram encontrados.
    pause
)
exit /b 1
