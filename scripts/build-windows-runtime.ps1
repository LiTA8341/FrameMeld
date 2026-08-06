[CmdletBinding()]
param(
    [string]$OutputDirectory = "",
    [string]$CacheDirectory = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $RepoRoot "dist\framemeld-runtime" }
if (-not $CacheDirectory) { $CacheDirectory = Join-Path $RepoRoot ".cache\downloads" }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$CacheDirectory = [IO.Path]::GetFullPath($CacheDirectory)
$Manifest = Get-Content -Raw (Join-Path $RepoRoot "config\windows-runtime.json") | ConvertFrom-Json
$SevenZip = "C:\Program Files\7-Zip\7z.exe"
if (-not (Test-Path -LiteralPath $SevenZip)) {
    $SevenZip = (Get-Command 7z.exe -ErrorAction SilentlyContinue).Source
}
if (-not $SevenZip) { throw "7-Zip is required to build the runtime." }

function Assert-SafeOutput([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $driveRoot = [IO.Path]::GetPathRoot($resolved).TrimEnd('\')
    if ($resolved -eq $driveRoot -or $resolved.Length -lt ($driveRoot.Length + 8)) {
        throw "Refusing to replace unsafe output path: $resolved"
    }
}

function Assert-Hash([string]$Path, [string]$Expected) {
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "SHA-256 mismatch for $Path`nexpected: $Expected`nactual:   $actual"
    }
}

function Get-Asset {
    param(
        [string]$Name,
        [string]$Url,
        [string]$Sha256,
        [string]$CacheFile = "",
        [string]$PreferredLocalFile = ""
    )
    if ($PreferredLocalFile) {
        $local = Join-Path $RepoRoot $PreferredLocalFile
        if (Test-Path -LiteralPath $local) {
            Assert-Hash $local $Sha256
            Write-Host "Using local $Name asset: $local"
            return $local
        }
    }
    if (-not $CacheFile) {
        $CacheFile = [IO.Path]::GetFileName(([Uri]$Url).AbsolutePath)
    }
    $target = Join-Path $CacheDirectory $CacheFile
    if (Test-Path -LiteralPath $target) {
        try {
            Assert-Hash $target $Sha256
            Write-Host "Using cached $Name asset: $target"
            return $target
        }
        catch {
            Remove-Item -LiteralPath $target -Force
        }
    }
    Write-Host "Downloading $Name"
    & curl.exe -L --fail --retry 3 --output $target $Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $Url" }
    Assert-Hash $target $Sha256
    return $target
}

function Expand-With7Zip([string]$Archive, [string]$Destination) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & $SevenZip x $Archive "-o$Destination" -y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not extract $Archive" }
}

function Install-ArchiveMember([string]$Archive, [string[]]$Members, [string]$Destination) {
    $temp = Join-Path $Staging ([Guid]::NewGuid().ToString("N"))
    Expand-With7Zip $Archive $temp
    foreach ($member in $Members) {
        $normal = $member -replace '/', '\'
        $source = Join-Path $temp $normal
        if (-not (Test-Path -LiteralPath $source)) { throw "Archive member not found: $member in $Archive" }
        Copy-Item -LiteralPath $source -Destination $Destination -Force
    }
}

function Build-Launcher([string]$Destination, [string]$ObjectPath) {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere)) { throw "Visual Studio Build Tools are required to compile ffmpeg.exe." }
    $vsRoot = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $vsRoot) { throw "The Visual C++ x64 build tools are not installed." }
    $devShell = Join-Path $vsRoot "Common7\Tools\Launch-VsDevShell.ps1"
    & $devShell -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | Out-Null
    $source = Join-Path $RepoRoot "src\ffmpeg_launcher.cpp"
    $arguments = @(
        "/nologo", "/O2", "/MT", "/EHsc", "/std:c++20", $source,
        "/Fo:$ObjectPath", "/Fe:$Destination", "/link", "/SUBSYSTEM:CONSOLE"
    )
    & cl.exe $arguments
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Destination)) {
        throw "Could not compile the FFmpeg-compatible launcher."
    }
}

