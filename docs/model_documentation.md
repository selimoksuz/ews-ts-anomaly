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

Peer adaylari `variables.segment_variables` listesinden ve `model.derived_features` ile acikca uretilen operatif bucket'lardan uretilir. Mevcut production config'te peer seviyeleri `peer_selection.explicit_levels` ile sabittir; bunun sebebi branch/sube gibi cok parcalayan kolonlarin destek gecmemesi ve davranis bazli peer adaylarinin daha iyi dagilim kalitesi vermesidir.

Aktif production aday setinde su aileler denenir:

- Davranis bazli adaylar: `segment_behavior_level_vol`, `segment_behavior_level`, `segment_behavior`, `sector_behavior_level`, `sector_behavior`, `behavior_level`, `behavior`.
- Referans feature bucket adaylari: `segment_sector_feature_q*_exposure`, `segment_feature_q*`, `sector_feature_q*_exposure`, `sector_feature_q*`.
- Is/segment adaylari: `segment_sector`, `segment`, `sector`.

`behavior_*` alanlari scoring ayini kullanmadan, yalnizca scoring ayindan onceki musteri gecmisinden uretilir. Bu nedenle peer secimine musteri davranis seviyesini katar ama scoring ayina leakage yaratmaz.

Referans feature bucket'i tek sabit kirilim degildir. `model.derived_features.feature_ratio.peer_bucket_variants` altinda q3/q4/q5/q8 gibi alternatif bucket cozumleri tanimlanir. Her run'da bu bucket edge'leri sadece history uzerinden fit edilir, scoring ayina aynen uygulanir ve her varyant peer objective icinde ayri aday olarak yaristirilir. Boylece `TURNOVER_AMT` veya baska bir denominator icin bucket sayisi manuel sabitlenmez; destek, dagilim, kalibrasyon ve temsil skoruna gore objective hangi bucket cozumunu daha iyi bulursa o secilir.

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

- `representability`: 0.20
- `distribution`: 0.30
- `calibration`: 0.20
- `stability`: 0.10
- `specificity`: 0.10
- `support`: 0.10

Bu agirliklar normalize edilir; toplam 1 olmak zorunda degildir.

`representability` peer'in musteriyi temsil etme gucudur. Once destek ve spesifiklik tabani hesaplanir, sonra bu taban dagilim kalite carpanina sokulur. Boylece yeterli satir destegi olsa bile heterojen/heavy-tail bir peer otomatik olarak strong temsil gibi gorunmez.

- History support: `hist_n / strong_history_rows`, taban agirlik 0.26
- Season support: `moy_n / strong_season_rows`, taban agirlik 0.15
- Recent support: `recent_n / strong_recent_rows`, taban agirlik 0.21
- Current support: `current_n / strong_current_rows`, taban agirlik 0.26
- Specificity: peer kiriliminin dar/bilgi tasiyan olmasi, taban agirlik 0.12
- Distribution factor: `0.50 + 0.50 * PEER_DAGILIM_SKORU / 100`

`distribution` peer'in normal dagilip dagilmadigini degil, robust sekilde karsilastirilabilir olup olmadigini izler. Ana metriklerde heavy-tail dogal oldugu icin normal dagilim hedeflenmez. Dagilim skoru asagidaki bilesenlerle hesaplanir ve bilesen agirliklari `peer_selection.distribution_quality` altindan yonetilir:

- Skorlanan ay log IQR: ayni ay icinde peer genisligi.
- Gecmis aylik log IQR medyani: peer'in tipik aylik genisligi.
- Skorlanan ay ve gecmis aylik log MAD: robust scale genisligi.
- Robust tail rate: medyan/MAD etrafinda robust outlier payi.
- Log skew/kurtosis: dusuk agirlikli heavy-tail sekil diagnostigi.
- Raw ortalama/medyan ve raw std/medyan: rapor ve dusuk agirlikli diagnostic ceza.

Ana anomaly residual'i yine medyan/MAD tabanlidir. Raw mean/median veya std/median tek basina peer'i gecersiz yapmaz; ama detail/peer quality raporunda peer'in neden genis veya riskli oldugunu aciklar. `PEER_DAGILIM_SKORU`, `PEER_TEMSIL_SKORU` ve `PEER_OBJECTIVE_SKORU` birlikte okunur.

2026-03 denemesinde mevcut segment/sector/tek feature-bucket/exposure adaylari secili peer ortalama dagilim skorunu yaklasik 34.6 seviyesinde birakti. Behavior bucket/cluster adaylari eklendiginde secili peer ortalama dagilim skoru yaklasik 47.4'e cikti ve not-scored satir sayisi 0 kaldi. Son revizyonda referans feature bucket q3/q4/q5/q8 olarak objective-driven denendi; secili peer ortalama dagilim skoru 47.49 oldu. Feature bucket adaylari 2,868 scoring musterisi icin secildi, behavior adaylari 14,745 scoring musterisi icin secildi. Bu sonuc feature bucket optimizasyonunun faydali ama tek basina yeterli olmadigini; behavior peer'in mevcut veriyle daha guclu iyilestirme getirdigini gosterir.

