param(
    [string]$ConfigPath = "configs\anomaly.yaml",
    [string]$DataSourceConfigPath = "",
    [string]$OutputDir = "",
    [int]$BacktestMonths = 0,
    [int]$StressTestSampleSize = 0,
    [switch]$SkipStressTest,
    [string]$LogDir = "outputs\logs"
)

$ErrorActionPreference = "Stop"

$argsList = @(
    "src\anomaly_validation.py",
    "--config", $ConfigPath
)

if ($DataSourceConfigPath -ne "") {
    $argsList += @("--data-source-config", $DataSourceConfigPath)
}

if ($OutputDir -ne "") {
    $argsList += @("--output-dir", $OutputDir)
}

if ($BacktestMonths -gt 0) {
    $argsList += @("--backtest-months", [string]$BacktestMonths)
}

if ($StressTestSampleSize -gt 0) {
    $argsList += @("--stress-test-sample-size", [string]$StressTestSampleSize)
}

if ($SkipStressTest) {
    $argsList += "--skip-stress-test"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$runTs = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$logFile = Join-Path $LogDir "anomaly_validation_$runTs.log"

Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] validation_run_start" | Tee-Object -FilePath $logFile
Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] log_file=$logFile" | Tee-Object -FilePath $logFile -Append
Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] command=python $($argsList -join ' ')" | Tee-Object -FilePath $logFile -Append

python @argsList 2>&1 | Tee-Object -FilePath $logFile -Append
$status = $LASTEXITCODE

Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] validation_run_end status=$status" | Tee-Object -FilePath $logFile -Append
exit $status