Assert-SafeOutput $OutputDirectory
New-Item -ItemType Directory -Force -Path $CacheDirectory | Out-Null
if (Test-Path -LiteralPath $OutputDirectory) {
    if (-not $Force) { throw "Output already exists. Re-run with -Force: $OutputDirectory" }
    Remove-Item -LiteralPath $OutputDirectory -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$Staging = Join-Path $RepoRoot ".cache\staging-runtime"
Assert-SafeOutput $Staging
if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Staging | Out-Null

try {
    $lib = Join-Path $OutputDirectory "lib"
    $ffmpegDir = Join-Path $lib "ffmpeg"
    $vsDir = Join-Path $lib "vapoursynth"
    $pluginsDir = Join-Path $vsDir "vs-plugins"
    $modelsDir = Join-Path $lib "models"
    $toolsDir = Join-Path $OutputDirectory "tools"
    New-Item -ItemType Directory -Force -Path $lib,$ffmpegDir,$vsDir,$pluginsDir,$modelsDir,$toolsDir | Out-Null

    $ffmpegArchive = Get-Asset "FFmpeg full build" $Manifest.ffmpeg.url $Manifest.ffmpeg.sha256 "" $Manifest.ffmpeg.preferred_local_file
    $ffmpegStage = Join-Path $Staging "ffmpeg"
    Expand-With7Zip $ffmpegArchive $ffmpegStage
    $ffmpegRoot = Join-Path $ffmpegStage $Manifest.ffmpeg.archive_root
    if (-not (Test-Path -LiteralPath (Join-Path $ffmpegRoot "bin\ffmpeg.exe"))) { throw "FFmpeg archive layout is invalid." }
    Copy-Item -Path (Join-Path $ffmpegRoot "*") -Destination $ffmpegDir -Recurse -Force
    # Keep the public compatibility launcher as the only file named
    # ffmpeg.exe. The bundled upstream binary is an implementation detail and
    # must not be selectable as the public FrameMeld entry point.
    Move-Item -LiteralPath (Join-Path $ffmpegDir "bin\ffmpeg.exe") -Destination (Join-Path $ffmpegDir "ffmpeg-core.exe")
    Move-Item -LiteralPath (Join-Path $ffmpegDir "bin\ffprobe.exe") -Destination (Join-Path $ffmpegDir "ffprobe.exe")
    Move-Item -LiteralPath (Join-Path $ffmpegDir "bin\ffplay.exe") -Destination (Join-Path $ffmpegDir "ffplay.exe")
    Remove-Item -LiteralPath (Join-Path $ffmpegDir "bin") -Recurse -Force

    $pythonArchive = Get-Asset "Python portable" $Manifest.python.url $Manifest.python.sha256
    $vsArchive = Get-Asset "VapourSynth portable" $Manifest.vapoursynth.url $Manifest.vapoursynth.sha256
    Expand-With7Zip $pythonArchive $vsDir
    Expand-With7Zip $vsArchive $vsDir
    $sitePackages = Join-Path $vsDir "Lib\site-packages"
    New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
    $wheel = Get-ChildItem -LiteralPath (Join-Path $vsDir "wheel") -Filter "VapourSynth-*-cp312-*.whl" | Select-Object -First 1
    if (-not $wheel) { throw "The VapourSynth cp312 wheel is missing." }
    Expand-With7Zip $wheel.FullName $sitePackages
    $wheelRuntime = Join-Path $sitePackages "VapourSynth-70.data\data\Lib\site-packages\vapoursynth.dll"
    if (-not (Test-Path -LiteralPath $wheelRuntime)) { throw "The VapourSynth runtime DLL is missing from its wheel." }
    Copy-Item -LiteralPath $wheelRuntime -Destination (Join-Path $sitePackages "vapoursynth.dll") -Force
    Remove-Item -LiteralPath (Join-Path $sitePackages "VapourSynth-70.data") -Recurse -Force
    if (Test-Path -LiteralPath (Join-Path $vsDir "VSScriptPython38.dll")) {
        Remove-Item -LiteralPath (Join-Path $vsDir "VSScriptPython38.dll") -Force
    }
    $pthPath = Join-Path $vsDir "python312._pth"
    $pth = Get-Content -LiteralPath $pthPath | Where-Object { $_ -notin @("Lib\site-packages", "vs-scripts", "..\..\tools") }
    $pth = $pth -replace '^#import site$', 'import site'
    @($pth) + @("Lib\site-packages", "vs-scripts", "..\..\tools") | Set-Content -LiteralPath $pthPath -Encoding ascii

    foreach ($entry in $Manifest.plugins.PSObject.Properties) {
        $name = $entry.Name
        $plugin = $entry.Value
        $cacheFile = if ($plugin.PSObject.Properties.Name -contains "cache_file") { $plugin.cache_file } else { "" }
        $archive = Get-Asset $name $plugin.url $plugin.sha256 $cacheFile
        if ($plugin.PSObject.Properties.Name -contains "member") {
            Install-ArchiveMember $archive @($plugin.member) $pluginsDir
        }
        elseif ($plugin.PSObject.Properties.Name -contains "members") {
            Install-ArchiveMember $archive @($plugin.members) $pluginsDir
        }
        else {
            $extension = [IO.Path]::GetExtension(([Uri]$plugin.url).AbsolutePath)
            Copy-Item -LiteralPath $archive -Destination (Join-Path $pluginsDir ($name + $extension)) -Force
        }
    }

    $modelDir = Join-Path $modelsDir $Manifest.rife_model.name
    New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
    $modelBin = Get-Asset "RIFE model weights" $Manifest.rife_model.bin_url $Manifest.rife_model.bin_sha256 "rife-4.26-flownet.bin"
    $modelParam = Get-Asset "RIFE model parameters" $Manifest.rife_model.param_url $Manifest.rife_model.param_sha256 "rife-4.26-flownet.param"
    Copy-Item -LiteralPath $modelBin -Destination (Join-Path $modelDir "flownet.bin") -Force
    Copy-Item -LiteralPath $modelParam -Destination (Join-Path $modelDir "flownet.param") -Force

    Copy-Item -Path (Join-Path $RepoRoot "src\engine\insight_engine") -Destination $lib -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot "src\engine\engine_entry.py") -Destination $lib -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot "src\insight_blur.py") -Destination $toolsDir -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot "src\encoder_selection.py") -Destination $toolsDir -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot "src\engine_defaults.py") -Destination $toolsDir -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot "src\framemeld_cli.py") -Destination $toolsDir -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot "src\runtime_probe.py") -Destination $toolsDir -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot "config\rife-performance.json") -Destination $OutputDirectory -Force
    Copy-Item -Path (Join-Path $RepoRoot "config\presets") -Destination $OutputDirectory -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot "README.md") -Destination $OutputDirectory -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot "USAGE.zh-CN.md") -Destination $OutputDirectory -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot "LICENSE") -Destination (Join-Path $OutputDirectory "LICENSE-GPL-3.0.txt") -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot "NOTICE.md") -Destination $OutputDirectory -Force
    Copy-Item -Path (Join-Path $RepoRoot "docs") -Destination $OutputDirectory -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot "config\windows-runtime.json") -Destination (Join-Path $OutputDirectory "runtime-manifest.json") -Force

    $launcher = Join-Path $OutputDirectory "ffmpeg.exe"
    Build-Launcher $launcher (Join-Path $Staging "ffmpeg_launcher.obj")
    Copy-Item -LiteralPath $launcher -Destination (Join-Path $OutputDirectory "ffprobe.exe") -Force

    $ffmpeg = Join-Path $ffmpegDir "ffmpeg-core.exe"
    $ffmpegInfo = & $ffmpeg -hide_banner -version 2>&1 | Select-Object -First 12
    $requiredFlags = @(
        "--enable-libx264", "--enable-libx265", "--enable-libplacebo",
        "--enable-vulkan", "--enable-opencl", "--enable-nvenc",
        "--enable-amf", "--enable-libvpl"
    )
    $infoText = $ffmpegInfo -join "`n"
    foreach ($flag in $requiredFlags) {
        if ($infoText -notmatch [regex]::Escape($flag)) { throw "FFmpeg is missing required build flag: $flag" }
    }
    $encoderText = (& $ffmpeg -hide_banner -encoders 2>&1) -join "`n"
    $requiredEncoders = @(
        "h264_nvenc", "h264_amf", "h264_qsv", "libx264",
        "hevc_nvenc", "hevc_amf", "hevc_qsv", "libx265"
    )
    foreach ($encoderName in $requiredEncoders) {
        if ($encoderText -notmatch "(?m)^\s*[A-Z.]{6}\s+$([regex]::Escape($encoderName))\s") {
            throw "FFmpeg is missing required encoder: $encoderName"
        }
    }
    $python = Join-Path $vsDir "python.exe"
    & $python -c "import vapoursynth as vs; print('VapourSynth', vs.__api_version__.api_major)"
    if ($LASTEXITCODE -ne 0) { throw "Portable Python cannot import VapourSynth." }
    $vspipe = Join-Path $vsDir "VSPipe.exe"
    & $vspipe --info (Join-Path $toolsDir "runtime_probe.py") - | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "VapourSynth plugin probe failed." }
    $launcherVersion = & $launcher -hide_banner -version 2>&1
    $launcherExitCode = $LASTEXITCODE
    $launcherVersion | Select-Object -First 1
    if ($launcherExitCode -ne 0) { throw "The FFmpeg compatibility launcher cannot start the core executable." }
    $probeLauncher = Join-Path $OutputDirectory "ffprobe.exe"
    $probeVersion = & $probeLauncher -hide_banner -version 2>&1
    $probeExitCode = $LASTEXITCODE
    if ($probeExitCode -ne 0 -or ($probeVersion | Select-Object -First 1) -notmatch '^ffprobe version') {
        throw "The sibling FFprobe compatibility launcher cannot start the core executable."
    }
    & $launcher -framemeld --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "The FrameMeld command route is unavailable." }
    $capabilities = & $launcher -framemeld --capabilities-json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $capabilities.protocol -ne "org.framemeld.cli" -or $capabilities.api_version -ne 1) {
        throw "The FrameMeld capability protocol is unavailable."
    }
    & $launcher -blur --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "The legacy -blur alias is unavailable." }

    $publicFfmpegFiles = @(Get-ChildItem -LiteralPath $OutputDirectory -Recurse -File -Filter "ffmpeg.exe")
    if ($publicFfmpegFiles.Count -ne 1 -or $publicFfmpegFiles[0].FullName -ne $launcher) {
        $found = ($publicFfmpegFiles.FullName -join ", ")
        throw "Runtime must expose exactly one selectable ffmpeg.exe. Found: $found"
    }

    Write-Host ""
    Write-Host "Runtime built successfully: $OutputDirectory" -ForegroundColor Green
    Write-Host "FrameMeld FFmpeg-compatible path: $launcher" -ForegroundColor Cyan
}
finally {
    if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
}
