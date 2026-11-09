$ErrorActionPreference = 'Stop'
$projectDirectory = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw 'The local Python environment is missing.'
}

$desktopDirectory = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopDirectory 'Data Mask Studio 中文版.lnk'
if (-not (Test-Path -LiteralPath $shortcutPath)) {
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $pythonExecutable
    $shortcut.Arguments = '-m data_mask_studio'
    $shortcut.WorkingDirectory = $projectDirectory
    $shortcut.IconLocation = (Join-Path $projectDirectory 'assets\branding\dms_icon.ico')
    $shortcut.Description = '本地数据脱敏与还原（中文修改版）'
    $shortcut.Save()
}

$application = Start-Process -FilePath $pythonExecutable -ArgumentList '-m', 'data_mask_studio' -WorkingDirectory $projectDirectory -WindowStyle Normal -PassThru
[pscustomobject]@{ Shortcut = $shortcutPath; LauncherProcessId = $application.Id }
