param(
    [Parameter(Mandatory = $true)]
    [string]$SourceSdist,

    [Parameter(Mandatory = $true)]
    [string]$BuilderPython,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot
)

$ErrorActionPreference = "Stop"
$expectedSourceSha256 = "d6bd68a916fb2451ab3dd640b2494e545edc204c839ae1d4dd49f88f89999b74"
$actualSourceSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $SourceSdist).Hash.ToLowerInvariant()
if ($actualSourceSha256 -ne $expectedSourceSha256) {
    throw "The acvl-utils source archive hash does not match the reviewed sdist."
}

$builderVersions = & $BuilderPython -c "import importlib.metadata as m; print('|'.join(m.version(x) for x in ('setuptools','wheel','packaging')))"
if ($builderVersions.Trim() -ne "81.0.0|0.47.0|26.2") {
    throw "The isolated builder versions are not setuptools 81.0.0, wheel 0.47.0, packaging 26.2."
}

$firstOutput = Join-Path $OutputRoot "build-a"
$secondOutput = Join-Path $OutputRoot "build-b"
foreach ($directory in @($firstOutput, $secondOutput)) {
    if (Test-Path -LiteralPath $directory) {
        throw "Output directory already exists: $directory"
    }
    New-Item -ItemType Directory -Path $directory | Out-Null
}

$env:SOURCE_DATE_EPOCH = "315532800"
& $BuilderPython -m pip wheel --no-deps --no-build-isolation --wheel-dir $firstOutput $SourceSdist
if ($LASTEXITCODE -ne 0) {
    throw "The first internal wheel build failed."
}
& $BuilderPython -m pip wheel --no-deps --no-build-isolation --wheel-dir $secondOutput $SourceSdist
if ($LASTEXITCODE -ne 0) {
    throw "The second internal wheel build failed."
}

$firstWheel = Get-ChildItem -LiteralPath $firstOutput -Filter "acvl_utils-0.2.6-*.whl" -File -Single
$secondWheel = Get-ChildItem -LiteralPath $secondOutput -Filter "acvl_utils-0.2.6-*.whl" -File -Single
$firstHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $firstWheel.FullName).Hash.ToLowerInvariant()
$secondHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $secondWheel.FullName).Hash.ToLowerInvariant()
if ($firstHash -ne $secondHash) {
    throw "The two internal wheel builds are not byte-for-byte reproducible."
}

[pscustomobject]@{
    filename = $firstWheel.Name
    sha256 = $firstHash
    reproducible = $true
}
