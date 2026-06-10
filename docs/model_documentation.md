# Model Documentation

## Amac

Bu model target kullanmadan aylik ana metrik anomalisi skorlar. Problem supervised siniflandirma degil; her ay gelen yeni ana metrik degerinin musterinin kendi davranisina ve temsil eden peer grubuna gore makul olup olmadigini olcer.

## Zaman Kurgusu

- `scoring_month: last` ise verideki son ay implementasyon ayi kabul edilir.
- Scoring ayi fit icinde kullanilmaz.
- Scoring ayindan onceki aylar customer history, trend, sezon ve peer istatistikleri icin kullanilir.
- Varsayilan pencere son 36 aydir.

## Veri Seviyesi

Zorunlu roller:

- Musteri ID
- Donem ayi
- Ana metrik

Opsiyonel ama peer kalitesi icin onemli degisken gruplari:

- Segment
- Faaliyet/sektor
- Sube
- Ana metrigi normalize edebilecek referans feature
- Portfoy/exposure gibi bucket'a cevrilebilecek feature'lar

Feature kolonlari adindan dolayi otomatik kullanilmaz. Bir feature'in oran sinyali, peer bucket'i veya davranis peer'i olarak kullanilmasi `model.derived_features` altinda acikca tanimlanir.

Ana metrik doldurulmaz. Ilk gorulme oncesi aylar yapay satir olarak uretilmez. Ilk gorulme sonrasi kopuk aylar detail tabloda data quality sinyali olarak tutulur.

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

Peer adaylari `variables.segment_variables` listesinden ve `model.derived_features` ile acikca uretilen operatif bucket'lardan uretilir. `priority_variables: auto` ise kullanicinin yazdigi segment degiskenleri ana peer aday setidir; feature kolonlari sadece config'te ratio/bucket/behavior olarak isaretlenirse peer adaylarina eklenir.

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

## Peer Kalite Metrikleri

Peer secimi `peer_selection.objective_weights` ile skorlanir. Varsayilan agirliklar:

- `representability`: 0.25
- `distribution`: 0.20
- `calibration`: 0.20
- `stability`: 0.15
- `specificity`: 0.20
- `support`: 0.10

Bu agirliklar normalize edilir; toplam 1 olmak zorunda degildir.

`representability` peer'in musteriyi temsil etme gucudur. Bilesenleri:

- History support: `hist_n / strong_history_rows`, agirlik 0.22
- Season support: `moy_n / strong_season_rows`, agirlik 0.13
- Recent support: `recent_n / strong_recent_rows`, agirlik 0.18
- Current support: `current_n / strong_current_rows`, agirlik 0.22
- Specificity: peer kiriliminin dar/bilgi tasiyan olmasi, agirlik 0.10
- Distribution quality: peer dagilim skoru, agirlik 0.15

`distribution` peer icindeki ana metrik dagiliminin saglikli olup olmadigini izler. Skew, kurtosis ve robust tail rate birlikte kullanilir. Merkez olcu medyan, sapma olcu MAD'dir. Ortalama ve standart sapma karar parametresi degil, raporlamada yardimci istatistiktir.

`calibration` peer'in gecmis aylarda bir sonraki ay referansi olarak ne kadar iyi calistigini olcer. Scoring ayi kullanilmaz. Scoring ayindan onceki son kalibrasyon aylarinda peer aylik medyani, yalnizca daha onceki aylarla kurulan expected degere gore test edilir. Bilesenleri:

- Median absolute residual: dusukse iyi, agirlik 0.45
- Robust interval coverage: `abs(residual) <= 3` orani, agirlik 0.35
- False alarm rate: `abs(residual) >= 2.5` orani, ters yonlu agirlik 0.20

`stability` peer'in tarihsel medyani ile son 3 ay medyani arasindaki kaymayi ve MAD degisimini cezalandirir. `specificity` daha dar ama destek gecen peer'leri odullendirir. `support` hist/season/recent/current adetlerini ayri bir destek skoru olarak toplar.

## Feature Ratio Kalite Gate