`calibration` peer'in gecmis aylarda bir sonraki ay referansi olarak ne kadar iyi calistigini olcer. Scoring ayi kullanilmaz. Scoring ayindan onceki son kalibrasyon aylarinda peer aylik medyani, yalnizca daha onceki aylarla kurulan expected degere gore test edilir. Bilesenleri:

- Median absolute residual: dusukse iyi, agirlik 0.45
- Robust interval coverage: `abs(residual) <= 3` orani, agirlik 0.35
- False alarm rate: `abs(residual) >= 2.5` orani, ters yonlu agirlik 0.20

`stability` peer'in tarihsel medyani ile son 3 ay medyani arasindaki kaymayi ve MAD degisimini cezalandirir. `specificity` daha dar ama destek gecen peer'leri odullendirir. `support` hist/season/recent/current adetlerini ayri bir destek skoru olarak toplar.

`PEER_SPESIFIKLIK_SKORU`, secilen peer kiriliminin ne kadar detayli ve oncelikli degiskenlerden olustugunu gosterir. Hesap mantigi:

- Peer kolonu yoksa skor dusuk baz seviye alir.
- Daha fazla peer kolonu kullanildikca `depth` artar.
- `priority_variables` icindeki oncelikli kolonlar kullanildikca `priority` artar.
- Skor 0-100 araligina tasinir.

Bu skor tek basina "iyi peer" demek degildir. Destek azsa `PEER_DESTEK_SKORU`, dagilim kotuyse `PEER_DAGILIM_SKORU`, gecmis referans performansi zayifsa `PEER_KALIBRASYON_SKORU` objective'i dusurur.

## Feature Ratio Kalite Gate

`feature_ratio` ana metrik / referans feature gibi oran sinyallerini uretir. Oran hesaplanabilir olsa bile final skora girmesi icin kalite gate'leri gecmelidir.

Global gate:

- `max_denominator_missing_or_zero_rate`: referans feature null/zero orani bu esigi asarsa oran skora girmez.
- `min_monthly_valid_coverage`: referans feature pozitif satir payi bu esigin altindaysa oran skora girmez.

Peer gate:

- `min_peer_ratio_rows`: secilen peer icinde minimum ratio gozlem sayisi.
- `min_peer_ratio_mad`: secilen peer icinde minimum ratio MAD.

Gate gecmezse oran detail tabloda diagnostic olarak kalir; `feature_ratio` sinyali final evidence aggregation'a girmez.

Peer bucket tarafinda ayni referans feature icin birden fazla bucket cozumlenebilir:

- `peer_bucket_variants.enabled`: referans feature bucket adaylarini acar/kapatir.
- `peer_bucket_variants.min_positive_rows`: bucket edge fit etmek icin gereken minimum pozitif denominator satiri.
- `peer_bucket_variants.variants`: q3/q4/q5/q8 gibi quantile listeleri.

Bu bucket'lar anomaly sinyali degil, peer adayidir. Oran sinyali kalite gate gecmezse final skora girmez; buna ragmen referans feature bucket'i peer seciminde kullanilabilir. Bunun sebebi denominator'in musteri hacmini temsil ederek peer'i daraltabilmesidir. Ancak hangi bucket cozumunun kullanilacagina sabit kural karar vermez; peer objective karar verir.

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

Sezon korumasi ayrica calisir: `customer_recent_regime` sert sapma gosteriyor ama `customer_seasonal` ayni ay icin normal diyorsa, bu durum sezonla aciklanmis kabul edilir ve son-3-ay rejim sinyali production evidence hesabina alinmaz. Detail tabloda son-3-ay z-score'u yine gorunur; normal reason metni "son 3 ay sapmasi var ancak sezon beklentisiyle uyumlu" diye aciklar.

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

`ANOMALI_SKORU` ham p-value/evidence skoru degildir; operasyonel karar skoru olarak ay ici risk persentiline kalibre edilir. Ham kanit gucu `ANA_SINYAL_SKORU`, `PRIMARY_SINYAL_SKORU`, `MUSTERI_SINYAL_SKORU` ve `PEER_SINYAL_SKORU` alanlarinda izlenir. Bu ayrim su nedenle vardir: bir musteri tek bir ham sinyalde cok yuksek sapabilir, fakat final actual/expected effect, peer/customer conflict veya operasyonel top-rate esigini gecmiyorsa `ANOMALI_FLAG=0` kalmalidir. Kalibrasyon sonrasi normal satirlar watchlist skor tabaninin altinda, watchlist satirlari watchlist ile high-anomaly tabani arasinda, high anomaly satirlari high-anomaly tabaninin uzerinde kalir.

## Challenger Model Diagnostic

Challenger modeller production driver degildir. Final `ANOMALI_FLAG` robust evidence-first sistemden gelir. Challenger katmani sadece residual feature matrix uzerinde ek diagnostic uretir:

- PCA reconstruction score
- Isolation Forest score
- Local Outlier Factor score

Raw ana metrik, raw segment ve raw musteri hacmi dogrudan modele verilmez. Challenger feature seti rule-derived veya karar-parametrik kolonlari kullanmaz. Kullanilan alanlar sadece ana metrikten uretilen fonksiyonel residual transformasyonlardir: musteri gecmis/trend/sezon/son-3-ay z skorlari, peer gecmis/guncel/trend z skorlari ve referans feature oran z skoru. Bu feature listesi `configs/anomaly.yaml > model.score_aggregation.challenger_models.feature_columns` altindan yonetilir.

