@echo off
setlocal EnableExtensions

set "DLP_INSTALLER_ROOT=%~dp0"
set "DLP_EASY_LAUNCHER=%DLP_INSTALLER_ROOT%INICIAR-WINDOWS.cmd"

if not exist "%DLP_EASY_LAUNCHER%" goto missing
if not "%~1"=="" goto with_arguments

call "%DLP_EASY_LAUNCHER%" gui
exit /b %ERRORLEVEL%

:with_arguments
call "%DLP_EASY_LAUNCHER%" %*
exit /b %ERRORLEVEL%

:missing
powershell.exe -NoProfile -NonInteractive -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('INICIAR-WINDOWS.cmd nao foi encontrado. Extraia todo o pacote antes de instalar.', 'Discord Local Proxy', [System.Windows.MessageBoxButton]::OK, [System.Windows.MessageBoxImage]::Error)" >nul 2>&1
if errorlevel 1 (
    echo INICIAR-WINDOWS.cmd nao foi encontrado.
    echo Extraia todo o pacote antes de instalar.
    pause
)
exit /b 1
