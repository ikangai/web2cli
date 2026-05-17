# WebCLIBridge installer for Windows
# Usage: irm https://raw.githubusercontent.com/ikangai/web2cli/main/install.ps1 | iex
# See: https://github.com/ikangai/web2cli

& {
    $ErrorActionPreference = 'Stop'

    $Repo         = 'ikangai/web2cli'
    $AppName      = 'WebCLIBridge'
    $AssetName    = 'WebCLIBridge-windows-x64.zip'
    $InstallDir   = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
    $Target       = Join-Path $InstallDir "$AppName.exe"
    $ShortcutPath = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$AppName.lnk"

    function Fail($msg) {
        Write-Host "error: $msg" -ForegroundColor Red
        exit 1
    }

    if ($PSVersionTable.PSEdition -eq 'Core' -and -not $IsWindows) {
        Fail 'Windows only'
    }
    if ($PSVersionTable.PSVersion -lt [Version]'5.1') {
        Fail 'PowerShell 5.1+ required'
    }

    if ($env:WEB2CLI_UNINSTALL -eq '1') {
        Write-Host "Uninstalling $AppName" -ForegroundColor Cyan
        Get-Process -Name $AppName -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep -Milliseconds 500
        if (Test-Path $ShortcutPath) {
            Remove-Item -Force $ShortcutPath
        }
        if (Test-Path $InstallDir) {
            Remove-Item -Recurse -Force $InstallDir
            Write-Host "Removed $InstallDir. Config left at %LOCALAPPDATA%\$AppName\." -ForegroundColor Green
        } else {
            Write-Host 'Nothing to uninstall.' -ForegroundColor Green
        }
        return
    }

    Write-Host "Installing $AppName" -ForegroundColor Cyan
    Write-Host 'Looking up latest release...'

    $apiUrl = "https://api.github.com/repos/$Repo/releases/latest"
    try {
        $release = Invoke-RestMethod $apiUrl -Headers @{ 'User-Agent' = 'web2cli-installer' }
    } catch {
        if ($_.Exception.Response.StatusCode.value__ -eq 404) {
            Fail "no release published yet for $Repo. See https://github.com/$Repo/releases"
        }
        Fail "could not fetch ${apiUrl}: $($_.Exception.Message)"
    }

    $version = $release.tag_name
    if (-not $version) { Fail 'release JSON has no tag_name' }

    $asset = $release.assets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1
    if (-not $asset) { Fail "release $version has no $AssetName asset" }

    Write-Host "Found $version"

    $tmpDir = Join-Path $env:TEMP ("web2cli-" + [guid]::NewGuid().ToString())
    New-Item -ItemType Directory -Path $tmpDir | Out-Null

    try {
        Write-Host 'Downloading...'
        $zipPath = Join-Path $tmpDir $AssetName
        Invoke-WebRequest $asset.browser_download_url -OutFile $zipPath -UseBasicParsing

        Write-Host 'Extracting...'
        Expand-Archive -Path $zipPath -DestinationPath $tmpDir -Force
        $extractedExe = Join-Path $tmpDir "$AppName.exe"
        if (-not (Test-Path $extractedExe)) {
            Fail "unexpected archive contents (no $AppName.exe)"
        }

        $running = Get-Process -Name $AppName -ErrorAction SilentlyContinue
        if ($running) {
            Write-Host 'Existing install found, stopping it...'
            $running | Stop-Process -Force
            Start-Sleep -Milliseconds 500
        }

        if (-not (Test-Path $InstallDir)) {
            New-Item -ItemType Directory -Path $InstallDir | Out-Null
        }
        if (Test-Path $Target) {
            Remove-Item $Target -Force
        }
        Move-Item -Path $extractedExe -Destination $Target

        Unblock-File -Path $Target

        Write-Host 'Creating Start Menu shortcut...'
        $wshell = New-Object -ComObject WScript.Shell
        $shortcut = $wshell.CreateShortcut($ShortcutPath)
        $shortcut.TargetPath = $Target
        $shortcut.WorkingDirectory = $InstallDir
        $shortcut.Description = 'web2cli localhost HTTP-to-CLI bridge'
        $shortcut.Save()

        Write-Host 'Launching...'
        Start-Process $Target

        Write-Host "Installed $AppName $version. Look for the bolt icon in your system tray." -ForegroundColor Green
    }
    finally {
        Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
    }
}
