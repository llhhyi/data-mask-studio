[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$PortableOnly,
    [switch]$Clean,
    [switch]$SkipSmokeTests,
    [switch]$SmokeTestsOnly,
    [switch]$VerboseOutput
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($env:OS -ne 'Windows_NT') {
    throw "Este build só pode ser executado no Windows."
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot '.tools\pyinstaller-cache'
. (Join-Path $PSScriptRoot 'windows_build_helpers.ps1')

function Write-BuildStep([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Get-BuildPython {
    if ($env:VIRTUAL_ENV) {
        $virtualPython = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
        if (Test-Path -LiteralPath $virtualPython -PathType Leaf) {
            return (Resolve-Path -LiteralPath $virtualPython).Path
        }
    }
    $projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $projectPython -PathType Leaf) {
        return (Resolve-Path -LiteralPath $projectPython).Path
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        $command = Get-Command python -ErrorAction SilentlyContinue
    }
    if ($null -eq $command) {
        throw "Python não foi encontrado no ambiente atual."
    }
    return $command.Source
}

function New-VersionInformationFile {
    param(
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if ($Version -notmatch '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)$') {
        throw "A versão deve usar o formato numérico MAJOR.MINOR.PATCH."
    }
    $major = [int]$Matches.major
    $minor = [int]$Matches.minor
    $patch = [int]$Matches.patch
    $content = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($major, $minor, $patch, 0),
    prodvers=($major, $minor, $patch, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Data Mask Studio'),
        StringStruct('FileDescription', 'Data Mask Studio'),
        StringStruct('FileVersion', '$Version'),
        StringStruct('InternalName', 'DataMaskStudio'),
        StringStruct('OriginalFilename', 'DataMaskStudio.exe'),
        StringStruct('ProductName', 'Data Mask Studio'),
        StringStruct('ProductVersion', '$Version')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Set-Content -LiteralPath $Destination -Value $content -Encoding UTF8
}

function Test-PortableStartup {
    param([Parameter(Mandatory = $true)][string]$Executable)

    $testRoot = Join-Path ([IO.Path]::GetTempPath()) ("DataMaskStudio-build-test-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $oldLocalAppData = $env:LOCALAPPDATA
    $oldQtPlatform = $env:QT_QPA_PLATFORM
    $oldSmokeReport = $env:DMS_BUILD_SMOKE_REPORT
    $process = $null
    try {
        $env:LOCALAPPDATA = $testRoot
        $env:QT_QPA_PLATFORM = 'offscreen'
        $env:DMS_BUILD_SMOKE_REPORT = Join-Path $testRoot 'packaging-smoke.json'
        $stdoutPath = Join-Path $testRoot 'stdout.log'
        $stderrPath = Join-Path $testRoot 'stderr.log'
        $process = Start-Process -FilePath $Executable -WorkingDirectory (Split-Path -Parent $Executable) -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        $deadline = (Get-Date).AddSeconds(45)
        while ((Get-Date) -lt $deadline -and -not $process.HasExited -and -not (Test-Path -LiteralPath $env:DMS_BUILD_SMOKE_REPORT)) {
            Start-Sleep -Milliseconds 200
            $process.Refresh()
        }
        if (-not (Test-Path -LiteralPath $env:DMS_BUILD_SMOKE_REPORT)) {
            $detail = if (Test-Path -LiteralPath $stderrPath) { [IO.File]::ReadAllText($stderrPath) } else { '' }
            throw "Frozen application did not confirm successful initialization: $detail"
        }
        $report = Get-Content -LiteralPath $env:DMS_BUILD_SMOKE_REPORT -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Output ($report | ConvertTo-Json -Compress)
        if ($report.status -ne 'passed') { throw 'Frozen feature probe failed.' }
        if (-not $process.WaitForExit(10000) -or $process.ExitCode -ne 0) { throw 'Frozen probe did not exit cleanly.' }
    }
    finally {
        if ($null -ne $process -and -not $process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
        $env:LOCALAPPDATA = $oldLocalAppData
        if ($null -eq $oldSmokeReport) { Remove-Item Env:DMS_BUILD_SMOKE_REPORT -ErrorAction SilentlyContinue }
        else { $env:DMS_BUILD_SMOKE_REPORT = $oldSmokeReport }
        if ($null -eq $oldQtPlatform) {
            Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        }
        else {
            $env:QT_QPA_PLATFORM = $oldQtPlatform
        }
        $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
        $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if ($resolvedTestRoot.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedTestRoot)) {
            Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
        }
    }
}

function Invoke-SilentInstaller {
    param(
        [Parameter(Mandatory = $true)][string]$Installer,
        [Parameter(Mandatory = $true)][string]$InstallDirectory
    )
    $process = Start-Process -FilePath $Installer -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
        '/NOICONS', '/TASKS=', ('/DIR="' + $InstallDirectory + '"')
    ) -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "O smoke test do instalador falhou com código $($process.ExitCode)."
    }
}

function Invoke-SilentUninstaller {
    param([Parameter(Mandatory = $true)][string]$Uninstaller)
    $process = Start-Process -FilePath $Uninstaller -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'
    ) -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "O smoke test do desinstalador falhou com código $($process.ExitCode)."
    }
}

function Test-InstallerLifecycle {
    param([Parameter(Mandatory = $true)][string]$Installer)

    $testRoot = Join-Path ([IO.Path]::GetTempPath()) ("DataMaskStudio-installer-test-" + [guid]::NewGuid())
    $installDirectory = Join-Path $testRoot 'program'
    $isolatedLocalAppData = Join-Path $testRoot 'localappdata'
    $dataDirectory = Join-Path $isolatedLocalAppData 'DataMaskStudio'
    $preservationMarker = Join-Path $dataDirectory 'synthetic-preservation.marker'
    $installedExecutable = Join-Path $installDirectory 'DataMaskStudio.exe'
    $uninstaller = Join-Path $installDirectory 'unins000.exe'
    $oldLocalAppData = $env:LOCALAPPDATA
    $uninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A99ABF1D-AF79-4F18-9C01-AF80EC65B100}_is1'
    foreach ($hive in @('HKCU:', 'HKLM:')) {
        if (Test-Path -LiteralPath ($hive + '\' + $uninstallKey)) {
            throw 'Team edition is already installed; refuse to change an existing installation during the build smoke test.'
        }
    }
    try {
        New-Item -ItemType Directory -Path $isolatedLocalAppData -Force | Out-Null
        $env:LOCALAPPDATA = $isolatedLocalAppData

        Invoke-SilentInstaller -Installer $Installer -InstallDirectory $installDirectory
        if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) {
            throw 'A instalação limpa não criou o executável esperado.'
        }
        Test-PortableStartup -Executable $installedExecutable
        New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
        Set-Content -LiteralPath $preservationMarker -Value 'synthetic' -Encoding ASCII

        Invoke-SilentInstaller -Installer $Installer -InstallDirectory $installDirectory
        if (-not (Test-Path -LiteralPath $preservationMarker -PathType Leaf)) {
            throw 'A atualização não preservou o ambiente local isolado.'
        }

        Invoke-SilentUninstaller -Uninstaller $uninstaller
        if (Test-Path -LiteralPath $installedExecutable -PathType Leaf) {
            throw 'A desinstalação não removeu o executável instalado.'
        }
        if (-not (Test-Path -LiteralPath $preservationMarker -PathType Leaf)) {
            throw 'A desinstalação removeu dados locais que deveriam ser preservados.'
        }

        Invoke-SilentInstaller -Installer $Installer -InstallDirectory $installDirectory
        if (-not (Test-Path -LiteralPath $preservationMarker -PathType Leaf)) {
            throw 'A reinstalação não reconheceu o ambiente local preservado.'
        }
        Invoke-SilentUninstaller -Uninstaller $uninstaller
    }
    finally {
        $env:LOCALAPPDATA = $oldLocalAppData
        $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
        $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if ($resolvedTestRoot.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedTestRoot)) {
            Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
        }
    }
}

