# Monthly Amount Anomaly Pipeline

Target kullanmadan aylik tutar anomalisi skorlayan, customer-first ve peer-aware bir pipeline.

## Ne yapar?

- Son ayi implementasyon/scoring ayi olarak ayirir.
- Onceki aylardan musteri trendi, sezon etkisi, kendi gecmisi ve peer istatistikleri uretir.
- Musteri verisi yeterliyse once musteri sinyalini kullanir.
- Musteri verisi zayifsa veya peer sinyali gucluyse adaptif peer ile karsilastirir.
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

## Lokal CSV run

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1 -SkipPeerQualityReport
```

```bash
./scripts/run_pipeline.sh --skip-peer-quality-report
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

## Outputlar

- Decision table: input ham kolonlari + `ANOMALI_FLAG` + `ANOMALI_NEDENI`
- Detail table: musteri serisi, peer metrikleri, trend/sezon, evidence driver, skor ve reason detaylari

## Performans guardrail

Optimizasyonlar benchmark ile dogrulanmadan kabul edilmez.

```powershell
python scripts\benchmark_score_aggregation.py --config configs\anomaly.yaml
```

```bash
python3 scripts/benchmark_score_aggregation.py --config configs/anomaly.yaml
```

Benchmark hem runtime kazancini hem de eski/yeni skor esdegerligini kontrol eder.
