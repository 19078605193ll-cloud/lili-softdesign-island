$ErrorActionPreference = 'Stop'
$destination = Join-Path $PSScriptRoot '..\var\backups\server-daily'
New-Item -ItemType Directory -Force $destination | Out-Null
$remote = 'ubuntu@159.75.2.196'
$names = & ssh -o BatchMode=yes -o ConnectTimeout=10 $remote 'ls -1 /opt/softdesign-island-backups/daily'
if ($LASTEXITCODE -ne 0) { throw 'Cannot list server backups' }
$useRsync = $false
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    & wsl.exe -d Ubuntu -- test -x /usr/bin/rsync
    $useRsync = $LASTEXITCODE -eq 0
}
foreach ($name in $names) {
    if ($name -notmatch '^\d{8}_\d{6}$') { continue }
    $target = Join-Path $destination $name
    if (Test-Path (Join-Path $target 'verified.txt')) { continue }
    if ($useRsync) {
        New-Item -ItemType Directory -Force $target | Out-Null
        if (-not (Test-Path (Join-Path $target 'assets.tar'))) {
            $seed = Get-ChildItem $destination -Filter assets.tar -Recurse |
                Where-Object { Test-Path (Join-Path $_.DirectoryName 'verified.txt') } |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($seed) { Copy-Item $seed.FullName (Join-Path $target 'assets.tar') }
        }
        $linuxScript = & wsl.exe -d Ubuntu -- wslpath -u ((Join-Path $PSScriptRoot 'download-backups.sh') -replace '\\', '/')
        $linuxKey = & wsl.exe -d Ubuntu -- wslpath -u ((Join-Path $env:USERPROFILE '.ssh\id_ed25519') -replace '\\', '/')
        $linuxKnown = & wsl.exe -d Ubuntu -- wslpath -u ((Join-Path $env:USERPROFILE '.ssh\known_hosts') -replace '\\', '/')
        $linuxTarget = & wsl.exe -d Ubuntu -- wslpath -u ($target -replace '\\', '/')
        & wsl.exe -d Ubuntu -- bash $linuxScript $linuxKey $linuxKnown $linuxTarget $name
    } else {
        & scp -q -r -o BatchMode=yes "${remote}:/opt/softdesign-island-backups/daily/$name" $destination
    }
    if ($LASTEXITCODE -ne 0) { throw "Backup download failed: $name" }
    $manifest = Get-Content (Join-Path $target 'manifest.json') -Raw | ConvertFrom-Json
    foreach ($file in $manifest.files.PSObject.Properties) {
        $hash = (Get-FileHash (Join-Path $target $file.Name) -Algorithm SHA256).Hash
        if ($hash -ne $file.Value) { throw "Checksum mismatch: $name/$($file.Name)" }
    }
    Set-Content (Join-Path $target 'verified.txt') (Get-Date -Format o)
}