$python = Get-BuildPython
$version = Get-DataMaskStudioProjectVersion -ProjectRoot $projectRoot
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Versão inválida no pyproject.toml: $version"
}
$artifactNames = Get-DataMaskStudioArtifactNames -Version $version
Write-Host "Versão confirmada pelo pyproject.toml: $version"
Write-Host "Python: $python"

if ($SmokeTestsOnly) {
    $portableDirectory = Join-Path $projectRoot 'dist\DataMaskStudio'
    $installerPath = Join-Path (Join-Path $projectRoot 'release') $artifactNames.Installer
    Assert-DataMaskStudioBuildIsSafe -Root $portableDirectory
    Test-PortableStartup -Executable (Join-Path $portableDirectory 'DataMaskStudio.exe')
    if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) { throw 'Expected installer is missing.' }
    Test-InstallerLifecycle -Installer $installerPath
    Write-Output 'PASS: frozen startup, clean install, upgrade, uninstall, reinstall, and isolated data preservation.'
    return
}

Write-BuildStep 'Sincronizando metadados locais do projeto'
& $python -m pip install --no-deps --no-build-isolation -e $projectRoot --no-index
if ($LASTEXITCODE -ne 0) {
    throw 'Não foi possível sincronizar os metadados do projeto no ambiente de build.'
}
$installedVersion = (& $python -c "from importlib.metadata import version; print(version('data-mask-studio'))").Trim()
if ($installedVersion -ne $version) {
    throw "Versão instalada ($installedVersion) difere do pyproject.toml ($version)."
}

