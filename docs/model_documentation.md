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

Peer adaylari `variables.segment_variables` listesinden ve `model.derived_features` ile aktif edilen generic bucket'lardan otomatik uretilir. Production config artik proses-spesifik `explicit_levels` tasimaz; engine aday lattice'ini kurar, destek/kalibrasyon/dagilim/objective skorlarina gore en iyi peer'i secer.

`segment_variables: auto` modunda proses-spesifik peer kolonlari kodda veya config'te tek tek hardcoded tutulmaz. Engine once role kolonlarini (`id_variables`, `time_variables`, `main_feature`, referans feature, exposure feature) ve `exclude_variables` listesini ayirir; sonra kalan kolonlardan dusuk/orta cardinality ve bilgi tasiyan adaylari infer eder. Tekil degerli veya cok yuksek cardinality kolonlar peer adayina girmez. Bu mod POS cirosu gibi yeni ana metriklerde ayni engine'i yeniden kullanmak icindir.

Otomatik aday setinde su aileler denenir:

- Segment adaylari: `variables.segment_variables` icindeki kolonlarin tekli/coklu kombinasyonlari.
- Auto segment adaylari: `segment_variables: auto` ise uygun cardinality'li kolonlar runtime'da infer edilir.
- Referans feature bucket adaylari: aktif `feature_ratio` denominator'i history uzerinden bucket'lanir ve segment adaylariyla yaristirilir.
- Davranis adaylari: `behavior_peer` aktifse level/volatilite/trend bucket'lari ve cluster, scoring ayindan onceki musteri gecmisiyle uretilir.
- Global fallback: hicbir aday destek gecmezse global peer denenir.

`behavior_*` alanlari scoring ayini kullanmadan, yalnizca scoring ayindan onceki musteri gecmisinden uretilir. Bu nedenle peer secimine musteri davranis seviyesini katar ama scoring ayina leakage yaratmaz.

Referans feature bucket'i tek sabit kirilim degildir. Engine varsayilan olarak birden fazla quantile cozumunu history uzerinden fit eder, scoring ayina aynen uygular ve her varyanti peer objective icinde ayri aday olarak yaristirir. Config'te q3/q4/q5/q8 gibi uretilmis kolonlari yazmak gerekmez; yalnizca referans feature'in hangi kolon oldugu belirtilir.

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

2026-03 denemesinde behavior ve referans feature bucket adaylari peer objective icinde yaristirildi; secili peer ortalama dagilim skoru 47.49 oldu. Bu deger hala mukemmel homojenlik degil; bu nedenle peer quality raporu ve customer-first karar mantigi production'da korunur.

`calibration` peer'in gecmis aylarda bir sonraki ay referansi olarak ne kadar iyi calistigini olcer. Scoring ayi kullanilmaz. Scoring ayindan onceki son kalibrasyon aylarinda peer aylik medyani, yalnizca daha onceki aylarla kurulan expected degere gore test edilir. Bilesenleri:

- Median absolute residual: dusukse iyi, agirlik 0.45
- Robust interval coverage: `abs(residual) <= 3` orani, agirlik 0.35
- False alarm rate: `abs(residual) >= 2.5` orani, ters yonlu agirlik 0.20

`stability` peer'in tarihsel medyani ile son 3 ay medyani arasindaki kaymayi ve MAD degisimini cezalandirir. `specificity` daha dar ama destek gecen peer'leri odullendirir. `support` hist/season/recent/current adetlerini ayri bir destek skoru olarak toplar.

`PEER_SPESIFIKLIK_SKORU`, secilen peer kiriliminin ne kadar detayli ve oncelikli degiskenlerden olustugunu gosterir. Hesap mantigi:

- Peer kolonu yoksa skor dusuk baz seviye alir.
- Daha fazla peer kolonu kullanildikca `depth` artar.
- Config auto modda ise `variables.segment_variables` ve aktif derived peer degiskenlerinin sirasi priority hesaplamasinda kullanilir.
- `peer_selection.max_candidate_count` sadece auto/genis kolon setlerinde performans koruma limitidir; objective skoru degistirmez, denenecek aday havuzunu sirali ilk N adayla sinirlar ve global fallback'i korur.
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

Peer bucket'lar anomaly sinyali degil, peer adayidir. Oran sinyali kalite gate gecmezse final skora girmez; buna ragmen referans feature bucket'i peer seciminde kullanilabilir. Bunun sebebi denominator'in musteri hacmini temsil ederek peer'i daraltabilmesidir. Ancak hangi bucket cozumunun kullanilacagina sabit kural karar vermez; peer objective karar verir.

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

Oracle identifier limiti nedeniyle modelin urettigi uzun kolonlar Oracle yaziminda dinamik kisaltilir. Ayrica kolon-alias sozlugu tutulmaz: 30 karakteri asmayan kolonlar aynen kalir, uzun kolonlar deterministic hash suffix ile otomatik kisalir. Raw input kolonlari mumkun oldugu surece inputtaki adiyla korunur.

### Decision Table

Decision tablo tek satir = scoring ayindaki musteri olacak sekilde tasarlanir. Kolon seti: ham input kolonlari + karar alanlari.

