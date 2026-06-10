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

Raw ana metrik, raw segment ve raw musteri hacmi dogrudan modele verilmez. Kullanilan feature seti customer/peer z-score'lari, recent regime, data gap, peer quality ve actual/expected residual alanlaridir. Detail tabloda aggregate `MODEL_CHALLENGER_SKORU`, model bazli `PCA/IF/LOF_CHALLENGER_SKORU`, `MODEL_CHALLENGER_UYARI` ve `PCA/IF/LOF_CHALLENGER_ANOMALI_FLAG` alanlari bulunur.

Challenger alanlari scoring ayina ait diagnostic'tir. Detail tablo musteri serisini gosterdigi icin bu kolonlar yalniz `DONEM_AY = MODEL_DONEM_AY` satirinda doludur; gecmis ay satirlarinda bos kalir.

## Oracle Veri Sozlugu

Oracle identifier limiti nedeniyle modelin urettigi uzun kolonlar kontrollu kisa aliaslarla yazilir. CSV/local output kolonlari human-readable kalabilir; Oracle yaziminda asagidaki alias sozlugu kullanilir. Raw input kolonlari mumkun oldugu surece inputtaki adiyla korunur.

### Decision Table

Decision tablo tek satir = scoring ayindaki musteri olacak sekilde tasarlanir. Kolon seti: ham input kolonlari + karar alanlari.

| Cikti kolonu | Oracle kolonu | Anlam |
|---|---|---|
| Ham input kolonlari | Inputtaki ad | Kaynaktan gelen ve decision output'a tasinan kolonlar; ornek: `MUSTERINO`, `DONEM_AY`, ana metrik, segment/faaliyet/sube gibi kolonlar. |
| ANOMALI_FLAG | ANOMALI_FLAG | 1 ise scoring ayinda anomaly/watchlist karari var, 0 ise yok. |
| ANOMALI_NEDENI | ANOMALI_NEDENI | Human-readable karar nedeni. |

### Detail Table

Detail tablo tek satir = skorlanan musterinin ilgili ay satiri olacak sekilde musteri serisini ve scoring ay karar kanitlarini tasir.

Fiziksel kolon sirasi asagidaki gruplara gore uretilir:

1. Ham input kolonlari
2. Seri ve data quality alanlari
3. Peer kimligi ve aylik peer metrikleri
4. Scoring ay beklenen/gercek ana metrik alanlari
5. Musteri sinyalleri
6. Peer sinyalleri
7. Peer kalite ve kalibrasyon alanlari
8. Davranis/behavior alanlari
9. Challenger model skor ve flag alanlari
10. Onceki skor diagnostigi
11. Evidence driver alanlari
12. Final karar, flag, skor ve reason alanlari

