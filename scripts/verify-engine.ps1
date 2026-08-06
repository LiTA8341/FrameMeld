[CmdletBinding()]
param(
    [string]$RuntimeDirectory = "",
    [int]$Seconds = 1
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $RuntimeDirectory) { $RuntimeDirectory = Join-Path $RepoRoot "dist\framemeld-runtime" }
$RuntimeDirectory = [IO.Path]::GetFullPath($RuntimeDirectory)
$ffmpeg = Join-Path $RuntimeDirectory "ffmpeg.exe"
$ffprobe = Join-Path $RuntimeDirectory "ffprobe.exe"
$work = Join-Path $RepoRoot ".cache\engine-validation"
New-Item -ItemType Directory -Force -Path $work | Out-Null

$normalInput = Join-Path $work "normal-640x360-60.mp4"
$duplicateInput = Join-Path $work "duplicate-640x360-60.mp4"
& $ffmpeg -y -hide_banner -loglevel error -f lavfi -i "testsrc2=size=640x360:rate=60" -t $Seconds -c:v libx264 -preset ultrafast -qp 0 $normalInput
if ($LASTEXITCODE -ne 0) { throw "Could not create the normal validation input." }
& $ffmpeg -y -hide_banner -loglevel error -f lavfi -i "testsrc2=size=640x360:rate=30" -vf fps=60 -t $Seconds -c:v libx264 -preset ultrafast -qp 0 $duplicateInput
if ($LASTEXITCODE -ne 0) { throw "Could not create the duplicate-frame validation input." }

$results = [Collections.Generic.List[object]]::new()
function Invoke-EngineCase {
    param(
        [string]$Name,
        [string]$InputPath,
        [string]$ExpectedFps,
        [string[]]$EngineArguments
    )
    $output = Join-Path $work ($Name + ".mp4")
    $timer = [Diagnostics.Stopwatch]::StartNew()
    & $ffmpeg -framemeld -i $InputPath @EngineArguments -c:v h264 -cq 22 $output
    $code = $LASTEXITCODE
    $timer.Stop()
    if ($code -ne 0) { throw "Engine case failed: $Name" }
    $fps = & $ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate -of default=nw=1:nk=1 $output
    if ($fps -ne $ExpectedFps) { throw "Unexpected FPS for $Name`: $fps (expected $ExpectedFps)" }
    $results.Add([pscustomobject]@{
        Case = $Name
        Seconds = [math]::Round($timer.Elapsed.TotalSeconds, 3)
        FPS = $fps
    })
}

Invoke-EngineCase "rife-interpolation" $normalInput "120/1" @("--interpolate-fps", "120", "--no-blur", "--no-deduplicate")
Invoke-EngineCase "rife-motion-blur" $normalInput "60/1" @("--interpolate-fps", "120", "--blur-output-fps", "60", "--no-deduplicate")
Invoke-EngineCase "rife-balanced-policy" $normalInput "300/1" @("--performance-mode", "balanced", "--no-blur", "--no-deduplicate")
Invoke-EngineCase "rife-adaptive-policy" $normalInput "300/1" @("--performance-mode", "adaptive", "--no-blur", "--no-deduplicate")
Invoke-EngineCase "svp-interpolation" $normalInput "120/1" @("--interpolate-fps", "120", "--interpolation-method", "svp", "--no-blur", "--no-deduplicate")
foreach ($method in @("old", "svp", "rife")) {
    Invoke-EngineCase "deduplicate-$method" $duplicateInput "60/1" @("--no-interpolate", "--no-blur", "--deduplicate-method", $method)
}
Invoke-EngineCase "blur-timescale-color" $normalInput "30/1" @(
    "--no-interpolate", "--no-deduplicate",
    "--output-timescale", "0.5",
    "--blur-output-fps", "30",
    "--weighting", "gaussian",
    "--blur-gamma", "2.0",
    "--brightness", "1.05",
    "--contrast", "1.1",
    "--saturation", "1.1"
)

$results | Format-Table -AutoSize
Write-Host "All headless engine paths passed." -ForegroundColor Green
