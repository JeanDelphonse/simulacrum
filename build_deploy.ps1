$ErrorActionPreference = 'Stop'
$source = "C:\Users\jeand\OneDrive\Opts\simulacrum"
$dest   = Join-Path $source "simulacrum_deploy.zip"

$excludeDirs  = @('.git','__pycache__','uploads','.idea','.vscode')
$excludeFiles = @('.env','.htaccess','simulacrum_deploy.zip','simulacrum_deploy_may5.zip','build_deploy.ps1')
$excludeExts  = @('.pyc','.pyo')

if (Test-Path $dest) { Remove-Item $dest -Force }

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($dest, 'Create')

$allFiles = Get-ChildItem -Path $source -Recurse -File

foreach ($f in $allFiles) {
    $rel = $f.FullName.Substring($source.Length + 1)
    $parts = $rel -split [regex]::Escape([System.IO.Path]::DirectorySeparatorChar)

    $skip = $false
    foreach ($part in $parts) {
        if ($excludeDirs -contains $part) { $skip = $true; break }
    }
    if (-not $skip -and ($excludeFiles -contains $f.Name)) { $skip = $true }
    if (-not $skip -and ($excludeExts -contains $f.Extension)) { $skip = $true }

    if (-not $skip) {
        $entryName = 'simulacrum/' + ($rel -replace [regex]::Escape('\'), '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $f.FullName, $entryName) | Out-Null
    }
}

$zip.Dispose()
$size = [math]::Round((Get-Item $dest).Length / 1MB, 2)
Write-Host "Done: simulacrum_deploy.zip - $size MB"
