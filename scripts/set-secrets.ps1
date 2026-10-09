<#
.SYNOPSIS
    Fills the Secrets Manager entries that Terraform creates empty.

.DESCRIPTION
    Terraform owns the secret containers but never their values, so rotating a
    key does not show up as configuration drift. This script is the only thing
    that writes values.

    No secret value ever appears on a command line or on screen:
    - Values are typed at a masked prompt, not passed as parameters, so they
      stay out of shell history.
    - They reach the AWS CLI through a temporary file in the user's private
      temp directory (--secret-string file://...), not as an argument, so they
      stay out of process listings. The file is deleted in a finally block.

    There is no collector secret any more: each computer is paired from the
    dashboard and gets its own credential (backend/src/hearally/pairing.py).

    Lambdas pick up a new value within five minutes (see secret_cache.py).

.PARAMETER Secret
    Which secret to set: Nebius, Tavily, Twilio or CallE. Omit to be asked.

.EXAMPLE
    ./scripts/set-secrets.ps1 -Secret Nebius
#>
[CmdletBinding()]
param(
    [ValidateSet('Nebius', 'Tavily', 'Twilio', 'CallE')]
    [string]$Secret,

    [string]$Region = 'us-east-1',

    [string]$Prefix = 'hearally-prod'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $Secret) {
    Write-Host 'Which secret?'
    Write-Host '  1. Nebius  - Token Factory API key, for Nemotron'
    Write-Host '  2. Tavily  - search API key, for the weekly scam-library refresh'
    Write-Host '  3. Twilio  - account SID and auth token'
    Write-Host '  4. CallE   - CALL-E API key, for heads-up calls to allies'
    switch (Read-Host 'Enter 1, 2, 3 or 4') {
        '1' { $Secret = 'Nebius' }
        '2' { $Secret = 'Tavily' }
        '3' { $Secret = 'Twilio' }
        '4' { $Secret = 'CallE' }
        default { throw 'Nothing chosen.' }
    }
}

function Read-Secret {
    param([string]$Prompt)
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $plain = [System.Net.NetworkCredential]::new('', $secure).Password
    if ([string]::IsNullOrWhiteSpace($plain)) { throw 'Empty value; nothing written.' }
    return $plain
}

function Set-SecretValue {
    param([string]$Name, [string]$Value)

    # $env:TEMP is under the user's profile, readable only by that user. The
    # value goes to the CLI through this file, never through its argument list.
    $file = Join-Path $env:TEMP ("hearally-secret-{0}.tmp" -f [guid]::NewGuid())
    try {
        [System.IO.File]::WriteAllText($file, $Value, [System.Text.UTF8Encoding]::new($false))
        $null = aws secretsmanager put-secret-value --secret-id $Name --secret-string "file://$file" --region $Region --output text
        if ($LASTEXITCODE -ne 0) { throw "Could not write $Name. Does it exist? Terraform creates it." }
    }
    finally {
        if (Test-Path $file) { Remove-Item $file -Force }
    }
    Write-Host "Wrote a new version of $Name." -ForegroundColor Green
}

switch ($Secret) {
    'Nebius' {
        $value = Read-Secret 'Nebius Token Factory API key'
        Set-SecretValue -Name "$Prefix/nebius-api-key" -Value $value
    }

    'Tavily' {
        $value = Read-Secret 'Tavily API key (starts with tvly-)'
        Set-SecretValue -Name "$Prefix/tavily-api-key" -Value $value
    }

    'Twilio' {
        $sid = Read-Secret 'Twilio Account SID'
        $token = Read-Secret 'Twilio Auth Token'
        $payload = ConvertTo-Json @{ account_sid = $sid; auth_token = $token } -Compress
        Set-SecretValue -Name "$Prefix/twilio" -Value $payload
    }

    'CallE' {
        $key = Read-Secret 'CALL-E API key'
        $payload = ConvertTo-Json @{ api_key = $key } -Compress
        Set-SecretValue -Name "$Prefix/calle" -Value $payload
    }
}

Write-Host ''
Write-Host 'Lambdas pick up the new value within five minutes. No redeploy is needed.' -ForegroundColor Cyan
