# Fatura Anomaly Implementation Handoff

Bu sistem target'siz aylik fatura anomalisi skorlar. Supervised target yoktur.

## Run Komutu

Bu handoff eski detaylari korur. Guncel metodoloji icin `docs/model_documentation.md`, guncel calistirma ve operasyon icin `docs/implementation_documentation.md` esas alinmalidir.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1 -SkipPeerQualityReport
```

```bash
./scripts/run_pipeline.sh --skip-peer-quality-report
```

Dogudan Python:

```bash
python3 src/configured_anomaly_pipeline.py --config configs/anomaly.yaml --skip-peer-quality-report
```

`--scoring-month last` dosyadaki son ayi implementasyon/OOT ayi kabul eder. Mevcut run'da bu ay `2026-03`.

## Klasor Duzeni

- `data/raw`: Ham input CSV dosyalari.
- `outputs/production/decision_table`: Implementasyon/aksiyon icin ana karar tablosu.
- `outputs/production/contracts`: Run sozlesmesi ve cikti path bilgisi.
- `outputs/analysis/decision_detail`: Musteri-ay detay kanit tablosu.
- `outputs/analysis/peer_quality_report`: Peer kalite Excel/HTML/CSV analizleri.
- `outputs/archive/old_runs`: Eski deneme ve backtest ciktilari.

## Ana Ciktilar

Sistem sadece iki ana tablo uretir.

### 1. Functional Decision Table

Path:

`outputs/production/decision_table/encrypted_final_anomaly_decisions_202603.csv`

Grain:

Tek satir = scoring ayindaki tek musteri.

Kullanim:

Decision making, aksiyon routing, dashboard, operasyon listesi.

Kolonlar:

- `SUBE_KD`
- `MUSTERINO`
- `SEGMENTAD`
- `DONEM_AY`
- `REF_ALTFAALIYET`
- `AKTIF_ABONE`
- `FATURA_TTR`
- `TURNOVER_AMT`
- `ANOMALI_FLAG`
- `ANOMALI_NEDENI`

Bu tablo decision-making icin yalindir. Skor, peer, guven, trend, sezon, temsil ve dagilim alanlari bu tabloda tutulmaz; hepsi detail evidence tablosundadir.

### 2. Detail Evidence Table

Path:

`outputs/analysis/decision_detail/encrypted_final_anomaly_decision_detail_202603.csv`

Grain:

Tek satir = scoring ayindaki musteriler icin musteri-ay.

Her scoring musteri icin detay paneli musterinin ilk goruldugu aydan scoring ayina kadar acilir. Yeni musteriye musteri olmadan onceki aylar yapay eksik ay olarak yazilmaz. Sadece ilk goruldugu ay ile scoring ayi arasindaki kopuk aylar `FATURA_EKSIK_MI` ile isaretlenir.

Kullanim:

Bir musteriye tiklayinca karar nasil olusmus diye analiz etmek.

Icerik:

- Musterinin aylik fatura serisi.
- Musterinin aylik turnover ve fatura/turnover bilgisi.
- Skor, guven, operasyon karari, anomali etiketi ve nedeni.
- Secilen peer grubunun ayni aylardaki medyan/ortalama fatura seviyesi.
- Musteri faturasinin peer medyanina orani.
- Musteri fatura/peer oraninin secili peer oran dagilimindaki persentili.
- Scoring ayinda modelin kullandigi tum z-score ve component score alanlari.
- Scoring ayinda human-readable karar nedeni.
- Gecmis ay satirlarinda ay bazli yorum.

Not:

Karar metrikleri ve karar cumlesi sadece skorlanan son ay satirinda doludur. Mevcut run icin bu satir `DONEM_AY = 202603` satiridir. Gecmis aylar seri ve peer kaniti olarak tutulur; ayni karar metni her aya tekrar yazilmaz.

## Model Mantigi

Fit scope:

Scoring ayindan onceki aylar kullanilir. Scoring ayi karar/veri olarak disarida tutulur ve sonradan skorlanir.

Amount filling:

Fatura tutari doldurulmaz. Musteri ilk gorulmeden onceki aylar detail paneline eklenmez. Ilk gorulen ay ile scoring ayi arasinda gelmeyen aylar eksik/kopuk seri olarak ayri alanlarda izlenir:

- `FATURA_EKSIK_MI`
- `SON_12_AY_KAPSAMA`
- `SON_GAP_AY_ADET`
- `DATA_GAP_SKORU`
- `YENI_MUSTERI_MI`
- `KESIK_SERI_MI`
- `VERI_YETERLILIK_DURUMU`

Customer-first layer:

- Musteri kendi gecmisi.
- Musteri trendi.
- Musteri ayni ay/sezonsal seviyesi.
- Bu uc bilesen bulunabiliyorsa musteri ic dunyasi ana karar kaynagidir.
- `MUSTERI_ACIKLANABILIRLIK_SKORU` musterinin kendi verisiyle ne kadar okunabildigini gosterir.
- Musteri yeterince aciklanabilirse final skor agirligi cogunlukla musteri bilesenlerine verilir; peer sadece destek/kontrast olarak kalir.

Peer layer:

- Peer adaptif olarak daraltilir.
- Siralama segment -> turnover/ciro -> faaliyet/sektor -> aktif abone bucket -> behavior cluster -> sube seklindedir.
- Sistem once en dar production-safe peer'i dener: `segment_turnover_sector_active_branch`.
- Destek yetersizse anlamli sirayla daha genis peer'e duser: segment+turnover+sector+active+behavior, segment+turnover+sector+behavior, segment+turnover+sector+active, segment+turnover+sector, segment+turnover+active, segment+turnover, segment+sector+active, segment+sector, segment+active, segment, sonra sector/turnover/active ve gerekirse global.
- Destek esikleri: gecmis >= 120, ayni ay/sezon >= 15, son donem >= 25, current ay peer >= 20.
- Secilen peer seviyesi `PEER_SEVIYE` ve `PEER_KOLONLARI` alanlarinda gorulur.
- `PEER_SECIM_GEREKCESI` hangi peer'in secildigini ve daha dar peer'lerin neden elendigini yazar.
- `PEER_TEMSIL_SKORU` ve `PEER_TEMSIL_DURUMU` secilen peer'in musteriyi temsil kabiliyetini ayrica skorlar.
- Detail tabloda peer'in her ay medyan faturasi `PEER_AYLIK_FATURA_MEDYAN` olarak gelir.
- Peer merkez ve sapma hesabi medyan + MAD ile yapilir; outlier satirlar peer ortalamasini bozsa bile ana skor bu ortalamaya dayanmaz.
- Peer trendi tekil satir ortalamasina degil, peer-ay medyan fatura serisine gore hesaplanir.

Behavior cluster:

- Behavior cluster musteri gecmis fatura davranisindan uretilir; scoring ayi kullanilmaz.
- Cluster deterministiktir, KMeans/black-box degildir.
- Kullanilan davranis kirilimlari:
  - `DAVRANIS_SEVIYE_BUCKET`: musteri gecmis medyan fatura seviyesi.
  - `DAVRANIS_VOLATILITE_BUCKET`: musteri gecmis log-fatura MAD volatilitesi.
  - `DAVRANIS_TREND_BUCKET`: musteri gecmis trend egimi.
- Bir musteri icin davranis cluster'i `DAVRANIS_CLUSTER` alaninda tutulur.
- Behavior cluster sadece yeterli musteri gecmisi varsa kullanilir; yetersizse `behavior_unknown` olur.
- Behavior'li peer adaylari destek esiklerini gecemezse otomatik olarak davranissiz daha genis peer'e duser.

Peer dagilim kalite layer:

- Ham fatura dagiliminin normal dagilmasi beklenmez.
- Ana karsilastirma `log(fatura)` uzerinde robust residual ile yapilir.
- Peer merkez parametresi: medyan.
- Peer sapma parametresi: `MAD * 1.4826`, minimum scale floor ile.
- Skor parametresi: modified robust z-score.
- Ortalama ve standart sapma karar motorunda ana parametre degildir; sadece detail tabloda referans/okuma icin vardir.
- `PEER_DAGILIM_SKORU`: secilen peer'in skew, kurtosis ve robust tail oranindan uretilen kalite skoru.
- `PEER_DAGILIM_DURUMU`: `HOMOGENEOUS_PEER`, `USABLE_HEAVY_TAIL_PEER`, `HEAVY_TAIL_PEER_REVIEW`.
- `PEER_TAIL_RATE`: peer icinde robust `|z| > 3` oranidir.
- Heavy-tail peer secildiginde peer guveni ve peer final agirligi dusurulur; musteri kendi verisiyle aciklanabiliyorsa musteri agirligi artar.

Final skor konsolidasyonu:

- Once musteri bilesenleri kendi icinde normalize edilir: gecmis, trend, sezon.
- Sonra peer bilesenleri kendi icinde normalize edilir: gecmis peer, guncel peer, peer trend, turnover yogunluk.
- Final musteri/peer agirligi musterinin aciklanabilirligine ve peer dagilim kalitesine gore belirlenir.
- `CUSTOMER_FIRST`: musteri verisi guclu; self-history/trend/sezon baskin.
- `CUSTOMER_PEER_HYBRID`: musteri verisi orta; peer ile birlikte karar verilir.
- `PEER_FIRST_WITH_CUSTOMER_CHECK`: musteri verisi sinirli; peer baskin ama musteri kontrolu vardir.
- `PEER_FIRST`: yeni veya cok kisitli musteri; karar peer temellidir.
- `PEER_FIRST_COARSE_REVIEW`: peer cok genis kalmistir; operasyonel review gerekir.
- Final component agirliklari ayri kolonlarda tutulur; toplam agirlik her scoring satirinda 1.0'dur.

Peer uyum layer:

- `PEER_UYUM_DURUMU`: Musterinin kendi davranisi ile secilen peer arasindaki yapisal uyumu anlatir.
- `PEER_FARK_YONU`: Peer'e gore ayrisma yonu (`LOW`, `HIGH`, `NONE`, `UNKNOWN`).
- `PEER_FARK_Z`: Peer tarafindaki en guclu signed z sinyali.
- `MUSTERI_FARK_Z`: Musterinin kendi gecmis/trend/sezonsal tarafindaki en guclu signed z sinyali.

Onemli durumlar:

- `PEER_UYUMLU`: Peer tarafinda belirgin fark yok.
- `MUSTERI_GECMISI_YETERSIZ`: Musteri icin yeterli self-history yok; karar peer agirliklidir.
- `YAPISAL_PEER_ALTINDA_MUSTERI_STABIL`: Musteri kendi icinde stabil ama peer'in altinda calisiyor; bu daha cok peer/segment atamasi supheli demektir.
- `YAPISAL_PEER_USTUNDE_MUSTERI_STABIL`: Musteri kendi icinde stabil ama peer'in ustunde calisiyor.
- `PEER_ALTINDA_VE_MUSTERI_ICI_DUSUS`: Hem peer'e gore dusuk hem musteri kendi trendine gore dusus var.
- `PEER_USTUNDE_VE_MUSTERI_ICI_YUKSELIS`: Hem peer'e gore yuksek hem musteri kendi trendine gore yukselis var.
- `PEER_MUSTERI_CELISKILI`: Peer ve musteri kendi sinyali ters yonlu konusuyor; review gerektirir.

Reason cumlesi:

- `ANOMALI_NEDENI` once ana nedeni yazar.
- Ana neden, karar yonuyle ayni yonde konusan en guclu sinyaldir.
- Detail metni sadece bu ana sinyale ait metrikleri icerir; tum musteri/peer ozeti bu cumlede tekrar edilmez.
- `NEDEN_KODLARI` ayri kolondur; destekleyici sinyaller orada tutulur.
- `ANA_SINYAL_Z` ana sinyalin yonunu ve buyuklugunu gosterir.

Detail aylik yorum:

- `MUSTERI_PEER_FATURA_ORANI`: Musteri faturasi / secili peer ay medyani.
- `MUSTERI_PEER_ORAN_PCTL`: Bu oranin ayni secili peer grubunun gecmis oran dagilimindaki persentili.
- `MUSTERI_PEER_ORAN_REF_N`: Persentil hesabinda kullanilan referans satir sayisi.
- `AYLIK_YORUM`: Sabit `0.33` gibi oran esikleriyle dusuk/yuksek demez; orani, peer icindeki persentili ve referans adedini yazar.

Score:

0-100 arasi robust residual score. Yuksek skor daha guclu ayrisma demektir.

Previous score:

Onceki ay skoru sadece diagnostiktir. Final skora girmez.

Isolation Forest notu:

Mevcut primary karar katmani Isolation Forest degildir. Sebep: bu problemde target yok ve kullaniciya hangi sinyalin neden anomali dedigini aciklamak gerekiyor. Bu yuzden primary sistem robust residual + musteri/peer bilesen agirliklariyla calisir. Isolation Forest ancak challenger/diagnostic kolon olarak eklenmelidir; final karar katmani yapilirsa reason kalitesi ve peer/self ayrimi zayiflar.

## Current Validation Snapshot

Mevcut `data/raw/encrypted_final.csv` run sonucu:

- Scoring ayi: `2026-03`
- Train rows: `775191`
- Scoring rows: `20478`
- Scored rows: `20478`
- Not scored rows: `0`
- Decision table rows: `20478`
- Detail table rows: `544741`
- Detail panel: musteri ilk goruldugu aydan scoring ayina kadar acilir; bu run'da musteri olmadan onceki aylar detail'a yazilmadigi icin eski 737208 satirlik global panel 544741 satira dustu.

Label dagilimi:

- `NORMAL`: 19863
- `WATCHLIST_LOW`: 280
- `WATCHLIST_HIGH`: 181
- `LOW_BILL_ANOMALY`: 102
- `HIGH_BILL_ANOMALY`: 52

Peer seviye dagilimi:

- `segment_turnover_sector_active`: 9322
- `segment_turnover_active`: 5988
- `segment_turnover_sector`: 3785
- `segment_turnover_sector_behavior`: 748
- `segment_turnover_sector_active_behavior`: 272
- `segment`: 152
- `segment_turnover`: 152
- `segment_active`: 30
- `global`: 29

Peer dagilim kalite dagilimi:

- `HOMOGENEOUS_PEER`: 12411
- `USABLE_HEAVY_TAIL_PEER`: 5429
- `HEAVY_TAIL_PEER_REVIEW`: 2638

Skorlama stratejisi dagilimi:

- `CUSTOMER_FIRST`: 18250
- `PEER_FIRST`: 914
- `PEER_FIRST_WITH_CUSTOMER_CHECK`: 723
- `CUSTOMER_PEER_HYBRID`: 586
- `PEER_FIRST_COARSE_REVIEW`: 5

Musteri aciklanabilirlik dagilimi:

- `CUSTOMER_SELF_EXPLAINABLE`: 18177
- `CUSTOMER_LIMITED_HISTORY`: 1069
- `CUSTOMER_NOT_EXPLAINABLE`: 919
- `CUSTOMER_PARTIAL_EXPLAINABLE`: 313

Peer temsil dagilimi:

- `STRONG_PEER_REPRESENTATION`: 12119
- `GOOD_PEER_REPRESENTATION`: 6249
- `MEDIUM_PEER_REPRESENTATION`: 1929
- `COARSE_PEER_REVIEW`: 181

Peer uyum dagilimi:

- `PEER_UYUMLU`: 17521
- `MUSTERI_GECMISI_YETERSIZ`: 2301
- `PEER_ALTINDA_VE_MUSTERI_ICI_DUSUS`: 211
- `YAPISAL_PEER_ALTINDA_MUSTERI_STABIL`: 186
- `YAPISAL_PEER_USTUNDE_MUSTERI_STABIL`: 92
- `PEER_MUSTERI_CELISKILI`: 64
- `PEER_USTUNDE_VE_MUSTERI_ICI_YUKSELIS`: 58
- `PEER_FARKI_VAR_MUSTERI_SINYALI_ORTA`: 45

Sube seviyesi bu run'da secili peer olarak kalmadi. Sistem subeyi en dar adaylarda dener; fakat secili musteriler icin sube kirilimi destek esiklerini genelde gecmedigi icin daha genis ve istatistiksel olarak guvenilir peer seviyelerine duser. Her musteri icin neden dar peer'in elendigi `PEER_SECIM_GEREKCESI` kolonunda yazilir.

Validasyon kontrolleri:

- Decision yasak kolon kontrolu: `customer_id`, `scoring_month`, `invoice_month`, `MODEL_DONEM_AY`, `ANOMALI_MI` yok.
- Detail run anahtari: `MODEL_DONEM_AY` var; Oracle `delete_insert` bu alanla ayni skor kosusunu siler.
- Final component agirlik toplami: tum karar satirlarinda 1.0.
- Ana sinyal yon tutarliligi: anomalilerde `ANOMALI_YONU` ile `ANA_SINYAL_Z` ters dusen satir yok.
- Oracle count kontrolu yeni detail panel mantiginda scoring ayina gelen musteri sayisi ve musterilerin ilk goruldugu aylarina gore degisir.

Bu validasyon target bazli accuracy degildir. Target yoktur. Validasyon leakage kontrolu, OOT ayrimi, skor dagilimi, reason/action tutarliligi ve musteri-level plausibility review ile yapilir.

## Oracle Yazimi

Oracle yazimi opsiyoneldir. CSV ciktilari her durumda uretilir. Yeni akista CSV/Oracle kaynaklari ve output sink ayarlari `configs/data_source.yaml` dosyasindan okunur.

Oracle connection bilgileri direkt YAML icindedir. INI dosyalari sadece backward-compatible fallback olarak kalir.

Credential degerleri loglanmaz.

Oracle driver:

```powershell
python -m pip install --user oracledb
```

```bash
python3 -m pip install --user oracledb
```

Mevcut ortamda `oracledb` kuruludur.

### Onerilen Oracle Run

Tablo adlari ve `write_mode` `configs/data_source.yaml` icindeki `oracle_output` sink'i altinda netlestirilir. Hangi kaynagin okunacagi `configs/data_source.yaml` icindeki `active_source` ile secilir. Yazmak icin:

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

`delete_insert` decision tablosunda ayni `DONEM_AY` kayitlarini siler, sonra yeni run'i yazar. Detail tablosunda `MODEL_DONEM_AY` ayni skor kosusunu temsil eder; bu alan silinip yeniden yazilir.

Desteklenen modlar:

- `append`: sadece ekler.
- `delete_insert`: decision icin ayni `DONEM_AY`, detail icin ayni `MODEL_DONEM_AY` kayitlarini silip ekler.
- `truncate_insert`: tum tabloyu temizleyip ekler.
- `replace`: tabloyu drop/create yapar.

Not:

Decision tablosunda skorlanan ay `DONEM_AY` alanidir ve ekstra run kolonu tutulmaz. Detail tablosu musteri-ay serisi oldugu icin `MODEL_DONEM_AY` run anahtari olarak tutulur.

### Hazir Scriptler

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_oracle.ps1
```

