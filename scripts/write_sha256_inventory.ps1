param(
    [Parameter(Mandatory = $true)]
    [string]$Directory,

    [Parameter(Mandatory = $true)]
    [string]$Output
)

$ErrorActionPreference = "Stop"
$resolvedDirectory = (Resolve-Path -LiteralPath $Directory).Path
$resolvedOutput = [System.IO.Path]::GetFullPath($Output)
$lines = @()

Get-ChildItem -LiteralPath $resolvedDirectory -File |
    Where-Object { $_.FullName -ne $resolvedOutput } |
    Sort-Object -Property Name |
    ForEach-Object {
        $sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        $lines += "$sha256  $($_.Name)"
    }

[System.IO.File]::WriteAllLines(
    $resolvedOutput,
    $lines,
    [System.Text.UTF8Encoding]::new($false)
)