& $python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller não está instalado. Execute: python -m pip install -e ".[dev]"'
}

if (-not $SkipTests) {
    Write-BuildStep 'Executando a suíte de testes'
    $oldQtPlatform = $env:QT_QPA_PLATFORM
    $oldBytecode = $env:PYTHONDONTWRITEBYTECODE
    $oldLocalAppData = $env:LOCALAPPDATA
    $isolatedTestRoot = Join-Path ([IO.Path]::GetTempPath()) ("DataMaskStudio-pytest-" + [guid]::NewGuid())
    try {
        New-Item -ItemType Directory -Path $isolatedTestRoot -Force | Out-Null
        $env:QT_QPA_PLATFORM = 'offscreen'
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $env:LOCALAPPDATA = Join-Path $isolatedTestRoot 'localappdata'
        New-Item -ItemType Directory -Path $env:LOCALAPPDATA -Force | Out-Null
        & $python -m pytest -q -p no:cacheprovider --basetemp (Join-Path $isolatedTestRoot 'pytest')
        if ($LASTEXITCODE -ne 0) {
            throw "A suíte de testes falhou."
        }
    }
    finally {
        $env:QT_QPA_PLATFORM = $oldQtPlatform
        $env:PYTHONDONTWRITEBYTECODE = $oldBytecode
        $env:LOCALAPPDATA = $oldLocalAppData
        if (Test-Path -LiteralPath $isolatedTestRoot) {
            Remove-Item -LiteralPath $isolatedTestRoot -Recurse -Force
        }
    }
}

Write-BuildStep 'Limpando artefatos anteriores'
& (Join-Path $PSScriptRoot 'clean_build.ps1') -IncludeRelease:$Clean

$buildRoot = Join-Path $projectRoot 'build'
$distRoot = Join-Path $projectRoot 'dist'
$releaseRoot = Join-Path $projectRoot 'release'
$portableDirectory = Join-Path $distRoot 'DataMaskStudio'
$executable = Join-Path $portableDirectory 'DataMaskStudio.exe'
$zipPath = Join-Path $releaseRoot $artifactNames.PortableZip
$installerPath = Join-Path $releaseRoot $artifactNames.Installer
$versionFile = Join-Path $buildRoot 'windows\DataMaskStudio-version.txt'
$iconFile = Join-Path $projectRoot 'assets\branding\dms_icon.ico'
$specFile = Join-Path $projectRoot 'packaging\windows\DataMaskStudio.spec'
$innoScript = Join-Path $projectRoot 'packaging\windows\DataMaskStudio.iss'
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
foreach ($oldArtifact in @($zipPath, $installerPath)) {
    if (Test-Path -LiteralPath $oldArtifact) {
        Remove-Item -LiteralPath $oldArtifact -Force
    }
}

New-VersionInformationFile -Version $version -Destination $versionFile
if (-not (Test-Path -LiteralPath $iconFile -PathType Leaf)) {
    throw "Ícone oficial obrigatório não encontrado: $iconFile"
}
Write-Host "Ícone oficial confirmado: $iconFile"