Asagidaki kolonlar challenger modele sokulmaz; bunlar rule/diagnostic katmaninda kalir: `PRIMARY_SINYAL_P_DEGERI`, `ANA_SINYAL_SKORU`, `GUVEN_SKORU`, `MUSTERI_ACIKLANABILIRLIK_SKORU`, `EVIDENCE_CONFLICT_FLAG`, `VERI_YETERLILIK_DURUMU`, data-gap skoru, peer kalite/kalibrasyon skorlari ve final beklenen/gercek oranlari. Aday residual feature'lar da `min_feature_valid_rate` ve `min_feature_unique_values` gate'lerinden gecmeden challenger modele verilmez; boylece tamamen bos veya sabit feature modele sinyal gibi girmez. Detail tabloda aggregate `MODEL_CHALLENGER_SKORU`, model bazli `PCA/IF/LOF_CHALLENGER_SKORU`, `MODEL_CHALLENGER_UYARI` ve `PCA/IF/LOF_CHALLENGER_ANOMALI_FLAG` alanlari bulunur.

`MODEL_CHALLENGER_SKORU` tum model skorlarini zorunlu olarak ortalamaz; `challenger_models.aggregate_methods` listesinde secilen modellerden hesaplanir. Mevcut varsayilan `isolation_forest + pca`dir. LOF ayri skor/flag olarak uretilir, fakat mevcut validasyonda rule ile zayif hizalandigi icin aggregate skora dahil edilmez.

Challenger alanlari scoring ayina ait diagnostic'tir. Detail tablo musteri serisini gosterdigi icin bu kolonlar yalniz `DONEM_AY = MODEL_DONEM_AY` satirinda doludur; gecmis ay satirlarinda bos kalir.

## Oracle Veri Sozlugu

Oracle identifier limiti nedeniyle modelin urettigi uzun kolonlar kontrollu kisa aliaslarla yazilir. CSV/local output kolonlari human-readable kalabilir; Oracle yaziminda asagidaki alias sozlugu kullanilir. Raw input kolonlari mumkun oldugu surece inputtaki adiyla korunur.

### Decision Table

Decision tablo tek satir = scoring ayindaki musteri olacak sekilde tasarlanir. Kolon seti: ham input kolonlari + karar alanlari.

| Cikti kolonu | Oracle kolonu | Anlam | Modelde kullanim / kosul |
|---|---|---|---|
| Ham input kolonlari | Inputtaki ad | Kaynaktan gelen ve decision output'a tasinan kolonlar; ornek: `MUSTERINO`, `DONEM_AY`, ana metrik, segment/faaliyet/sube gibi kolonlar. | Decision table karar tuketimi icindir; bu kolonlar karar nedenini ham kaynak satiriyla baglamak, downstream join yapmak ve kullanicinin skorlanan gercek degeri gormesi icin korunur. |
| ANOMALI_FLAG | ANOMALI_FLAG | 1 ise scoring ayinda anomaly/watchlist karari var, 0 ise yok. | Final karar kolonudur. Robust evidence-first sisteminden gelir; challenger modeller bu flag'i dogrudan degistirmez. |
| ANOMALI_NEDENI | ANOMALI_NEDENI | Human-readable karar nedeni. | Final reason kolonudur. Ana sinyal, beklenen/gercek farki, musteri/peer guvenilirligi ve veri yeterlilik durumuna gore uretilir. |

### Detail Table

Detail tablo tek satir = skorlanan musterinin ilgili ay satiri olacak sekilde musteri serisini ve scoring ay karar kanitlarini tasir.

Fiziksel kolon sirasi asagidaki gruplara gore uretilir:

1. Ham input kolonlari
2. Seri ve data quality alanlari
3. Peer kimligi ve aylik peer metrikleri
4. Scoring ay beklenen/gercek ana metrik alanlari
5. Musteri sinyalleri
6. Peer sinyalleri
7. Challenger model skor ve flag alanlari
8. Peer kalite ve kalibrasyon alanlari
9. Davranis/behavior alanlari
10. Onceki skor diagnostigi
11. Evidence driver alanlari
12. Final karar, flag, skor ve reason alanlari

Detail sozlugunu okurken kolonlari su kullanim tipleriyle dusun:

