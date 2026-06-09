# Anomaly Implementation Handoff

Bu proje target kullanmadan aylik tek ana metrik anomalisi skorlar. Ana metrik herhangi bir operasyonel sayisal deger olabilir; kod metrik adina bagli calismaz.

## Temel Kontrat

`configs/anomaly.yaml` icinde:

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
    - FATURA_TTR   # ornek ana metrik; baska proseste POS_CIROSU yazilabilir
    - TURNOVER_AMT # ornek referans feature
```

`feature_variables` listesindeki ilk kolon skorlanan ana metriktir. Diger feature kolonlari sadece `model.derived_features` altinda acikca referans verilirse anomali sinyali veya peer degiskeni olur.

## Run

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
python3 src/anomaly_pipeline.py --config configs/anomaly.yaml --skip-peer-quality-report
```

Oracle source + Oracle sink icin:

```bash
./scripts/run_oracle.sh --skip-peer-quality-report
```

## Output

Decision table operasyonel tablodur:

- Ham input kolonlari onde korunur.
- `ANOMALI_FLAG`
- `ANOMALI_NEDENI`

Detail table analiz tablosudur:

- Musteri-ay seri gorunumu.
- `DETAY_SATIR_TIPI`: `HISTORY_MONTH` veya `SCORING_MONTH`.
- `SKOR_KOLON_DURUMU`: history satirlarinda skor kolonlarinin neden bos oldugunu, scoring satirinda skorlanip skorlanmadigini gosterir.
- `AKTIF_SINYAL_ADET` ve `*_SINYAL_AKTIF` kolonlari: scoring ayinda hangi sinyallerin hesaplanabildigini gosterir.
- `ANA_METRIK_EKSIK_MI`
- `PEER_AYLIK_ANA_METRIK_MEDYAN`
- `PEER_AYLIK_ANA_METRIK_ORTALAMA`
- `MUSTERI_PEER_ANA_METRIK_ORANI`
- `ORAN_PAY_KOLON` ve `ORAN_PAYDA_KOLON`
- `MUSTERI_ANA_METRIK_PAYDA_ORANI`
- `PEER_AYLIK_ORAN_PAYDA_MEDYAN`
- `PEER_AYLIK_ANA_METRIK_PAYDA_ORAN_MEDYAN`
- Customer/peer trend, sezon, z-score, p-value, reason ve data-quality alanlari.

Oracle source + Oracle sink run'inda decision/detail lokal CSV olarak uretilmez; Oracle'a yazilir. Peer quality raporu aciksa sadece analiz raporlari lokal uretilir.

## Model Ozeti

- Scoring ayi fit icinde kullanilmaz.
- Once customer-first kanit aranir: kendi gecmis, trend, sezon ve son 3 ay rejimi.
- Customer kaniti yetersizse adaptif peer secimi devreye girer.
- Peer secimi segment degiskenleri ve config ile acikca tanimlanan turev peer degiskenleri uzerinden objective score ile yapilir.
- Ana metrik eksik aylar doldurulmaz; sadece gap ve coverage sinyali olarak detail tabloda tasinir.
- Final karar evidence-first p-value konsolidasyonu ile verilir; agirlikli ortalama target modeli degildir.

## Peer Quality

Peer quality raporu instance bazinda su ana metrik dagilim kolonlarini verir:

- `peer_guncel_ana_metrik_ortalama`
- `peer_guncel_ana_metrik_medyan`
- `peer_guncel_ana_metrik_std`
- `peer_guncel_ana_metrik_min`
- `peer_guncel_ana_metrik_max`
- `peer_gecmis_ana_metrik_ortalama`
- `peer_gecmis_ana_metrik_medyan`
- `peer_gecmis_ana_metrik_std`
- `peer_gecmis_ana_metrik_min`
- `peer_gecmis_ana_metrik_max`

Bu alanlar karar yerine peer temsil ve dagilim kalitesini analiz etmek icindir.