| Cikti kolonu | Oracle kolonu | Anlam |
|---|---|---|
| Ham input kolonlari | Inputtaki ad | Kaynaktan gelen kolonlar; mevcut fatura datasinda `SUBE_KD`, `MUSTERINO`, `SEGMENTAD`, `DONEM_AY`, `REF_ALTFAALIYET`, `AKTIF_ABONE`, `FATURA_TTR`, `TURNOVER_AMT`. |
| ANA_METRIK_EKSIK_MI | AM_EKSIK_FLG | O ay ana metrik degeri kaynakta yok mu. |
| ORAN_PAY_KOLON | ORAN_PAY_KOL | Feature-ratio pay kolonu. |
| ORAN_PAYDA_KOLON | ORAN_PAYDA_KOL | Feature-ratio payda kolonu. |
| MUSTERI_ANA_METRIK_PAYDA_ORANI | MUS_AM_PAYDA_ORAN | Musteri ana metrik / referans feature orani. |
| MUSTERI_TOPLAM_AY_ADET | MUS_TOP_AY_ADET | Musterinin kaynakta gozlenen toplam ay adedi. |
| ONCEKI_AYA_GAP | ONC_AY_GAP | Bu satirdaki ayin onceki gozleme uzakligi. |
| PEER_SEVIYE | PEER_SEVIYE | Secilen peer seviyesinin adi. |
| PEER_KOLONLARI | PEER_KOLONLAR | Secilen peer'i olusturan kolonlar. |
| PEER_AYLIK_MUSTERI_ADET | PEER_AY_MUS_ADET | Ayni ay secilen peer icindeki musteri adedi. |
| PEER_AYLIK_SATIR_ADET | PEER_AY_SATIR_ADET | Ayni ay secilen peer icindeki satir adedi. |
| PEER_AYLIK_ANA_METRIK_MEDYAN | PEER_AY_AM_MEDYAN | Ayni ay peer ana metrik medyani. |
| PEER_AYLIK_ANA_METRIK_ORTALAMA | PEER_AY_AM_ORT | Ayni ay peer ana metrik ortalamasi; karar driver'i degil, analiz kolonudur. |
| PEER_AYLIK_ORAN_PAYDA_MEDYAN | PEER_AY_PAYDA_MEDYAN | Ayni ay peer referans feature medyani. |
| PEER_AYLIK_ANA_METRIK_PAYDA_ORAN_MEDYAN | PEER_AY_AM_PAYDA_MED | Ayni ay peer ana metrik / referans feature oran medyani. |
| MUSTERI_PEER_ANA_METRIK_ORANI | MUS_PEER_AM_ORAN | Musteri ana metrik / peer ay medyani orani. |
| MUSTERI_PEER_ORAN_PCTL | MUS_PEER_ORAN_PCTL | Musterinin peer oran dagilimindaki percentile'i. |
| MUSTERI_PEER_ORAN_REF_N | MUS_PEER_ORAN_REFN | Peer oran percentile hesabindaki referans gozlem sayisi. |
| AYLIK_YORUM | AYLIK_YORUM | O ay satiri icin okunabilir seri yorumu. |
| ANOMALI_SKORU | ANOMALI_SKORU | Final robust anomaly skoru; scoring ay satirinda doludur. |
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
| SKORLANAN_ANA_METRIK | SKOR_AM | Scoring ayindaki gercek ana metrik. |
| BEKLENEN_ANA_METRIK | BEKLENEN_AM | Modelin referans/beklenen ana metrik seviyesi. |
| PEER_GUNCEL_MEDYAN_ANA_METRIK | PEER_GUNCEL_AM_MED | Scoring ayinda peer medyan ana metrik. |
| MUSTERI_GECMIS_MEDYAN_ANA_METRIK | MUS_GECMIS_AM_MED | Musterinin gecmis medyan ana metrik seviyesi. |
| MUSTERI_TREND_BEKLENEN_ANA_METRIK | MUS_TREND_BEK_AM | Musteri trendinden beklenen ana metrik. |
| MUSTERI_SEZON_BEKLENEN_ANA_METRIK | MUS_SEZON_BEK_AM | Musteri sezonundan beklenen ana metrik. |
| MUSTERI_SON3_AY_MEDYAN_ANA_METRIK | MUS_SON3_AM_MED | Musterinin son 3 ay medyan ana metrik seviyesi. |
| MUSTERI_SON3_AY_RANGE_LOG | MUS_SON3_RANGE_LOG | Son 3 ay log range; recent regime stabilitesi. |
| PEER_TREND_BEKLENEN_ANA_METRIK | PEER_TREND_BEK_AM | Peer trendinden beklenen ana metrik. |
| GERCEK_BEKLENEN_ORANI | GERCEK_BEK_ORAN | Gercek ana metrik / beklenen ana metrik orani. |
| GECMIS_PEER_Z | GECMIS_PEER_Z | Gecmis peer beklentisine gore z skoru. |
| GECMIS_PEER_SKORU | GECMIS_PEER_SKOR | Gecmis peer evidence skoru. |
| GUNCEL_PEER_Z | GUNCEL_PEER_Z | Ayni ay peer medyanina gore z skoru. |
| GUNCEL_PEER_SKORU | GUNCEL_PEER_SKOR | Ayni ay peer evidence skoru. |
| PEER_TREND_Z | PEER_TREND_Z | Peer trend beklentisine gore z skoru. |
| PEER_TREND_SKORU | PEER_TREND_SKOR | Peer trend evidence skoru. |
| FEATURE_ORAN_Z | FT_ORAN_Z | Ana metrik / referans feature oran z skoru. |
| FEATURE_ORAN_SKORU | FT_ORAN_SKOR | Feature-ratio evidence skoru. |
| FEATURE_ORAN_SINYAL_ISTENDI | FT_ORAN_ISTENDI | Config'te ratio sinyalinin istenip istenmedigi. |
| FEATURE_ORAN_GLOBAL_GATE_GECTI | FT_GLB_GATE_FLG | Global ratio kalite gate sonucu. |
| FEATURE_ORAN_GLOBAL_GATE_NEDENI | FT_GLB_GATE_NEDEN | Global ratio gate gecmeme nedeni. |
| FEATURE_ORAN_PEER_GATE_GECTI | FT_PEER_GATE_FLG | Peer ici ratio kalite gate sonucu. |
| FEATURE_ORAN_PEER_GATE_NEDENI | FT_PEER_GATE_NEDEN | Peer ratio gate gecmeme nedeni. |
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
| PEER_SECIM_GEREKCESI | PEER_SECIM_NEDEN | Secilen peer'in gerekcesi. |
| SKORLAMA_STRATEJISI | SKOR_STRATEJI | Customer-first/peer fallback scoring stratejisi. |
| MODEL_CHALLENGER_SKORU | CHL_SKOR | PCA/IF/LOF aggregate challenger skoru; sadece scoring ay satirinda doludur. |
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
| PEER_FEATURE_ORAN_ADET | PEER_FT_ORAN_ADET | Peer feature-ratio gozlem adedi. |
| MUSTERI_GECMIS_AY_ADET | MUS_GECMIS_ADET | Musteri gecmis ay adedi. |
| SON_12_AY_ANA_METRIK_ADET | SON12_AM_ADET | Son 12 ayda ana metrik gozlem adedi. |
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