| Kolon ailesi | Ornek kolonlar | Modelde aktif kullanim / kosul | Ne ise yarar |
|---|---|---|---|
| Ham input | `MUSTERINO`, `DONEM_AY`, ana metrik, segment degiskenleri | Her zaman tasinir. ID ve donem scoring/join icin zorunludur; ana metrik scoring degeridir; segment degiskenleri peer secimi icin kullanilir. | Kararin hangi kaynak satirindan geldigini, hangi ay ve hangi gercek ana metrikle skorlandigini gosterir. |
| Seri ve data quality | `ANA_METRIK_EKSIK_MI`, `YENI_MUSTERI_MI`, `KESIK_SERI_MI`, `SON_12_AY_KAPSAMA`, `DATA_GAP_SKORU` | Karar guveni ve strateji seciminde aktiftir. Ana metrik eksikse skorlanabilirlik sinirlanir; yeni/kesik musteri self-history guvenini dusurur ve peer fallback'i guclendirir. | Modelin neden musteri-first, peer-only veya dusuk guvenli karar verdigini aciklar. |
| Peer kimligi ve aylik peer metrikleri | `PEER_SEVIYE`, `PEER_KOLONLARI`, `PEER_AYLIK_ANA_METRIK_MEDYAN`, `MUSTERI_PEER_ANA_METRIK_ORANI` | Adaptif peer secimi sonucudur. Peer anlamli support ve kalite kapilarini gectiginde musteri degeri peer seviyesine gore de yorumlanir. | Musterinin hangi grupla kiyaslandigini ve o ay peer seviyesine gore nerede durdugunu gosterir. |
| Beklenen/gercek ana metrik | `SKORLANAN_ANA_METRIK`, `BEKLENEN_ANA_METRIK`, `GERCEK_BEKLENEN_ORANI` | Scoring ayinda final reason ve skor aciklamasinda aktiftir. Beklenen seviye customer/peer evidence kaynaklarindan secilir. | "Neye gore anomalidir?" sorusunun sayisal cevabini verir. |
| Musteri sinyalleri | `MUSTERI_GECMIS_Z`, `MUSTERI_TREND_Z`, `MUSTERI_SEZON_Z`, `MUSTERI_SINYAL_SKORU` | Musteri gecmisi yeterliyse oncelikli karar ailesidir. Trend icin yeterli gozlem, sezon icin ayni ay/gecmis ay bilgisi ve coverage kosullari aranir. | Musterinin kendi normalinden ne kadar saptigini olcer; customer-first mantigin ana evidencelaridir. |
| Peer sinyalleri | `GECMIS_PEER_Z`, `GUNCEL_PEER_Z`, `PEER_TREND_Z`, `PEER_SINYAL_SKORU` | Musteri verisi yetersizse veya peer de destekliyorsa aktiftir. Peer kalite dusukse karar guveni dusurulur veya review dili kullanilir. | Musteri kendi dunyasiyla aciklanamiyorsa benzer grup davranisina gore farki olcer. |
| Feature-ratio gate | `FEATURE_ORAN_Z`, `FEATURE_ORAN_GLOBAL_GATE_GECTI`, `FEATURE_ORAN_PEER_GATE_GECTI` | Sadece denominator coverage, missing/zero orani, peer ratio row sayisi ve MAD kosullari gecerse anomaly sinyali olabilir. Gate gecmezse diagnostic olarak kalir. | Ana metrik / referans feature oraninin guvenilir olup olmadigini ve karara dahil edilip edilmedigini aciklar. |
| Challenger diagnostic | `CHL_SKOR`, `CHL_PCA_SKOR`, `CHL_IF_SKOR`, `CHL_LOF_SKOR`, `CHL_*_FLG` | Sadece scoring ay satirinda doludur. Production flag'i degistirmez; sadece ana metrik residual transformasyonlariyla ek kontrol saglar. | Robust sistemle uyumlu/uyumsuz model davranisini izlemek ve challenger adaylarini takip etmek icindir; rule-derived karar kolonlari modele verilmez. |
| Peer kalite ve kalibrasyon | `PEER_TEMSIL_SKORU`, `PEER_OBJECTIVE_SKORU`, `PEER_DAGILIM_SKORU`, `PEER_KALIBRASYON_SKORU` | Peer seciminde ve karar guveninde aktiftir. Support, dagilim, spesifiklik, stabilite ve rolling OOT kalibrasyon kriterleri birlikte degerlendirilir. | Secilen peer gercekten temsil edici mi, yoksa peer kaynakli karar dikkatle mi okunmali sorusunu cevaplar. |
| Davranis/behavior | `DAVRANIS_CLUSTER`, `DAVRANIS_*` | Config'te behavior peer aktifse peer aday genisletmede kullanilir; kapaliyken karar sinyali degildir. | Musterinin seviye/volatilite/trend davranisini diagnostic olarak izler. |
| Onceki skor diagnostigi | `ONCEKI_ANOMALI_SKORU`, `ONCEKI_AYA_GORE_SKOR_FARKI`, `SKOR_TREND_DIAGNOSTIGI` | Karar driver'i degildir; bias yaratmamak icin final flag'i tek basina degistirmez. | Bu ayki skorun onceki skor trendinden kopup kopmadigini izlemek icindir. |
| Evidence driver | `ANA_SINYAL`, `PRIMARY_SINYAL_P_DEGERI`, `EVIDENCE_DRIVER`, `EVIDENCE_CONFLICT_FLAG` | Final karar anlatiminda aktiftir. En guclu sinyal p-value/effect yonu ve customer-peer uyumuna gore secilir. | Reason cumlesinin hangi kanita dayandigini ve sinyaller arasi konflikt olup olmadigini gosterir. |
| Final karar | `ANOMALI_FLAG`, `ANOMALI_ETIKETI`, `ANOMALI_SKORU`, `ANOMALI_NEDENI` | Scoring ayinin karar sonucudur. `ANOMALI_SKORU` final etiketle uyumlu operasyonel risk skorudur; ham sinyal gucu sinyal skor kolonlarinda kalir. Detail satirlarinda seri gorunumu icin gecmis aylar da bulunur; flag scoring ay disinda 0 kalir. | Operasyonel karar, skor, yon ve okunabilir reason'i tasir. |

