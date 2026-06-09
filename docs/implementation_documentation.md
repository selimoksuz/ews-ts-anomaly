# Implementation Documentation

## Klasor Yapisi

- `src/`: model, config, IO ve rapor kodlari
- `scripts/`: Windows PowerShell ve Linux/macOS shell runner'lari
- `configs/anomaly.yaml`: model ve peer secim parametreleri
- `configs/data_source.example.yaml`: datasource template
- `configs/data_source.yaml`: ortam bazli gercek datasource; git'e alinmaz
- `data/`: lokal input; git'e alinmaz
- `outputs/`: run ciktilari; git'e alinmaz

## Kurulum

Windows PowerShell:

```powershell
python -m pip install -r requirements.txt
Copy-Item configs\data_source.example.yaml configs\data_source.yaml
```

Linux/macOS terminal:

```bash
python3 -m pip install -r requirements.txt
cp configs/data_source.example.yaml configs/data_source.yaml
chmod +x scripts/*.sh
```

`configs/data_source.yaml` dosyasini kendi ortamindaki CSV veya Oracle bilgileriyle doldur.

## Config Secimleri

Input secimi `configs/data_source.yaml` icindeki `active_source` ile yapilir:

```yaml
active_source: local_csv
```

veya:

```yaml
active_source: oracle_input
```

Output sink secimi `active_sink` ile yapilir. Oracle yazimi icin sink tanimi yeterli degildir; run komutunda ayrica Oracle output flag'i verilmelidir.

## Lokal CSV Run

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1 -SkipPeerQualityReport
```

Linux/macOS:

```bash
./scripts/run_pipeline.sh --skip-peer-quality-report
```

Dogudan Python:

```bash
python3 src/configured_anomaly_pipeline.py --config configs/anomaly.yaml --skip-peer-quality-report
```

## Oracle Input + Oracle Output Run

`configs/data_source.yaml`:

```yaml
active_source: oracle_input
active_sink: oracle_output
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_oracle.ps1 -SkipPeerQualityReport
```

Linux/macOS:

```bash
./scripts/run_oracle.sh --skip-peer-quality-report
```

Dogudan Python:

```bash
python3 src/configured_anomaly_pipeline.py \
  --config configs/anomaly.yaml \
  --enable-oracle-output \
  --skip-peer-quality-report
```

## Outputlar

Decision table:

- Tek satir = scoring ayindaki musteri
- Ham input kolonlari
- `ANOMALI_FLAG`
- `ANOMALI_NEDENI`

Detail table:

- Tek satir = scoring musterileri icin musteri-ay
- Musteri seri gorunumu
- Peer aylik medyan/ortalama metrikleri
- Trend, sezon, p-value, z-score, evidence driver ve reason detaylari

## Oracle Write Mode

- `append`: tabloya ekler
- `delete_insert`: decision icin ayni `DONEM_AY`, detail icin ayni `MODEL_DONEM_AY` silinir ve yeniden yazilir
- `truncate_insert`: tablo bosaltilir ve yeniden yazilir
- `replace`: tablo drop/create edilir

Varsayilan `delete_insert` operasyonel run icin uygundur.

## Linux Notlari

- Path'lerde `/` kullan. Python kodu Windows path'lerini de okuyabilir ama dokuman ve shell scriptler POSIX path varsayar.
- Shell runner'lar kendi konumundan repo kokunu bulup oraya gecerek calisir; yine de config ve data path'leri repo kokune gore verilmelidir.
- Shell runner'lar stage bazli ilerleme log'u basar ve ayni ciktiyi varsayilan olarak `outputs/logs/anomaly_pipeline_<timestamp>.log` dosyasina yazar.
- Oracle client thin mode `oracledb` ile calisir; ekstra instant client gerekmeyebilir.
- Oracle network erisimi ve firewall izinleri makine bazinda ayrica saglanmalidir.
- `PYTHON_BIN` ile python binary override edilebilir:

```bash
PYTHON_BIN=python ./scripts/run_pipeline.sh --skip-peer-quality-report
```

Log dosyasi canli izlenebilir:

```bash
tail -f outputs/logs/anomaly_pipeline_*.log
```

Log dizini override edilebilir:

```bash
./scripts/run_oracle.sh --log-dir /tmp/anomaly_logs
```

Uzun model adimlarinda terminal/log sessiz kalmasin diye varsayilan heartbeat 60 saniyedir:

```bash
./scripts/run_oracle.sh --heartbeat-seconds 30
./scripts/run_oracle.sh --heartbeat-seconds 0
```

## Validasyon

Compile kontrolu:

```bash
python3 -m py_compile src/*.py scripts/benchmark_score_aggregation.py
```

Benchmark:

```bash
python3 scripts/benchmark_score_aggregation.py --config configs/anomaly.yaml
```

Peer kalite raporu default olarak aciktir. Hizli smoke run icin `--skip-peer-quality-report` kullan.