`feature_ratio` ana metrik / referans feature gibi oran sinyallerini uretir. Oran hesaplanabilir olsa bile final skora girmesi icin kalite gate'leri gecmelidir.

Global gate:

- `max_denominator_missing_or_zero_rate`: referans feature null/zero orani bu esigi asarsa oran skora girmez.
- `min_monthly_valid_coverage`: referans feature pozitif satir payi bu esigin altindaysa oran skora girmez.

Peer gate:

- `min_peer_ratio_rows`: secilen peer icinde minimum ratio gozlem sayisi.
- `min_peer_ratio_mad`: secilen peer icinde minimum ratio MAD.

Gate gecmezse oran detail tabloda diagnostic olarak kalir; `feature_ratio` sinyali final evidence aggregation'a girmez.

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
- `feature_ratio`

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
- Ana neden: ana metrik/referans feature orani
- Ana neden: musteri trendi
- Ana neden: musteri sezonalligi
- Ana neden: musterinin son 3 ay rejimi

Detail tabloda driver, p-value, z-score, beklenen ana metrik, peer metrikleri ve data quality alanlari birlikte bulunur.

## Watchlist ve Anomaly

`configs/anomaly.yaml` icindeki:

- `watch_top_rate`
- `high_top_rate`

skor dagilimina gore watchlist ve high anomaly esiklerini belirler. Bu oranlar target degildir; label'siz dunyada operasyonel review yogunlugunu kontrol eden kapasite parametreleridir.

Top-rate tek basina anomaly etiketi uretmez. `label_guardrails` aktifse minimum mutlak z-score ve minimum `abs(log(actual/expected))` etkisi de aranir:

- Watchlist default: `min_watch_abs_z=1.50`, `min_watch_log_effect=0.14`
- High anomaly default: `min_high_abs_z=2.50`, `min_high_log_effect=0.26`

## Challenger Model Diagnostic

Challenger modeller production driver degildir. Final `ANOMALI_FLAG` robust evidence-first sistemden gelir. Challenger katmani sadece residual feature matrix uzerinde ek diagnostic uretir:

- PCA reconstruction score
- Isolation Forest score
- Local Outlier Factor score

Raw ana metrik, raw segment ve raw musteri hacmi dogrudan modele verilmez. Kullanilan feature seti customer/peer z-score'lari, recent regime, data gap, peer quality ve actual/expected residual alanlaridir. Detail tabloda aggregate `MODEL_CHALLENGER_SKORU`, `MODEL_CHALLENGER_UYARI` ve `PCA/IF/LOF_CHALLENGER_ANOMALI_FLAG` alanlari bulunur.

## Literatur ve Model Ailesi Karari

Mevcut production modeli robust, evidence-first ve customer-first kalmalidir. Isolation Forest, LOF ve PCA production karari veren ana model olmamalidir; residual feature matrix uzerinde challenger diagnostic olarak kalmalidir. Autoencoder ve LSTM ailesi mevcut veri yapisinda production icin uygun degildir.

Bu karar problem yapisindan gelir:

- Target yok.
- Ana metrik tek sayisal seri.
- Frekans aylik.
- Musteri serileri kisa ve kesik.
- Her karar icin human-readable reason gerekiyor.
- Uretimde her ay sliding 36 aylik pencere ile calisilacak.

### Robust Median/MAD

NIST robust outlier tespitinde median ve MAD tabanli modified z-score yaklasimini kullanir. Bu, heavy-tail veya outlier etkisinin ortalama/std yaklasimini bozabilecegi durumlarda daha uygundur.

Mevcut karsilik:

- Ana metrik log uzayinda robust z-score ile skorlanir.
- Peer merkez olcusu medyan, sapma olcusu MAD'dir.
- Ortalama/std karar parametresi degil, peer quality raporunda destekleyici istatistiktir.

Kod karsiligi:

- `src/anomaly_model.py`: `robust_mad`, `robust_group_stats`, `score_from_z`

### Trend/Sezon Residual Scoring

Twitter S-H-ESD yaklasimi trend ve sezon etkisini ayirdiktan sonra residual uzerinde robust anomaly tespiti yapar. Bizde ayni prensip aylik ve sparse data icin sade uygulanir.