Asagidaki alias tablosu teknik kolon eslemesini ve kisa anlamini verir; kolonun karara nasil girdigi icin yukaridaki kullanim sozlugu esas alinmalidir.

| Cikti kolonu | Oracle kolonu | Kisa anlam |
|---|---|---|
| Ham input kolonlari | Inputtaki ad | Kaynaktan gelen kolonlar; mevcut fatura datasinda `SUBE_KD`, `MUSTERINO`, `SEGMENTAD`, `DONEM_AY`, `REF_ALTFAALIYET`, `AKTIF_ABONE`, `FATURA_TTR`, `TURNOVER_AMT`. |
| ANA_METRIK_EKSIK_MI | ANA_MET_EKSIK_FLG | O ay ana metrik degeri kaynakta yok mu. |
| ORAN_PAY_KOLON | ORAN_PAY_KOL | Feature-ratio pay kolonu. |
| ORAN_PAYDA_KOLON | ORAN_PAYDA_KOL | Feature-ratio payda kolonu. |
| MUSTERI_ANA_METRIK_PAYDA_ORANI | MUS_ANA_MET_PAYDA_ORAN | Musteri ana metrik / referans feature orani. |
| MUSTERI_TOPLAM_AY_ADET | MUS_TOP_AY_ADET | Musterinin kaynakta gozlenen toplam ay adedi. |
| ONCEKI_AYA_GAP | ONC_AY_GAP | Bu satirdaki ayin onceki gozleme uzakligi. |
| PEER_SEVIYE | PEER_SEVIYE | Secilen peer seviyesinin adi. |
| PEER_KOLONLARI | PEER_KOLONLAR | Secilen peer'i olusturan kolonlar. |
| PEER_AYLIK_MUSTERI_ADET | PEER_AY_MUS_ADET | Ayni ay secilen peer icindeki musteri adedi. |
| PEER_AYLIK_SATIR_ADET | PEER_AY_SATIR_ADET | Ayni ay secilen peer icindeki satir adedi. |
| PEER_AYLIK_ANA_METRIK_MEDYAN | PEER_AY_ANA_MET_MED | Ayni ay peer ana metrik medyani. |
| PEER_AYLIK_ANA_METRIK_ORTALAMA | PEER_AY_ANA_MET_ORT | Ayni ay peer ana metrik ortalamasi; karar driver'i degil, analiz kolonudur. |
| PEER_AYLIK_ORAN_PAYDA_MEDYAN | PEER_AY_PAYDA_MEDYAN | Ayni ay peer referans feature medyani. |
| PEER_AYLIK_ANA_METRIK_PAYDA_ORAN_MEDYAN | PEER_AY_ANA_MET_PAYDA_MED | Ayni ay peer ana metrik / referans feature oran medyani. |
| MUSTERI_PEER_ANA_METRIK_ORANI | MUS_PEER_ANA_MET_ORAN | Musteri ana metrik / peer ay medyani orani. |
| MUSTERI_PEER_ORAN_PCTL | MUS_PEER_ORAN_PCTL | Musterinin peer oran dagilimindaki percentile'i. |
| MUSTERI_PEER_ORAN_REF_N | MUS_PEER_ORAN_REFN | Peer oran percentile hesabindaki referans gozlem sayisi. |
| AYLIK_YORUM | AYLIK_YORUM | O ay satiri icin okunabilir seri yorumu. |
| ANOMALI_SKORU | ANOMALI_SKORU | Operasyonel anomaly risk skoru; ay ici persentile kalibre edilir ve final etiketle uyumludur. |
| GUVEN_SKORU | GUVEN_SKORU | Karar guven skoru. |
| ANOMALI_ETIKETI | ANOMALI_ETIKETI | Final etiket: normal, watchlist veya anomaly tipi. |
| ANOMALI_YONU | ANOMALI_YONU | Yuksek/dusuk ana metrik yonu. |
| OPERASYON_KARARI | OPERASYON_KARARI | Operasyonel karar sinifi. |
| AKSIYON_KARARI | AKSIYON_KARARI | Aksiyon/review etiketi. |
| KANIT_GUCU | KANIT_GUCU | Kanit gucu: weak/medium/strong/normal. |
| SINYAL_TUTARLILIGI | SINYAL_TUTAR | Customer ve peer sinyallerinin tutarlilik durumu. |
| PEER_UYUM_DURUMU | PEER_UYUM | Musteri davranisi ile peer davranisi uyum durumu. |
| PEER_FARK_YONU | PEER_FARK_YON | Peer'e gore fark yonu. |
| PEER_FARK_Z | PEER_FARK_Z | Peer fark robust z skoru. |
| MUSTERI_FARK_Z | MUS_FARK_Z | Musterinin kendi tarihine gore fark z skoru. |
| VERI_YETERLILIK_DURUMU | VERI_YETER_DRM | Scoring icin veri yeterlilik sinifi. |
| ANOMALI_NEDENI | ANOMALI_NEDENI | Human-readable reason. |
| ANA_SINYAL | ANA_SINYAL | Karari en cok aciklayan ana sinyal. |
| ANA_SINYAL_Z | ANA_SINYAL_Z | Ana sinyal z skoru. |
| ANA_SINYAL_SKORU | ANA_SINYAL_SKOR | Ana sinyal evidence skoru. |
| SKORLANAN_ANA_METRIK | SKOR_ANA_MET | Scoring ayindaki gercek ana metrik. |
| BEKLENEN_ANA_METRIK | BEKLENEN_ANA_MET | Modelin referans/beklenen ana metrik seviyesi. |
| PEER_GUNCEL_MEDYAN_ANA_METRIK | PEER_GUNCEL_ANA_MET_MED | Scoring ayinda peer medyan ana metrik. |
| MUSTERI_GECMIS_MEDYAN_ANA_METRIK | MUS_GECMIS_ANA_MET_MED | Musterinin gecmis medyan ana metrik seviyesi. |
| MUSTERI_TREND_BEKLENEN_ANA_METRIK | MUS_TREND_BEK_ANA_MET | Musteri trendinden beklenen ana metrik. |
| MUSTERI_SEZON_BEKLENEN_ANA_METRIK | MUS_SEZON_BEK_ANA_MET | Musteri sezonundan beklenen ana metrik. |
| MUSTERI_SON3_AY_MEDYAN_ANA_METRIK | MUS_SON3_ANA_MET_MED | Musterinin son 3 ay medyan ana metrik seviyesi. |
| MUSTERI_SON3_AY_RANGE_LOG | MUS_SON3_RANGE_LOG | Son 3 ay log range; recent regime stabilitesi. |
| PEER_TREND_BEKLENEN_ANA_METRIK | PEER_TREND_BEK_ANA_MET | Peer trendinden beklenen ana metrik. |
| GERCEK_BEKLENEN_ORANI | GERCEK_BEK_ORAN | Gercek ana metrik / beklenen ana metrik orani. |
| GECMIS_PEER_Z | GECMIS_PEER_Z | Gecmis peer beklentisine gore z skoru. |
| GECMIS_PEER_SKORU | GECMIS_PEER_SKOR | Gecmis peer evidence skoru. |
| GUNCEL_PEER_Z | GUNCEL_PEER_Z | Ayni ay peer medyanina gore z skoru. |
| GUNCEL_PEER_SKORU | GUNCEL_PEER_SKOR | Ayni ay peer evidence skoru. |
| PEER_TREND_Z | PEER_TREND_Z | Peer trend beklentisine gore z skoru. |
| PEER_TREND_SKORU | PEER_TREND_SKOR | Peer trend evidence skoru. |
| FEATURE_ORAN_Z | FEAT_ORAN_Z | Ana metrik / referans feature oran z skoru. |
| FEATURE_ORAN_SKORU | FEAT_ORAN_SKOR | Feature-ratio evidence skoru. |
| FEATURE_ORAN_SINYAL_ISTENDI | FEAT_ORAN_ISTENDI | Config'te ratio sinyalinin istenip istenmedigi. |
| FEATURE_ORAN_GLOBAL_GATE_GECTI | FEAT_GLB_GATE_FLG | Global ratio kalite gate sonucu. |
| FEATURE_ORAN_GLOBAL_GATE_NEDENI | FEAT_GLB_GATE_NEDEN | Global ratio gate gecmeme nedeni. |
| FEATURE_ORAN_PEER_GATE_GECTI | FEAT_PEER_GATE_FLG | Peer ici ratio kalite gate sonucu. |
| FEATURE_ORAN_PEER_GATE_NEDENI | FEAT_PEER_GATE_NEDEN | Peer ratio gate gecmeme nedeni. |
| MUSTERI_GECMIS_Z | MUS_GECMIS_Z | Musteri gecmis medyanina gore z skoru. |
| MUSTERI_GECMIS_SKORU | MUS_GECMIS_SKOR | Musteri gecmis evidence skoru. |
| MUSTERI_TREND_Z | MUS_TREND_Z | Musteri trendine gore z skoru. |
| MUSTERI_TREND_SKORU | MUS_TREND_SKOR | Musteri trend evidence skoru. |
| MUSTERI_SEZON_Z | MUS_SEZON_Z | Musteri sezon beklentisine gore z skoru. |
| MUSTERI_SEZON_SKORU | MUS_SEZON_SKOR | Musteri sezon evidence skoru. |
| MUSTERI_SON3_REJIM_Z | MUS_SON3_REJIM_Z | Stabil son 3 ay rejimine gore z skoru. |
| MUSTERI_SON3_REJIM_SKORU | MUS_SON3_REJIM_SKOR | Son 3 ay rejim evidence skoru. |
| MUSTERI_SINYAL_SKORU | MUS_SINYAL_SKOR | Customer family konsolide sinyal skoru. |
| PEER_SINYAL_SKORU | PEER_SINYAL_SKOR | Peer family konsolide sinyal skoru. |
| MUSTERI_AILE_P_DEGERI | MUS_AILE_P | Customer family p-value. |
| PEER_AILE_P_DEGERI | PEER_AILE_P | Peer family p-value. |
| MUSTERI_AILE_YONU | MUS_AILE_YON | Customer family anomaly yonu. |
| PEER_AILE_YONU | PEER_AILE_YON | Peer family anomaly yonu. |
| MUSTERI_GUVENILIRLIK_DURUMU | MUS_GUVEN_DRM | Customer history guvenilirlik sinifi. |
| PEER_GUVENILIRLIK_DURUMU | PEER_GUVEN_DRM | Peer guvenilirlik sinifi. |
| PRIMARY_SINYAL_P_DEGERI | PRIM_SINYAL_P | Karardaki birincil sinyal p-value. |
| PRIMARY_SINYAL_SKORU | PRIM_SINYAL_SKOR | Karardaki birincil sinyal skoru. |
| SECONDARY_SINYAL_P_DEGERI | SEC_SINYAL_P | Ikincil destek sinyal p-value. |
| EVIDENCE_DRIVER | EVIDENCE_DRIVER | Final evidence driver ailesi. |
| EVIDENCE_CONFLICT_FLAG | EVID_CONFLICT_FLG | Customer-peer sinyal konflikti var mi. |
| MUSTERI_ACIKLANABILIRLIK_SKORU | MUS_ACIK_SKOR | Musterinin kendi datasiyla aciklanabilirlik skoru. |
| MUSTERI_ACIKLANABILIRLIK_DURUMU | MUS_ACIK_DRM | Musteri aciklanabilirlik durumu. |
| PEER_TEMSIL_SKORU | PEER_TEMSIL_SKOR | Peer'in musteriyi temsil skoru. |
| PEER_TEMSIL_DURUMU | PEER_TEMSIL_DRM | Peer temsil durumu. |
| PEER_OBJECTIVE_SKORU | PEER_OBJ_SKOR | Adaptif peer secimi objective skoru. |
| PEER_DESTEK_SKORU | PEER_DESTEK_SKOR | Peer support skoru. |
| PEER_STABILITE_SKORU | PEER_STABIL_SKOR | Peer stabilite skoru. |
| PEER_SPESIFIKLIK_SKORU | PEER_SPES_SKOR | Peer kiriliminin detay/oncelik skoru. |
| PEER_UYGUN_ADAY_ADET | PEER_ADAY_ADET | Uygun peer aday adedi. |
| PEER_DAGILIM_SKORU | PEER_DAGILIM_SKOR | Peer dagilim kalite skoru. |
| PEER_DAGILIM_DURUMU | PEER_DAGILIM_DRM | Peer dagilim kalite durumu. |
| PEER_LOG_ORAN_SKEW | PEER_LOG_SKEW | Peer log oran dagilimi skew. |
| PEER_LOG_ORAN_KURTOSIS | PEER_LOG_KURT | Peer log oran dagilimi kurtosis. |
| PEER_TAIL_RATE | PEER_TAIL_RATE | Peer robust tail rate. |
| PEER_MEAN_MEDYAN_ORANI | PEER_MM_ORAN | Peer raw ana metrik ortalama/medyan orani; heavy-tail kalite cezasinda kullanilir. |
| PEER_MEAN_MEDYAN_GAP_LOG | PEER_MM_GAP_LOG | Peer ortalama/medyan oraninin log mutlak farki. |
| PEER_STD_MEDYAN_ORANI | PEER_STD_MED_ORAN | Peer raw ana metrik std/medyan orani; oynaklik kalite cezasinda kullanilir. |
| PEER_GUNCEL_IQR_LOG | PEER_GUN_IQR_LOG | Scoring ayinda secilen peer'in log ana metrik IQR genisligi. Dagilim kalitesinde aktif kullanilir. |
| PEER_GECMIS_AYLIK_IQR_LOG | PEER_GEC_IQR_LOG | Gecmis aylarda secilen peer'in aylik log IQR medyani. Tipik peer genisligini olcer. |
| PEER_GUNCEL_MAD_LOG | PEER_GUN_MAD_LOG | Scoring ayinda secilen peer'in IQR'dan turetilen robust log MAD yaklasimi. Dagilim kalitesinde aktif kullanilir. |
| PEER_GECMIS_AYLIK_MAD_LOG | PEER_GEC_MAD_LOG | Gecmis aylarda secilen peer'in aylik robust log MAD medyani. Dagilim kalitesinde aktif kullanilir. |
| PEER_SECIM_GEREKCESI | PEER_SECIM_NEDEN | Secilen peer'in gerekcesi. |
| SKORLAMA_STRATEJISI | SKOR_STRATEJI | Customer-first/peer fallback scoring stratejisi. |
| MODEL_CHALLENGER_SKORU | CHL_SKOR | Config `aggregate_methods` listesindeki challenger modellerinden hesaplanan aggregate skor; sadece scoring ay satirinda doludur. |
| MODEL_CHALLENGER_UYARI | CHL_UYARI | Challenger model yorumu; sadece scoring ay satirinda doludur. |
| PCA_CHALLENGER_SKORU | CHL_PCA_SKOR | PCA reconstruction-error percentile skoru; sadece scoring ay satirinda doludur. |
| IF_CHALLENGER_SKORU | CHL_IF_SKOR | Isolation Forest percentile skoru; sadece scoring ay satirinda doludur. |
| LOF_CHALLENGER_SKORU | CHL_LOF_SKOR | Local Outlier Factor percentile skoru; sadece scoring ay satirinda doludur. |
| PCA_CHALLENGER_ANOMALI_FLAG | CHL_PCA_FLG | PCA challenger flag; sadece scoring ay satirinda doludur. |
| IF_CHALLENGER_ANOMALI_FLAG | CHL_IF_FLG | Isolation Forest challenger flag; sadece scoring ay satirinda doludur. |
| LOF_CHALLENGER_ANOMALI_FLAG | CHL_LOF_FLG | Local Outlier Factor challenger flag; sadece scoring ay satirinda doludur. |
| PEER_KALIBRASYON_SKORU | PEER_KALIB_SKOR | Peer kalibrasyon skoru. |
| PEER_KALIBRASYON_AY_ADET | PEER_KALIB_AY_ADET | Kalibrasyonda kullanilan ay adedi. |
| PEER_KALIBRASYON_MEDYAN_ABS_RESIDUAL | PEER_KALIB_MED_ABS_RES | Kalibrasyon median absolute residual. |
| PEER_KALIBRASYON_INTERVAL_KAPSAMA | PEER_KALIB_INT_KAPS | Kalibrasyon robust interval coverage. |
| PEER_KALIBRASYON_FALSE_ALARM_ORANI | PEER_KALIB_FA_ORAN | Kalibrasyon false alarm orani. |
| PEER_GECMIS_ADET | PEER_GECMIS_ADET | Peer gecmis gozlem adedi. |
| PEER_SEZON_AY_ADET | PEER_SEZON_ADET | Peer ayni sezon/ay gozlem adedi. |
| PEER_RECENT_ADET | PEER_RECENT_ADET | Peer recent gozlem adedi. |
| PEER_GUNCEL_ADET | PEER_GUNCEL_ADET | Peer scoring ayi gozlem adedi. |
| PEER_FEATURE_ORAN_ADET | PEER_FEAT_ORAN_ADET | Peer feature-ratio gozlem adedi. |
| MUSTERI_GECMIS_AY_ADET | MUS_GECMIS_ADET | Musteri gecmis ay adedi. |
| SON_12_AY_ANA_METRIK_ADET | SON12_ANA_MET_ADET | Son 12 ayda ana metrik gozlem adedi. |
| MUSTERI_TREND_ADET | MUS_TREND_ADET | Musteri trend hesap gozlem adedi. |
| MUSTERI_SEZON_ADET | MUS_SEZON_ADET | Musteri sezon hesap gozlem adedi. |
| MUSTERI_SON3_AY_ADET | MUS_SON3_ADET | Son 3 ay rejim gozlem adedi. |
| SON_12_AY_KAPSAMA | SON12_KAPSAMA | Son 12 ay coverage orani. |
| SON_GAP_AY_ADET | SON_GAP_ADET | Scoring ayindan onceki son gap. |
| DATA_GAP_SKORU | DATA_GAP_SKOR | Kesiklik/data gap skoru. |
| YENI_MUSTERI_MI | YENI_MUS_FLG | Scoring ayinda yeni musteri flag'i. |
| KESIK_SERI_MI | KESIK_SERI_FLG | Musteri serisi kesikli mi. |
| MODEL_DOLDURMA_POLITIKASI | MODEL_DOLDURMA_POL | Ana metrik doldurma politikasi; model ana metrik imputasyonu yapmaz. |
| ONCEKI_SKOR_DONEM_AY | ONC_SKOR_DONEM | Onceki skor donemi. |
| ONCEKI_ANOMALI_SKORU | ONC_ANOMALI_SKOR | Onceki anomaly skoru. |
| ONCEKI_AYA_GORE_SKOR_FARKI | ONC_AY_SKOR_FARK | Onceki aya gore skor farki. |
| SKOR_TREND_DIAGNOSTIGI | SKOR_TREND_DIAG | Onceki skorla karsilastirma diagnostigi. |
| MODEL_DONEM_AY | MODEL_DONEM_AY | Bu run'da skorlanan ay. |
| ANOMALI_FLAG | ANOMALI_FLAG | Detail satirinda anomaly flag; scoring ay disinda 0 kalir. |

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
- Kullanilan feature set sadece fonksiyonel residual sinyallerden olusur: customer z, peer z, trend z, seasonal z, recent regime z ve referans feature oran z. Data gap, peer quality, final actual/expected ratio ve evidence/decision kolonlari challenger modele verilmez. Residual kolon yeterli finite coverage veya varyasyon tasimiyorsa model input'undan otomatik cikarilir.
- Aggregate challenger skoru config'teki `aggregate_methods` ile secilen modellerden hesaplanir; LOF ayri diagnostic olarak kalabilir.

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
- Behavior cluster production config'te peer adayi olarak acik; ancak anomaly sinyali degil, sadece peer secimini iyilestiren leakage-safe turevdir.
- Referans feature bucket'i tek q5 gibi sabit tutulmaz; q3/q4/q5/q8 adaylari objective icinde yaristirilir.
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

Output integrity kosullu kolonlari ayrica ele alir. Feature-ratio gate gecmediginde `FEATURE_ORAN_*`, son-3-ay rejimi kosulu olusmadiginda `MUSTERI_SON3_REJIM_*` ve ikincil sinyal yoksa `SECONDARY_SINYAL_P_DEGERI` bos kalabilir; bunlar `detail_expected_conditional_missing_columns` altinda PASS diagnostik olarak raporlanir, genel missing problemi sayilmaz.

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
