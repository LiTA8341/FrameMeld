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
$work = Join-Path $RepoRoot ".cache\smoke-test"
New-Item -ItemType Directory -Force -Path $work | Out-Null
$input = Join-Path $work "input-720p60.mp4"
$output = Join-Path $work "output-720p60-rife-hevc.mp4"

& $ffmpeg -y -hide_banner -loglevel error -f lavfi -i "testsrc2=size=1280x720:rate=60" -f lavfi -i "sine=frequency=440:sample_rate=48000" -t $Seconds -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac $input
if ($LASTEXITCODE -ne 0) { throw "Could not generate smoke-test input." }

$timer = [Diagnostics.Stopwatch]::StartNew()
& $ffmpeg -framemeld -i $input --interpolate-fps 120 --blur-output-fps 60 --interpolation-method rife --deduplicate-method rife -c:v h265 -cq 20 $output
$timer.Stop()
if ($LASTEXITCODE -ne 0) { throw "RIFE render smoke test failed." }

$result = & $ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,avg_frame_rate -show_entries format=duration -of json $output | ConvertFrom-Json
if (
    -not $result.streams -or
    $result.streams[0].codec_name -ne "hevc" -or
    $result.streams[0].avg_frame_rate -ne "60/1"
) { throw "Unexpected HEVC smoke-test output." }
Write-Host "Smoke test passed in $([math]::Round($timer.Elapsed.TotalSeconds, 2)) seconds" -ForegroundColor Green
$result | ConvertTo-Json -Depth 5
