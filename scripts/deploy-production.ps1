<#
.SYNOPSIS
    Deploys Hearally to production. There is no other environment.

.DESCRIPTION
    Builds the marketing stylesheet, applies Terraform, uploads the static site
    and invalidates the CDN. Terraform owns every resource; nothing here creates
    infrastructure on its own.

    Production is the only environment by decision. See docs/adr/0002-production-only.md.

.PARAMETER SkipInfra
    Upload the site only. Use when nothing in infra/ changed.

.PARAMETER Python
    A Python with pip, for building the worker layer. Defaults to "python".

.PARAMETER AutoApprove
    Skip the Terraform confirmation prompt. Off by default: read the plan.

.EXAMPLE
    ./scripts/deploy-production.ps1
    ./scripts/deploy-production.ps1 -SkipInfra
#>
[CmdletBinding()]
param(
    [switch]$SkipInfra,
    [switch]$AutoApprove,
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$terraform = Join-Path $repoRoot '.tools/terraform-1.15.6/terraform.exe'
$infraDir = Join-Path $repoRoot 'infra/production'
$webDir = Join-Path $repoRoot 'web'

if (-not (Test-Path $terraform)) {
    throw "Terraform not found at $terraform. Vendored tooling lives in .tools/ and is gitignored; download 1.15.6 there."
}

function Write-Step {
    param([string]$Message)
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

Write-Step 'Building the marketing stylesheet'
Push-Location $webDir
try {
    if (-not (Test-Path 'node_modules')) {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Stylesheet build failed' }
}
finally {
    Pop-Location
}

if (-not $SkipInfra) {
    Write-Step 'Building the transcription worker layer'
    & (Join-Path $PSScriptRoot 'build-worker-layer.ps1') -Python $Python

    Write-Step 'Applying Terraform'
    & $terraform "-chdir=$infraDir" init -input=false
    if ($LASTEXITCODE -ne 0) { throw 'terraform init failed' }

    $applyArgs = @("-chdir=$infraDir", 'apply', '-input=false')
    if ($AutoApprove) { $applyArgs += '-auto-approve' }

    & $terraform @applyArgs
    if ($LASTEXITCODE -ne 0) { throw 'terraform apply failed' }
}

Write-Step 'Reading stack outputs'
$bucket = & $terraform "-chdir=$infraDir" output -raw web_bucket
$distribution = & $terraform "-chdir=$infraDir" output -raw web_distribution_id
if ([string]::IsNullOrWhiteSpace($bucket)) { throw 'Could not read web_bucket from Terraform outputs' }

Write-Step "Uploading the site to s3://$bucket"
# HTML is short-lived so copy changes land quickly. Assets are not fingerprinted,
# so they get an hour rather than a year: a long cache would serve stale CSS.
$public = Join-Path $webDir 'public'
aws s3 sync $public "s3://$bucket/" --delete --region us-east-1 --exclude '*' --include '*.html' --cache-control 'public, max-age=300'
if ($LASTEXITCODE -ne 0) { throw 'HTML upload failed' }
aws s3 sync $public "s3://$bucket/" --delete --region us-east-1 --exclude '*.html' --exclude 'collector/*' --cache-control 'public, max-age=3600'
if ($LASTEXITCODE -ne 0) { throw 'Site upload failed' }
# The collector download: saved as a file rather than shown as text, and
# short-lived like HTML, since its URL does not change between releases.
aws s3 cp (Join-Path $public 'collector/hearally-collector.mjs') "s3://$bucket/collector/hearally-collector.mjs" --region us-east-1 --content-type 'text/javascript; charset=utf-8' --content-disposition 'attachment; filename="hearally-collector.mjs"' --cache-control 'public, max-age=300'
if ($LASTEXITCODE -ne 0) { throw 'Collector upload failed' }

Write-Step 'Invalidating the CDN'
$invalidation = aws cloudfront create-invalidation --distribution-id $distribution --paths '/*' --query 'Invalidation.Id' --output text
if ($LASTEXITCODE -ne 0) { throw 'Invalidation failed' }
Write-Host "Invalidation $invalidation created. It usually clears within a couple of minutes."

Write-Step 'Verifying'
$apiUrl = & $terraform "-chdir=$infraDir" output -raw api_url
try {
    $config = Invoke-RestMethod -Uri "$apiUrl/v1/config" -TimeoutSec 30
    Write-Host "API healthy: $($config.service) $($config.version) ($($config.environment))" -ForegroundColor Green
}
catch {
    Write-Warning "GET $apiUrl/v1/config did not respond: $_"
    Write-Warning 'A freshly created custom domain can take a minute to serve TLS. Retry before assuming a failure.'
}

Write-Host ''
Write-Host 'Deployed.' -ForegroundColor Green