| Cikti kolonu | Oracle kolonu | Anlam | Modelde kullanim / kosul |
|---|---|---|---|
| Ham input kolonlari | Inputtaki ad | Kaynaktan gelen ve decision output'a tasinan kolonlar; ornek: `MUSTERINO`, `DONEM_AY`, ana metrik, segment/faaliyet/sube gibi kolonlar. | Decision table karar tuketimi icindir; bu kolonlar karar nedenini ham kaynak satiriyla baglamak, downstream join yapmak ve kullanicinin skorlanan gercek degeri gormesi icin korunur. |
| ANOMALI_FLAG | ANOMALI_FLAG | 1 ise scoring ayinda anomaly/watchlist karari var, 0 ise yok. | Final karar kolonudur. Robust evidence-first sisteminden gelir; challenger modeller bu flag'i dogrudan degistirmez. |
| ANOMALI_SKORU | ANOMALI_SKORU | Nihai operasyonel anomaly skoru. | 0-100 arasi final karar skorudur. Ham p-value skoru degildir; final etiketle uyumlu ay ici risk skorudur. |
| ANOMALI_NEDENI | ANOMALI_NEDENI | Human-readable karar nedeni. | Final reason kolonudur. Ana sinyal, beklenen/gercek farki, musteri/peer guvenilirligi ve veri yeterlilik durumuna gore uretilir; decision reason metninde guven yuzdesi yazilmaz. Guven detayi detail tablosundaki `GUVEN_SKORU` alaninda kalir. |

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
| Challenger diagnostic | `model_challenger_score`, `pca_challenger_score`, `if_challenger_score`, `lof_challenger_score`, `*_challenger_anomaly_flag` | Sadece scoring ay satirinda doludur. Production flag'i degistirmez; sadece ana metrik residual transformasyonlariyla ek kontrol saglar. | Robust sistemle uyumlu/uyumsuz model davranisini izlemek ve challenger adaylarini takip etmek icindir; rule-derived karar kolonlari modele verilmez. |
| Peer kalite ve kalibrasyon | `PEER_TEMSIL_SKORU`, `PEER_OBJECTIVE_SKORU`, `PEER_DAGILIM_SKORU`, `PEER_KALIBRASYON_SKORU` | Peer seciminde ve karar guveninde aktiftir. Support, dagilim, spesifiklik, stabilite ve rolling OOT kalibrasyon kriterleri birlikte degerlendirilir. | Secilen peer gercekten temsil edici mi, yoksa peer kaynakli karar dikkatle mi okunmali sorusunu cevaplar. |
| Davranis/behavior | `DAVRANIS_CLUSTER`, `DAVRANIS_*` | Config'te behavior peer aktifse peer aday genisletmede kullanilir; kapaliyken karar sinyali degildir. | Musterinin seviye/volatilite/trend davranisini diagnostic olarak izler. |
| Onceki skor diagnostigi | `ONCEKI_ANOMALI_SKORU`, `ONCEKI_AYA_GORE_SKOR_FARKI`, `SKOR_TREND_DIAGNOSTIGI` | Karar driver'i degildir; bias yaratmamak icin final flag'i tek basina degistirmez. | Bu ayki skorun onceki skor trendinden kopup kopmadigini izlemek icindir. |
| Evidence driver | `ANA_SINYAL`, `PRIMARY_SINYAL_P_DEGERI`, `EVIDENCE_DRIVER`, `EVIDENCE_CONFLICT_FLAG` | Final karar anlatiminda aktiftir. En guclu sinyal p-value/effect yonu ve customer-peer uyumuna gore secilir. | Reason cumlesinin hangi kanita dayandigini ve sinyaller arasi konflikt olup olmadigini gosterir. |
| Final karar | `ANOMALI_FLAG`, `ANOMALI_ETIKETI`, `ANOMALI_SKORU`, `ANOMALI_NEDENI` | Scoring ayinin karar sonucudur. `ANOMALI_SKORU` final etiketle uyumlu operasyonel risk skorudur; ham sinyal gucu sinyal skor kolonlarinda kalir. Detail satirlarinda seri gorunumu icin gecmis aylar da bulunur; flag scoring ay disinda 0 kalir. | Operasyonel karar, skor, yon ve okunabilir reason'i tasir. |

Oracle teknik kolon adlari icin statik alias tablosu tutulmaz. Yazim sirasinda kolon adlari su kuralla uretilir:

- 30 karakteri asmayan ve Oracle uyumlu kolonlar aynen kalir.
- 30 karakteri asan kolonlar `ILK_PARCA_HASH` formatinda deterministic kisaltilir.
- Ayni run icinde olasi cakismalar otomatik suffix ile ayrilir.
- Long text kolonlari isim paternine ve gercek string uzunluguna gore CLOB yazilir.

Bu nedenle detail tablodaki diagnostic kolon seti modelin o run'da urettigi alanlara gore dinamik genisler/daralir. Karar tuketimi icin sabit tutulmasi gereken alanlar asagidakilerdir:

| Kolon | Kisa anlam |
|---|---|
| Ham input kolonlari | Kaynaktan gelen kolonlar inputtaki adlariyla korunur. |
| ANOMALI_FLAG | Decision ve scoring ayi detail satirinda final anomaly/watchlist flag'i. |
| ANOMALI_SKORU | Nihai operasyonel anomaly skoru. |
| ANOMALI_NEDENI | Human-readable karar nedeni. |
| ANOMALI_ETIKETI | Detail icin final etiket. |
| ANOMALI_YONU | Detail icin yuksek/dusuk anomaly yonu. |
| GUVEN_SKORU | Detail icin karar guveni. Decision reason icinde yazilmaz. |
| MODEL_DONEM_AY | Detail tablosunda run scoring ayi. |

Challenger diagnostic kolonlari da sabit kisa aliaslarla degil, uretildigi internal adla gelir. Ornekler: `model_challenger_score`, `pca_challenger_score`, `if_challenger_score`, `lof_challenger_score`, `pca_challenger_anomaly_flag`, `if_challenger_anomaly_flag`, `lof_challenger_anomaly_flag`. Bu kolonlar yalniz scoring ay satirinda doludur.

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
