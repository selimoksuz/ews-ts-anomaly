param(
    [string]$ConfigPath = "configs\anomaly.yaml",
    [switch]$SkipPeerQualityReport
)

$argsList = @("-ConfigPath", $ConfigPath)

if ($SkipPeerQualityReport) {
    $argsList += "-SkipPeerQualityReport"
}

powershell -ExecutionPolicy Bypass -File scripts\run_configured_anomaly_pipeline.ps1 @argsList
exit $LASTEXITCODE
