# Download a release executable, check its hash, then pass arguments unchanged.
$ErrorActionPreference = 'Stop'
$releaseTag = '__RELEASE_TAG__'
$base = "https://github.com/walero-moon/winix-local/releases/download/$releaseTag"
$architecture = $env:PROCESSOR_ARCHITEW6432
if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITECTURE }
if ($env:OS -ne 'Windows_NT' -or $architecture -ne 'AMD64') {
    throw 'This download requires Windows x64. See the README for other options.'
}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$asset = 'winix-local-windows-x86_64.exe'
$work = Join-Path ([IO.Path]::GetTempPath()) ('winix-local-' + [guid]::NewGuid())
$null = New-Item -ItemType Directory -Path $work
$setupArguments = @($args)
if ($setupArguments.Count -eq 0) { $setupArguments = @('setup') }

try {
    $executable = Join-Path $work $asset
    $checksums = Join-Path $work 'SHA256SUMS'
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$asset" -OutFile $executable
    Invoke-WebRequest -UseBasicParsing -Uri "$base/SHA256SUMS" -OutFile $checksums
    $pattern = '^([a-f0-9]{64})  ' + [regex]::Escape($asset) + '$'
    $entries = @(Get-Content $checksums | Where-Object { $_ -match $pattern })
    if ($entries.Count -ne 1) { throw 'Missing or duplicate executable checksum.' }
    $expected = [regex]::Match($entries[0], $pattern).Groups[1].Value
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $executable).Hash
    if ($actual -ine $expected) { throw 'Checksum verification failed. Nothing was run.' }

    & $executable @setupArguments
    $result = $LASTEXITCODE
    if ($result -ne 0) { throw "Winix Local exited with code $result." }
}
finally {
    Remove-Item -LiteralPath $work -Recurse -Force
}