Linux/macOS:

```bash
./scripts/run_oracle.sh
```

Windows parametreli ornek:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_oracle.ps1 `
  -ConfigPath configs\anomaly.yaml `
  -DataSourceConfigPath configs\data_source.yaml
```

Linux/macOS parametreli ornek:

```bash
./scripts/run_oracle.sh \
  --config configs/anomaly.yaml \
  --data-source-config configs/data_source.yaml
```

## Oracle Notu

Yeni lokal decision table yalnizca ham input kolonlari, `ANOMALI_FLAG` ve `ANOMALI_NEDENI` icerir. Bu sade semayi Oracle'a yansitmak icin ilk run `replace` modu ile calistirilmelidir. Sonraki aylarda `delete_insert` kullanilabilir.

Decision tablosu input kolonlarini birebir onde tutar:

- `SUBE_KD`
- `MUSTERINO`
- `SEGMENTAD`
- `DONEM_AY`
- `REF_ALTFAALIYET`
- `AKTIF_ABONE`
- `FATURA_TTR`
- `TURNOVER_AMT`

Decision tablosunda ek olarak sadece su kolonlar vardir:

- `ANOMALI_FLAG`
- `ANOMALI_NEDENI`

Peer uyum ve model kanit kolonlari decision tablosunda degil detail tablosundadir:

- `ANA_SINYAL_Z`
- `PEER_UYUM_DURUMU`
- `PEER_FARK_YONU`
- `PEER_FARK_Z`
- `MUSTERI_FARK_Z`
- `MUSTERI_ACIKLANABILIRLIK_SKORU`
- `MUSTERI_ACIKLANABILIRLIK_DURUM`
- `PEER_TEMSIL_SKORU`
- `PEER_TEMSIL_DURUMU`
- `PEER_DAGILIM_SKORU`
- `PEER_DAGILIM_DURUMU`
- `PEER_LOG_ORAN_SKEW`
- `PEER_LOG_ORAN_KURTOSIS`
- `PEER_TAIL_RATE`
- `PEER_SECIM_GEREKCESI`
- `SKORLAMA_STRATEJISI`
- `DAVRANIS_CLUSTER`
- `DAVRANIS_GECMIS_ADET`
- `DAVRANIS_SEVIYE_BUCKET`
- `DAVRANIS_VOLATILITE_BUCKET`
- `DAVRANIS_TREND_BUCKET`

Oracle detail tablosunda peer oran persentil kolonlari vardir:

- `MUSTERI_PEER_ORAN_PCTL`
- `MUSTERI_PEER_ORAN_REF_N`
- `PEER_DAGILIM_SKORU`
- `PEER_DAGILIM_DURUMU`
- `DAVRANIS_CLUSTER`

Kolon isimleri Oracle 30 karakter limitine uyumlu olacak sekilde map edilir. Orijinal CSV kolon adi ile Oracle kolon adi eslesmesi contract JSON icindeki `oracle_column_maps` alanindadir.
