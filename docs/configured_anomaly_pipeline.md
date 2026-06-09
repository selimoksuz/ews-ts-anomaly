# Configured Anomaly Pipeline

## Amac

Bu akis target kullanmadan aylik tutar anomalisi skorlar. Son ay implementasyon ayi olarak skorlanir; onceki aylar modelleme ve peer istatistikleri icin kullanilir. Varsayilan pencere son 36 aydir.

## Config Dosyalari

- `configs/anomaly.yaml`: kolon rolleri, model parametreleri, peer secimi, output ve rapor ayarlari.
- `configs/data_source.yaml`: aktif CSV/Oracle source secimi, kaynak tanimlari, Oracle connection ve output sink tablo ayarlari. Bu dosya ortam/secret bilgisi tasidigi icin git'e alinmaz; `configs/data_source.example.yaml` kopyalanarak olusturulur.

CSV mi Oracle mi okunacagina `configs/data_source.yaml` icindeki `active_source` karar verir. Output sink icin `active_sink` kullanilir.

## Kolon Davranisi

`columns.required` bos kalirsa sistem bilinen alias'lardan musteri/id, ay ve tutar kolonlarini bulmaya calisir. Farkli isimli veri gelirse sadece gerekli roller yazilir:

```yaml
columns:
  required:
    customer_id: CUSTOMER_NO
    invoice_month: PERIOD_YYYYMM
    bill_amount: AMOUNT
```

Secilen source icinde `output_columns: all` ise kaynak tablodaki kolonlar decision/detail output'a tasinir. Bir kolonu istemiyorsan ilgili source altinda `exclude_output_columns` kullan.

## Peer Secimi

`peer_selection.priority_variables: auto` ise sistem teknik kolonlari, ID/ay/tutar rollerini ve map edilen raw kolonlari disarida birakarak kullanilabilir peer degiskenlerini infer eder. Ek olarak:

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
