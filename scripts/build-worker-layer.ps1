<#
.SYNOPSIS
    Builds the transcription worker's Lambda layer into .build/worker-layer.

.DESCRIPTION
    The worker (backend/src/hearally/worker.py) starts and checks the Parakeet
    job on Nebius, which needs the Nebius SDK and grpc. Lambda runs arm64
    Linux, so the wheels are fetched for that platform, never this machine's.
    The GPU job's own code (transcriber/) goes in too, under transcriber_job/,
    because each job is the stock PyTorch image with that code injected.

    Terraform zips the result (infra/modules/application, worker_layer_dir).
    Run it before planning; deploy-production.ps1 does.

.PARAMETER Python
    A Python 3.10+ with pip. Defaults to "python".

.EXAMPLE
    ./scripts/build-worker-layer.ps1
    ./scripts/build-worker-layer.ps1 -Python C:\path\to\python.exe
#>
[CmdletBinding()]
param(
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$out = Join-Path $repoRoot '.build/worker-layer'
$transcriber = Join-Path $repoRoot 'transcriber'

if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force (Join-Path $out 'python') | Out-Null

# Pinned to the version the operator tooling was tested with.
& $Python -m pip install --quiet --disable-pip-version-check `
    --platform manylinux2014_aarch64 --python-version 3.12 --implementation cp `
    --only-binary=:all: --target (Join-Path $out 'python') 'nebius==0.6.14' 'tzdata==2025.2'
if ($LASTEXITCODE -ne 0) { throw 'pip could not fetch the worker dependencies' }

$job = Join-Path $out 'transcriber_job'
New-Item -ItemType Directory -Force (Join-Path $job 'src/hearally_transcriber') | Out-Null
Copy-Item (Join-Path $transcriber 'bootstrap.sh') $job
Copy-Item (Join-Path $transcriber 'requirements.txt') $job
Copy-Item (Join-Path $transcriber 'src/hearally_transcriber/*.py') (Join-Path $job 'src/hearally_transcriber')

$size = (Get-ChildItem -Recurse $out | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("Worker layer built: {0:N0} MB in {1}" -f $size, $out)
