# Configured Anomaly Pipeline

## Amac

Bu akis target kullanmadan aylik tutar anomalisi skorlar. Son ay implementasyon ayi olarak skorlanir; onceki aylar modelleme ve peer istatistikleri icin kullanilir. Varsayilan pencere son 36 aydir.

## Config Dosyalari

- `configs/anomaly.yaml`: kolon rolleri, model parametreleri, peer secimi, output ve rapor ayarlari.
- `configs/data_source.yaml`: aktif CSV/Oracle source secimi, kaynak tanimlari, Oracle connection ve output sink tablo ayarlari. Bu dosya ortam/secret bilgisi tasidigi icin git'e alinmaz; `configs/data_source.example.yaml` kopyalanarak olusturulur.

CSV mi Oracle mi okunacagina `configs/data_source.yaml` icindeki `active_source` karar verir. Output sink icin `active_sink` kullanilir.

`model.scoring_month` iki sekilde calisir:

```yaml
model:
  scoring_month: last
```

`last`, kaynak datadaki en son donemi skorlar. Belirli bir ay icin `YYYYMM`, `YYYYMMDD` veya tarih benzeri format yaz:

```yaml
model:
  scoring_month: 202603
```

Bu ornekler de `202603` ayina normalize edilir:

```yaml
model:
  scoring_month: 20260301
```

```yaml
model:
  scoring_month: 2026-03-01
```

## Degisken Gruplari

Kullanici tarafinda ana kolon rol isimleri yazilmaz. `variables` altinda sadece kolon gruplari verilir:

```yaml
variables:
  id_variables:
    - MUSTERINO
  time_variables:
    - DONEM_AY
  segment_variables:
    - SEGMENTAD
    - REF_ALTFAALIYET
    - SUBE_KD
  feature_variables:
    - FATURA_TTR
    - TURNOVER_AMT
    - AKTIF_ABONE
```

`feature_variables` listesindeki ilk kolon skorlanan ana tutardir. Diger feature kolonlari turnover/aktif abone gibi destek sinyali veya operasyonel feature olarak kullanilir. Eski `columns.required` formati backward-compatible kalir ama yeni kullanimda gerekli degildir.

Secilen source icinde `output_columns: all` ise kaynak tablodaki kolonlar decision/detail output'a tasinir. Bir kolonu istemiyorsan ilgili source altinda `exclude_output_columns` kullan.

## Peer Secimi

`peer_selection.priority_variables: auto` ise sistem `variables.segment_variables` listesini ve feature'lardan tureyen `turnover_bucket`, `active_subscriber_bucket` gibi operatif peer degiskenlerini kullanir. Ek olarak:

- `mandatory_variables`: varsa once bu degiskenleri merkeze alir.
- `fallback_variables`: destek dusunce bu degiskenlerle daha genis peer dener.
- `exclude_variables`: kullanilmasini istemedigin peer degiskenlerini yazarsin.

Peer adaylari destek, sezon, recent, current ve dagilim kalitesi esiklerini gecemezse daha genis peer'e duser.

## Calistirma

CSV/local run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1
```

```bash
./scripts/run_pipeline.sh
```

Oracle output run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_oracle.ps1
```

```bash
./scripts/run_oracle.sh
```

Oracle source + Oracle sink modunda decision/detail Oracle'a yazilir; lokal decision/detail CSV, contract JSON ve staging snapshot uretilmez. Peer quality raporu aciksa sadece peer analiz dosyalari uretilir.

Raporu atlayarak hizli scoring:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1 -SkipPeerQualityReport
```

```bash
./scripts/run_pipeline.sh --skip-peer-quality-report
```

## Oracle Write Mode

- `append`: tabloya ekler.
- `delete_insert`: decision icin ayni skor ayini, detail icin ayni `MODEL_DONEM_AY` kosusunu silip yeniden yazar.
- `truncate_insert`: tabloyu bosaltip yazar.
- `replace`: tabloyu drop/create ederek yeniden yazar.

Mevcut Oracle tablosunda hedef output kolonlarindan biri eksikse ve `create_table: true` ise tablo otomatik drop/create edilir.
