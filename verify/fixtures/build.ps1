$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$sources = @("simple.v", "widths.v", "xz_init.v", "many_signals.v")

foreach ($src in $sources) {
    $base = [System.IO.Path]::GetFileNameWithoutExtension($src)
    $vcd = "${base}.vcd"
    $fst = "${base}.fst"

    Write-Host "Building $src ..."
    iverilog -o "${base}.vvp" $src
    if ($LASTEXITCODE -ne 0) { throw "iverilog failed" }

    vvp "${base}.vvp"
    if ($LASTEXITCODE -ne 0) { throw "vvp failed" }

    if (-not (Test-Path $vcd)) {
        # Icarus may name VCD per $dumpfile in source
        $alt = Get-ChildItem "*.vcd" | Where-Object { $_.Name -ne $vcd } | Select-Object -First 1
        if ($alt) { Rename-Item $alt.Name $vcd }
    }

    vcd2fst $vcd $fst
    if ($LASTEXITCODE -ne 0) { throw "vcd2fst failed" }

    Remove-Item "${base}.vvp", $vcd -ErrorAction SilentlyContinue
    Write-Host "  -> $fst  $((Get-Item $fst).Length) bytes"
}

Write-Host "Done."
Get-ChildItem *.fst | ForEach-Object { Write-Host "  $($_.Name)  $($_.Length) bytes" }
