param(
    [string]$ConfigPath = "configs\anomaly.yaml",
    [string]$DataSourceConfigPath = "",
    [switch]$EnableOracleOutput,
    [switch]$SkipPeerQualityReport
)

$argsList = @(
    "src\configured_anomaly_pipeline.py",
    "--config", $ConfigPath
)

if ($DataSourceConfigPath -ne "") {
    $argsList += @("--data-source-config", $DataSourceConfigPath)
}

if ($EnableOracleOutput) {
    $argsList += "--enable-oracle-output"
}

if ($SkipPeerQualityReport) {
    $argsList += "--skip-peer-quality-report"
}

python @argsList
exit $LASTEXITCODE
