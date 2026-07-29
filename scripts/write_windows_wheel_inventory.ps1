param(
    [Parameter(Mandatory = $true)]
    [string]$Wheelhouse,

    [Parameter(Mandatory = $true)]
    [string]$RequirementsOutput,

    [Parameter(Mandatory = $true)]
    [string]$HashOutput
)

$ErrorActionPreference = "Stop"
$resolvedWheelhouse = (Resolve-Path -LiteralPath $Wheelhouse).Path
$wheels = Get-ChildItem -LiteralPath $resolvedWheelhouse -Filter "*.whl" |
    Sort-Object -Property Name

if ($wheels.Count -eq 0) {
    throw "The wheelhouse contains no wheels."
}

$requirements = @(
    "--only-binary=:all:"
    "--require-hashes"
)
$hashes = @()

foreach ($wheel in $wheels) {
    $parts = $wheel.BaseName -split "-"
    if ($parts.Count -lt 5) {
        throw "Unrecognized wheel filename: $($wheel.Name)"
    }
    $distribution = $parts[0] -replace "_", "-"
    $version = $parts[1]
    $sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $wheel.FullName).Hash.ToLowerInvariant()
    $requirements += "$distribution==$version --hash=sha256:$sha256"
    $hashes += "$sha256  $($wheel.Name)"
}

[System.IO.File]::WriteAllLines(
    $RequirementsOutput,
    $requirements,
    [System.Text.UTF8Encoding]::new($false)
)
[System.IO.File]::WriteAllLines(
    $HashOutput,
    $hashes,
    [System.Text.UTF8Encoding]::new($false)
)