Mevcut karsilik:

- Musteri trendi yeterli history varsa hesaplanir.
- Musteri sezonu ayni ay gecmisi varsa kullanilir.
- Musteri seviyesi yetersizse peer trendi ve peer sezon etkisi kullanilir.
- Son 3 ay rejimi sadece son 3 calendar ay tam ve stabilse devreye girer.

Kod karsiligi:

- `src/anomaly_model.py`: `trend_group_stats`, `build_self_stats`, `customer_recent_regime_z`, `peer_trend_z`

### Isolation Forest

Isolation Forest az sayida ve farkli gozlemleri feature space icinde izole eder. Raw ana metrik ve raw segment kolonlariyla dogrudan calistirilirse olcek, segment ve peer etkisi karar aciklanabilirligini zayiflatir.

Mevcut karsilik:

- Isolation Forest sadece challenger diagnostic'tir.
- Raw ana metrik, raw segment ve raw musteri hacmi modele verilmez.
- Kullanilan feature set residual sinyallerden olusur: customer z, peer z, trend z, seasonal z, data gap, peer quality, actual/expected ratio.

Kod karsiligi:

- `src/anomaly_model.py`: `sklearn_challenger_scores`, `add_challenger_diagnostics`

### LOF

LOF lokal yogunluk farkini yakalar. Segment/peer yapisi dogru kurulmadan direkt kullanilirsa buyuk/kucuk peer yogunluk farklarini anomaly gibi yorumlayabilir ve reason kalitesi duser.

Mevcut karsilik:

- LOF production driver degildir.
- Residual feature matrix uzerinde challenger flag uretir.
- Detail tabloda `LOF_CHALLENGER_ANOMALI_FLAG` ile izlenir.

Kod karsiligi:

- `src/anomaly_model.py`: `sklearn_challenger_scores`

### PCA

PCA tabanli anomaly yaklasimi normal varyasyon alt uzayindan sapma/reconstruction error mantigina dayanir. Bizim problemde PCA, musteri veya peer davranisini tek basina aciklamaz; residual feature set icinde ek sinyal olarak mantiklidir.

Mevcut karsilik:

- PCA dependency-free challenger olarak eklenmistir.
- Production `ANOMALI_FLAG` PCA'dan gelmez.
- Detail tabloda `PCA_CHALLENGER_ANOMALI_FLAG` ve aggregate challenger skor bulunur.

Kod karsiligi:

- `src/anomaly_model.py`: `pca_challenger_score`

### Autoencoder ve LSTM

Donut, OmniAnomaly ve LSTM/autoencoder ailesi daha yogun, uzun ve genellikle multivariate zaman serilerinde anlamlidir. Mevcut aylik 36 ay pencerede, musteri bazinda kesik ve kisa seriler icin bu modeller asiri karmasik, aciklamasi zayif ve overfit riski yuksek olur.

Production'a alinmama nedeni:

- Musteri basina maksimum 36 nokta var.
- Bircok musteri daha kisa veya kesik.
- Model reason ciktisi isteniyor.
- Label yok; model stabilitesi dogrudan dogrulanamiyor.
- Her ay Oracle uzerinden operasyonel ve hizli calismasi gerekiyor.

Gelecekte ancak su sartlarda challenger olabilir:

- Musteri basina uzun ve duzenli seri.
- Ek multivariate davranis sinyalleri.
- Operasyonel feedback veya onaylanmis anomaly ornekleri.
- Backtestte robust sistemi anlamli ve stabil sekilde gecmesi.

### Fayda Saglayan ve Sinirlanan Parcalar

Fayda saglayan parcalar:

- Customer-first karar: musteri datasina guvenilebiliyorsa once musteri kendi dunyasinda degerlendirilir.
- Adaptif peer fallback: musteri datasinin yetersiz kaldigi yerde peer devreye girer.
- Peer objective: support, distribution, calibration, stability, specificity ve representability birlikte kullanilir.
- Peer calibration: scoring ayi kullanmadan gecmis holdout aylarinda peer referans isabeti olculur.
- Feature-ratio quality gate: referans feature eksik/zero ise oran skora sokulmaz.
- Label guardrail: top-rate tek basina flag uretmez; minimum z/effect kosulu aranir.
- Challenger modeller: IF/LOF/PCA production kararini degistirmeden ek diagnostic uretir.
- Validation monitor: gercek data rolling backtest, output integrity ve perturbation stress test birlikte izlenir.

