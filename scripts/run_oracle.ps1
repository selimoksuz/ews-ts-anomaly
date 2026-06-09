param(
    [string]$ConfigPath = "configs\anomaly.yaml",
    [string]$DataSourceConfigPath = "configs\data_source.yaml",
    [switch]$SkipPeerQualityReport
)

$argsList = @(
    "-ConfigPath", $ConfigPath,
    "-DataSourceConfigPath", $DataSourceConfigPath,
    "-EnableOracleOutput"
)

if ($SkipPeerQualityReport) {
    $argsList += "-SkipPeerQualityReport"
}

powershell -ExecutionPolicy Bypass -File scripts\run_anomaly_pipeline.ps1 @argsList
exit $LASTEXITCODE
