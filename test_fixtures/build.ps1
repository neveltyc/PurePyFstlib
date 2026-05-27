$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$sources = @("counter.v", "nested.v")

foreach ($src in $sources) {
    $base = [System.IO.Path]::GetFileNameWithoutExtension($src)
    $fst = "${base}.fst"

    Write-Host "Compiling $src ..."
    iverilog -o "${base}.vvp" $src
    if ($LASTEXITCODE -ne 0) { throw "iverilog failed" }

    Write-Host "  Running ${base}.vvp -fst ..."
    vvp "${base}.vvp" -fst
    if ($LASTEXITCODE -ne 0) { throw "vvp failed" }

    # vvp -fst creates <name>.fst alongside the vvp
    # but we need to make sure the name is right
    if (-not (Test-Path $fst)) {
        # vvp might name it after the $dumpfile in the Verilog
        throw "FST file not found: $fst"
    }

    Remove-Item "${base}.vvp" -ErrorAction SilentlyContinue
    Write-Host "  -> $fst  $((Get-Item $fst).Length) bytes"
}

Write-Host "Done."