Cikarilan veya sinirlanan parcalar:

- `*_FINAL_AGIRLIK` detail outputtan cikarildi. Final karar weighted average olmadigi icin bu isim yanlis izlenim veriyordu.
- Feature ratio default karar sinyali olmaktan cikarildi; kalite gate gecerse aktif.
- Behavior cluster default kapali. Mevcut veri ve peer quality sonucunda default faydasi kanitlanmadigi icin production driver degil.
- Autoencoder/LSTM production kapsamina alinmadi.
- Raw IF/LOF/PCA production kapsamina alinmadi.
- Performance ve project risk review output klasorleri kalici proje ciktisi olmaktan cikarildi.

Kaynaklar:

- NIST/SEMATECH e-Handbook, modified z-score and MAD based outlier detection: https://www.itl.nist.gov/div898/handbook/eda/section3/eda35h.htm
- Hochenbaum, Vallis, Kejariwal, Automatic Anomaly Detection in the Cloud Via Statistical Learning, S-H-ESD: https://arxiv.org/abs/1704.07706
- Liu, Ting, Zhou, Isolation Forest: https://cs.nju.edu.cn/zhouzh/zhouzh.files/publication/icdm08b.pdf
- Breunig, Kriegel, Ng, Sander, LOF: Identifying Density-Based Local Outliers: https://www.dbs.ifi.lmu.de/Publikationen/Papers/LOF.pdf
- Jackson and Mudholkar, Control Procedures for Residuals Associated With Principal Component Analysis: https://doi.org/10.1080/00401706.1979.10489779
- Xu et al., Donut: Unsupervised Anomaly Detection for Seasonal KPIs: https://arxiv.org/abs/1802.03903
- Su et al., OmniAnomaly: Stochastic Recurrent Neural Network for Multivariate Time Series Anomaly Detection: https://dl.acm.org/doi/10.1145/3292500.3330672
- Schmidl, Wenig, Papenbrock, Anomaly Detection in Time Series: A Comprehensive Evaluation: https://arxiv.org/abs/2202.04236

## Backtest ve Stabilite Monitoru

Uretim run'i her yeni ayda son 36 aylik pencereyi kaydirarak calisir. Validation monitor de ayni mantigi geriye donuk uygular: son N ayin her biri tek tek scoring ayi kabul edilir, o ay icin pencere tekrar kurulur ve sadece onceki aylar fit/reference olarak kullanilir.

Monitor ciktisi:

- `validation_monthly_summary.csv`: ay bazinda skorlanan satir, not-scored, anomaly/watchlist oranlari, skor quantile'lari, peer objective ve kalibrasyon medyanlari.
- `validation_stability_flags.csv`: aylar arasi watch/anomaly rate, high anomaly rate, scored rate ve peer kalibrasyon kaymalarini flag'ler.
- `validation_scoreability_breakdown.csv`: yeni/kesik/yeterli customer history gibi veri yeterlilik durumlarinin ay bazinda dagilimi.
- `validation_label_breakdown.csv`: label ve direction bazinda sayim ve skor medyanlari.
- `validation_stress_test_sensitivity.json`: gercek scoring datasindaki ornek musterilere kontrollu spike/drop perturbation uygulaninca skor tepkisini izler. Bu test gercek data rolling backtest'in yerine gecmez; ek stres testidir.
- `validation_output_integrity.csv`: decision/detail tablolarinda satir var mi, `ANOMALI_FLAG` missing mi, flag 0/1 disina cikmis mi, decision reason bos mu kontrolleri.

Bu monitor target uretmez. Amac her ay yeni veri eklendiginde gercek data uzerinde modelin stabilitesi, peer kalitesi, scoreability orani ve output sozlesmesi bozuldu mu sorusuna cevap vermektir.

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
