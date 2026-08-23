$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildDir = Join-Path $ProjectRoot "build\native"
$PackageNative = Join-Path $ProjectRoot "discord_local_proxy\native"

function Assert-Succeeded {
    param(
        [Parameter(Mandatory = $true)][string]$Step,
        [Parameter(Mandatory = $true)][int]$ExitCode
    )
    if ($ExitCode -ne 0) {
        throw "$Step falhou com código de saída $ExitCode."
    }
}

cmake -S (Join-Path $ProjectRoot "native") -B $BuildDir -A x64
Assert-Succeeded "A configuração do CMake" $LASTEXITCODE
cmake --build $BuildDir --config Release --parallel
Assert-Succeeded "A compilação nativa" $LASTEXITCODE
cmake --install $BuildDir --config Release --prefix $PackageNative
Assert-Succeeded "A instalação da biblioteca nativa" $LASTEXITCODE

python -m unittest discover -s (Join-Path $ProjectRoot "tests") -v
Assert-Succeeded "A suíte de testes" $LASTEXITCODE
python -m PyInstaller --noconfirm (Join-Path $ProjectRoot "DiscordLocalProxy.spec")
Assert-Succeeded "O empacotamento com PyInstaller" $LASTEXITCODE
$GuiRuntimeCheck = Start-Process -FilePath (Join-Path $ProjectRoot "dist\DiscordLocalProxy.exe") -ArgumentList "check-gui" -Wait -PassThru
if ($GuiRuntimeCheck.ExitCode -ne 0) {
    throw "O executável empacotado não conseguiu importar o runtime gráfico Tk."
}
Copy-Item (Join-Path $ProjectRoot "INICIAR-WINDOWS.cmd") (Join-Path $ProjectRoot "dist\INICIAR-WINDOWS.cmd") -Force
Copy-Item (Join-Path $ProjectRoot "INSTALAR-WINDOWS.cmd") (Join-Path $ProjectRoot "dist\INSTALAR-WINDOWS.cmd") -Force

$MinHookLicense = Join-Path $BuildDir "_deps\minhook-src\LICENSE.txt"
if (Test-Path $MinHookLicense) {
    Copy-Item $MinHookLicense (Join-Path $ProjectRoot "dist\MINHOOK-LICENSE.txt") -Force
}

Copy-Item (Join-Path $ProjectRoot "LICENSE") (Join-Path $ProjectRoot "dist\LICENSE.txt") -Force
Copy-Item (Join-Path $ProjectRoot "NOTICE.md") (Join-Path $ProjectRoot "dist\NOTICE.md") -Force

$ReleaseRoot = Join-Path $ProjectRoot "release"
$ReleaseDir = Join-Path $ReleaseRoot "DiscordLocalProxy-Windows-x64"
$InternalDir = Join-Path $ReleaseDir ".discord-local-proxy"
$Archive = Join-Path $ReleaseRoot "DiscordLocalProxy-Windows-x64.zip"

if (Test-Path $ReleaseDir) {
    Remove-Item $ReleaseDir -Recurse -Force
}
if (Test-Path $Archive) {
    Remove-Item $Archive -Force
}
New-Item $InternalDir -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $ProjectRoot "packaging\windows\INICIAR-WINDOWS.cmd") (Join-Path $ReleaseDir "INICIAR-WINDOWS.cmd")
Copy-Item (Join-Path $ProjectRoot "packaging\windows\INSTALAR-WINDOWS.cmd") (Join-Path $ReleaseDir "INSTALAR-WINDOWS.cmd")
Copy-Item (Join-Path $ProjectRoot "dist\DiscordLocalProxy.exe") (Join-Path $InternalDir "DiscordLocalProxy.exe")
Copy-Item (Join-Path $ProjectRoot "LICENSE") (Join-Path $InternalDir "LICENSE.txt")
Copy-Item (Join-Path $ProjectRoot "NOTICE.md") (Join-Path $InternalDir "NOTICE.md")
if (Test-Path $MinHookLicense) {
    Copy-Item $MinHookLicense (Join-Path $InternalDir "MINHOOK-LICENSE.txt")
}
Compress-Archive -Path $ReleaseDir -DestinationPath $Archive -CompressionLevel Optimal
Remove-Item $ReleaseDir -Recurse -Force
Write-Host "Pacote Windows criado em $Archive"
