# Model Documentation

## Amac

Bu model target kullanmadan aylik tutar anomalisi skorlar. Problem supervised siniflandirma degil; her ay gelen yeni fatura tutarinin musterinin kendi davranisina ve temsil eden peer grubuna gore makul olup olmadigini olcer.

## Zaman Kurgusu

- `scoring_month: last` ise verideki son ay implementasyon ayi kabul edilir.
- Scoring ayi fit icinde kullanilmaz.
- Scoring ayindan onceki aylar customer history, trend, sezon ve peer istatistikleri icin kullanilir.
- Varsayilan pencere son 36 aydir.

## Veri Seviyesi

Zorunlu roller:

- Musteri ID
- Donem ayi
- Tutar

Opsiyonel ama peer kalitesi icin onemli roller:

- Segment
- Faaliyet/sektor
- Turnover
- Sube
- Aktif abone veya benzeri portfoy degiskenleri

Fatura tutari doldurulmaz. Ilk gorulme oncesi aylar yapay satir olarak uretilmez. Ilk gorulme sonrasi kopuk aylar detail tabloda data quality sinyali olarak tutulur.

## Customer-First Yaklasim

Model once musterinin kendi datasinin yeterli olup olmadigina bakar:

- Kendi gecmis medyani
- Musteri trend beklentisi
- Ayni ay/sezon beklentisi
- Stabil son 3 ay rejimi
- Son 12 ay kapsama
- Scoring ayindan onceki gap

Musteri yeterince okunabiliyorsa karar driver'i oncelikle customer family olur. Bu durumda peer bilgisi karar destek ve temsil kontrolu olarak kalabilir.

## Adaptif Peer Secimi

Peer adaylari config'teki segment degiskenlerinden uretilir. `priority_variables: auto` ise sistem teknik kolonlari, ID/ay/tutar kolonlarini ve source role kolonlarini disarida birakarak uygun kategorik degiskenleri infer eder.

Peer adaylari su kriterlerle degerlendirilir:

- History support
- Ayni sezon/ay support
- Recent support
- Current month support
- Dagilim kalitesi
- Stabilite
- Spesifiklik
- Temsil skoru

Destek ve kalite esikleri gecilmezse daha genis peer seviyesine dusulur. Peer secimi objective function ile yapilir; sadece ilk uygun peer'e takilmaz.

## Sinyal Aileleri

Customer family:

- `self_history`
- `customer_trend`
- `customer_seasonal`
- `customer_recent_regime`

Peer family:

- `historical_peer`
- `current_peer`
- `peer_trend`
- `turnover_intensity`

Her sinyal robust z-score, empirical p-value ve evidence score ile degerlendirilir.

`customer_recent_regime` sadece son 3 calendar ayin tamami mevcutsa ve bu 3 ay kendi icinde stabilse aktif olur. Amaci eski uzun tarihsel seviyenin yeni kisa rejimi bastirdigi veya scoring ayinin stabil son 3 aydan sert koptugu vakalari yakalamaktir. Bu kosullar saglanmazsa sinyal `NaN` kalir ve skora girmez.

## Evidence Aggregation

Final skor agirlikli ortalama degildir. Evidence-first yaklasim kullanilir:

- Customer ve peer family p-value'lari ayri hesaplanir.
- Ayni yonde guclu customer + peer sinyali varsa `CUSTOMER_PEER_COMBINED`.
- Sadece musteri yeterli ve gucluyse `CUSTOMER`.
- Mustrinin datasinin yetersiz kaldigi yerde peer gucluyse `PEER`.
- Customer ve peer ters yonde gucluyse `CUSTOMER_PEER_CONFLICT`.
- Yeterli kanit yoksa `WEAK_EVIDENCE` veya `INSUFFICIENT_EVIDENCE`.

Skor 0-100 araligindadir. Yuksek skor daha anomaliye yakin davranisi ifade eder.

## Reason Mantigi

Decision reason en guclu sinyalden baslar. Ornek:

- Ana neden: musterinin kendi gecmisi
- Ana neden: gecmis peer beklentisi
- Ana neden: fatura/turnover yogunlugu
- Ana neden: musteri trendi
- Ana neden: musteri sezonalligi
- Ana neden: musterinin son 3 ay rejimi

Detail tabloda driver, p-value, z-score, beklenen tutarlar, peer metrikleri ve data quality alanlari birlikte bulunur.

## Watchlist ve Anomaly

`configs/anomaly.yaml` icindeki:

- `watch_top_rate`
- `high_top_rate`

skor dagilimina gore watchlist ve high anomaly esiklerini belirler. Bu oranlar target degildir; label'siz dunyada operasyonel review yogunlugunu kontrol eden kapasite parametreleridir.

## Performans Guardrail

Optimizasyonlar benchmark ile dogrulanmadan kabul edilmez.

Benchmark kontrol eder:

- Eski ve yeni skorlar ayni mi?
- Driver, label, reason uyumu bozuldu mu?
- Runtime gercekten iyilesti mi?

Komut:

```bash
python3 scripts/benchmark_score_aggregation.py --config configs/anomaly.yaml
```
