@echo off
setlocal EnableExtensions

set "DLP_ROOT=%~dp0"
set "DLP_BINARY="

if exist "%DLP_ROOT%DiscordLocalProxy.exe" set "DLP_BINARY=%DLP_ROOT%DiscordLocalProxy.exe"
if not defined DLP_BINARY if exist "%DLP_ROOT%dist\DiscordLocalProxy.exe" set "DLP_BINARY=%DLP_ROOT%dist\DiscordLocalProxy.exe"

if defined DLP_BINARY goto run_binary

pushd "%DLP_ROOT%" >nul
if not exist "%DLP_ROOT%discord_local_proxy\__main__.py" goto missing
if "%~1"=="" goto source_gui

where py.exe >nul 2>&1
if not errorlevel 1 goto source_cli_py

where python.exe >nul 2>&1
if not errorlevel 1 goto source_cli_python
goto missing

:source_gui
where pyw.exe >nul 2>&1
if not errorlevel 1 goto source_gui_pyw

where pythonw.exe >nul 2>&1
if not errorlevel 1 goto source_gui_pythonw

where py.exe >nul 2>&1
if not errorlevel 1 goto source_gui_py

where python.exe >nul 2>&1
if not errorlevel 1 goto source_gui_python
goto missing

:source_cli_py
py.exe -3 -m discord_local_proxy %*
set "DLP_EXIT=%ERRORLEVEL%"
popd
exit /b %DLP_EXIT%

:source_cli_python
python.exe -m discord_local_proxy %*
set "DLP_EXIT=%ERRORLEVEL%"
popd
exit /b %DLP_EXIT%

:source_gui_pyw
start "" pyw.exe -3 -m discord_local_proxy
popd
exit /b 0

:source_gui_pythonw
start "" pythonw.exe -m discord_local_proxy
popd
exit /b 0

:source_gui_py
start "" py.exe -3 -m discord_local_proxy
popd
exit /b 0

:source_gui_python
start "" python.exe -m discord_local_proxy
popd
exit /b 0

:run_binary
if "%~1"=="" (
    start "" "%DLP_BINARY%"
    exit /b 0
)
"%DLP_BINARY%" %*
exit /b %ERRORLEVEL%

:missing
popd
powershell.exe -NoProfile -NonInteractive -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('DiscordLocalProxy.exe ou Python 3 nao foi encontrado nesta pasta. Extraia todo o pacote antes de iniciar.', 'Discord Local Proxy', [System.Windows.MessageBoxButton]::OK, [System.Windows.MessageBoxImage]::Error)" >nul 2>&1
if errorlevel 1 (
    echo DiscordLocalProxy.exe ou Python 3 nao foi encontrado nesta pasta.
    echo Extraia todo o pacote antes de iniciar.
    pause
)
exit /b 1