Write-BuildStep 'Gerando a aplicação portátil com PyInstaller'
$oldVersionFile = $env:DMS_VERSION_FILE
$oldIconFile = $env:DMS_ICON_FILE
try {
    $env:DMS_VERSION_FILE = $versionFile
    $env:DMS_ICON_FILE = $iconFile
    $logLevel = if ($VerboseOutput) { 'INFO' } else { 'WARN' }
    & $python -m PyInstaller --noconfirm --clean --log-level $logLevel --distpath $distRoot --workpath (Join-Path $buildRoot 'pyinstaller') $specFile
    if ($LASTEXITCODE -ne 0) {
        throw "O PyInstaller não concluiu o build."
    }
}
finally {
    $env:DMS_VERSION_FILE = $oldVersionFile
    $env:DMS_ICON_FILE = $oldIconFile
}

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "O executável esperado não foi criado: $executable"
}
Assert-DataMaskStudioBuildIsSafe -Root $portableDirectory

Write-BuildStep 'Preparing team instructions and license notices'
& $python (Join-Path $PSScriptRoot 'prepare_team_payload.py') --destination $portableDirectory
if ($LASTEXITCODE -ne 0) { throw 'Team payload preparation failed.' }
Assert-DataMaskStudioBuildIsSafe -Root $portableDirectory

if (-not $SkipSmokeTests) {
    Write-BuildStep 'Validando a inicialização do executável'
    Test-PortableStartup -Executable $executable
}
else {
    Write-Host 'Smoke test do executável ignorado explicitamente nesta execução.'
}

Write-BuildStep 'Criando o ZIP portátil'
New-DataMaskStudioPortableArchive -PortableDirectory $portableDirectory -DestinationPath $zipPath | Out-Null

$zipAuditRoot = Join-Path ([IO.Path]::GetTempPath()) ("DataMaskStudio-zip-audit-" + [guid]::NewGuid())
try {
    Expand-Archive -LiteralPath $zipPath -DestinationPath $zipAuditRoot
    Assert-DataMaskStudioBuildIsSafe -Root $zipAuditRoot
}
finally {
    $resolvedAuditRoot = [IO.Path]::GetFullPath($zipAuditRoot)
    $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($resolvedAuditRoot.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedAuditRoot)) {
        Remove-Item -LiteralPath $resolvedAuditRoot -Recurse -Force
    }
}

$installerCreated = $false
$innoCompiler = $null
if (-not $PortableOnly) {
    $innoCompiler = Find-InnoSetupCompiler
    if ($null -eq $innoCompiler) {
        Write-Warning "Inno Setup não foi encontrado. O portátil e o ZIP foram gerados normalmente."
        Write-Host "Instale o Inno Setup 6 manualmente e execute este script novamente para gerar o instalador."
    }
    else {
        Write-BuildStep 'Gerando o instalador com Inno Setup'
        $innoArguments = @(
            "/DMyAppVersion=$version",
            "/DBuildRoot=$portableDirectory",
            "/DReleaseRoot=$releaseRoot"
        )
        $innoArguments += "/DDmsIconFile=$iconFile"
        $innoArguments += $innoScript
        & $innoCompiler @innoArguments
        if ($LASTEXITCODE -ne 0) {
            throw "O Inno Setup não concluiu a geração do instalador."
        }
        if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
            throw "O instalador esperado não foi criado: $installerPath"
        }
        if (-not $SkipSmokeTests) {
            Write-BuildStep 'Validando instalação limpa, atualização e reinstalação'
            Test-InstallerLifecycle -Installer $installerPath
        }
        else {
            Write-Host 'Smoke test do instalador ignorado explicitamente nesta execução.'
        }
        $installerCreated = $true
    }
}

Write-BuildStep 'Resumo do build'
$zipSizeMb = [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 1)
Write-Host "Aplicação portátil: $portableDirectory"
Write-Host "ZIP: $zipPath ($zipSizeMb MB)"
if ($installerCreated) {
    $installerSizeMb = [math]::Round((Get-Item -LiteralPath $installerPath).Length / 1MB, 1)
    Write-Host "Instalador: $installerPath ($installerSizeMb MB)"
}
elseif ($PortableOnly) {
    Write-Host "Instalador: ignorado por -PortableOnly"
}
else {
    Write-Host "Instalador: não gerado (Inno Setup ausente)"
}
Write-Host "Nenhum dado de %LOCALAPPDATA%\DataMaskStudio foi incluído."
