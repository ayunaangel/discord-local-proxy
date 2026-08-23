@echo off
setlocal EnableExtensions

set "DLP_ROOT=%~dp0"
set "DLP_INTERNAL=%DLP_ROOT%.discord-local-proxy"
set "DLP_LAUNCHER=%DLP_ROOT%INICIAR-WINDOWS.cmd"

if exist "%DLP_INTERNAL%" attrib +h "%DLP_INTERNAL%" >nul 2>&1
if not exist "%DLP_LAUNCHER%" goto missing

if not "%~1"=="" goto with_arguments
call "%DLP_LAUNCHER%" gui
exit /b %ERRORLEVEL%

:with_arguments
call "%DLP_LAUNCHER%" %*
exit /b %ERRORLEVEL%

:missing
powershell.exe -NoProfile -NonInteractive -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('INICIAR-WINDOWS.cmd nao foi encontrado ao lado deste instalador.', 'Discord Local Proxy', [System.Windows.MessageBoxButton]::OK, [System.Windows.MessageBoxImage]::Error)" >nul 2>&1
if errorlevel 1 (
    echo INICIAR-WINDOWS.cmd nao foi encontrado.
    pause
)
exit /b 1
