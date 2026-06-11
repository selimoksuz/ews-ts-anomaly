# Monthly Amount Anomaly Pipeline

Target kullanmadan aylik tutar anomalisi skorlayan, customer-first ve peer-aware bir pipeline.

## Ne yapar?

- Son ayi implementasyon/scoring ayi olarak ayirir.
- Onceki aylardan musteri trendi, sezon etkisi, kendi gecmisi ve peer istatistikleri uretir.
- Musteri verisi yeterliyse once musteri sinyalini kullanir.
- Musteri verisi zayifsa veya peer sinyali gucluyse adaptif peer ile karsilastirir.
- Peer secimini destek/dagilim/stabilite/spesifiklik yaninda gecmis kalibrasyon performansiyla yapar.
- Feature-ratio sinyalini veri kalite gate'lerinden gecmeden final skora sokmaz.
- PCA/Isolation Forest/LOF challenger diagnostic uretir; production kararini degistirmez.
- Karar tablosu ve detay tablosu uretir.
- CSV veya Oracle input okuyabilir, lokal veya Oracle output yazabilir.

## Dokumanlar

- [Model Documentation](docs/model_documentation.md)
- [Implementation Documentation](docs/implementation_documentation.md)

## Kurulum

```powershell
python -m pip install -r requirements.txt
Copy-Item configs\data_source.example.yaml configs\data_source.yaml
```

```bash
python3 -m pip install -r requirements.txt
cp configs/data_source.example.yaml configs/data_source.yaml
chmod +x scripts/*.sh
```

`configs\data_source.yaml` dosyasini kendi ortamindaki CSV veya Oracle bilgileriyle doldur. Bu dosya bilerek git'e alinmaz.

## Aktif model config'i

Pipeline varsayilan olarak `configs/anomaly.yaml` okur. Hazir template'ler:

```powershell
Copy-Item configs\anomaly_fatura.example.yaml configs\anomaly.yaml -Force
Copy-Item configs\anomaly_pos.example.yaml configs\anomaly.yaml -Force
```

```bash
cp configs/anomaly_fatura.example.yaml configs/anomaly.yaml
cp configs/anomaly_pos.example.yaml configs/anomaly.yaml
```

## Lokal CSV run

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1 -SkipPeerQualityReport
```

```bash
./scripts/run_pipeline.sh --skip-peer-quality-report
```

Linux run sirasinda terminale stage log'u basilir ve ayni cikti `outputs/logs` altina yazilir.

```bash
./scripts/run_pipeline.sh
tail -f outputs/logs/anomaly_pipeline_*.log
```

## Oracle input + Oracle output run

`configs\data_source.yaml` icinde:

```yaml
active_source: oracle_input
active_sink: oracle_output
```

Sonra:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_oracle.ps1 -SkipPeerQualityReport
```

```bash
./scripts/run_oracle.sh --skip-peer-quality-report
```

Log dizini degistirilebilir:

```bash
./scripts/run_oracle.sh --log-dir /tmp/anomaly_logs
```

Uzun adimlarda varsayilan olarak 60 saniyede bir heartbeat basilir. Aralik degistirilebilir veya kapatilabilir:

```bash
./scripts/run_oracle.sh --heartbeat-seconds 30
./scripts/run_oracle.sh --heartbeat-seconds 0
```

## Outputlar

- Decision table: input ham kolonlari + `ANOMALI_FLAG` + `ANOMALI_NEDENI`
- Detail table: musteri serisi, peer metrikleri, trend/sezon, evidence driver, skor ve reason detaylari

## Backtest ve validation monitor

Her aylik run sonrasi son N ayi rolling OOT mantigiyla test etmek icin:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_validation_report.ps1
```

```bash
./scripts/run_validation_report.sh
tail -f outputs/logs/anomaly_validation_*.log
```

Monitor `outputs/analysis/validation_report` altina aylik skor stabilitesi, scoreability, label dagilimi, gercek data perturbation stress testi ve decision/detail output integrity raporlari yazar. `ANOMALI_FLAG` kontrolu decision ve detail tablolarinda missing olmama ve sadece 0/1 deger alma sartini test eder.

## Performans guardrail

Optimizasyonlar benchmark ile dogrulanmadan kabul edilmez.

```powershell
python scripts\benchmark_score_aggregation.py --config configs\anomaly.yaml
```

```bash
python3 scripts/benchmark_score_aggregation.py --config configs/anomaly.yaml
```

Benchmark hem runtime kazancini hem de eski/yeni skor esdegerligini kontrol eder.
