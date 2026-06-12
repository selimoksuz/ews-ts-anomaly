# Implementation Documentation

## Klasor Yapisi

- `src/`: model, config, IO ve rapor kodlari
- `scripts/`: Windows PowerShell ve Linux/macOS shell runner'lari
- `configs/anomaly.yaml`: aktif model ve peer secim parametreleri
- `configs/anomaly_fatura.example.yaml`: fatura run template'i
- `configs/anomaly_pos.example.yaml`: POS ciro run template'i
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

Aktif pipeline her zaman `configs/anomaly.yaml` dosyasini okur. Farkli prosesler icin template dosyalari example olarak tutulur:

```bash
cp configs/anomaly_fatura.example.yaml configs/anomaly.yaml
cp configs/anomaly_pos.example.yaml configs/anomaly.yaml
```

PowerShell:

```powershell
Copy-Item configs\anomaly_fatura.example.yaml configs\anomaly.yaml -Force
Copy-Item configs\anomaly_pos.example.yaml configs\anomaly.yaml -Force
```

Bu dosyalar secret tasimaz; Oracle/CSV kaynagi `data_source_config_path` ile isaret edilen ve git'e alinmayan datasource YAML dosyasindan gelir.

Input secimi `configs/data_source.yaml` icindeki `active_source` ile yapilir:

```yaml
active_source: local_csv
```

veya:

```yaml
active_source: oracle_input
```

Output sink secimi `active_sink` ile yapilir. Oracle yazimi icin sink tanimi yeterli degildir; run komutunda ayrica Oracle output flag'i verilmelidir.

Secilen source icinde `output_columns: all` ise kaynak tablodaki kolonlar decision/detail output'a tasinir. Bir kolonu istemiyorsan ilgili source altinda `exclude_output_columns` kullan.

Ana degiskenler `configs/anomaly.yaml` icindeki `variables` alanindan okunur:

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

`feature_variables` listesindeki ilk kolon skorlanan ana metriktir. Adaptif peer adaylarini sadece `segment_variables` listesi besler.

Segment degiskenleri tek tek biliniyorsa liste verilir; production icin onerilen mod budur. Kesif amacli `segment_variables: auto` yazilabilir; bu modda engine `id_variables`, `time_variables`, ana metrik, referans feature ve `exclude_variables` alanlarini cikarir, kalan dusuk/orta cardinality kolonlari gecici segment listesi olarak infer eder. Production'a tasirken bu liste YAML'a acik segment listesi olarak yazilmalidir.

```yaml
variables:
  id_variables:
    - MUSTERI_ID
  time_variables:
    - AY
  segment_variables: auto
  main_feature: AYLIK_CIRO_FIXED_20260531
  exclude_variables:
    - PERIOD_INDEX_VALUE
    - TURNOVER_FINANCIAL_TERM
```

Ana metrik herhangi bir operasyonel sayisal deger olabilir. Bugun fatura tutari, baska bir proseste POS cirosu veya farkli bir tekil sayisal metrik olabilir; kod metrik adina bagli calismaz.

Feature kolonlari sadece listede yer aldigi icin anomali skoruna girmez. Aktif turevler `model.derived_features` altinda tanimlanir; bu turevler peer segmentasyonuna otomatik girmez:

```yaml
model:
  derived_features:
    feature_ratio:
      enabled: true
      numerator: main_feature
      denominator: TURNOVER_AMT
      use_as_anomaly_signal: true
    behavior_peer:
      enabled: false
    bucket_features:
      - source: AKTIF_ABONE
        internal_role: exposure_feature
```

Farkli proseslerde `denominator` ve `bucket_features.source` alanlari degistirilir; kod icinde proses-spesifik kolon adi aranmaz. Peer icin kullanilacak kolonlar ise mutlaka `variables.segment_variables` altinda bulunmalidir. Config'te uretilmis q-bucket kolonlari yazilmaz.

Output kolonlari ayri bir schema dosyasindan okunmaz. Decision tablo kontrati sabittir: ham input kolonlari + `ANOMALI_FLAG`, `ANOMALI_SKORU`, `ANOMALI_NEDENI`. Detail tabloda ham input kolonlari onde gelir; sonrasinda modelin run sirasinda urettigi diagnostic kolonlar dinamik eklenir. Oracle kolon ve tablo adlari da ayrica maplenmez: 30 karakteri asmayan adlar aynen kalir, uzun adlar deterministic hash suffix ile otomatik kisaltilir. Tablo adini birebir Oracle'da gormek istiyorsan `decision_table` ve `detail_table` adlarini 30 karakter altinda tut.

Skorlanacak ay `configs/anomaly.yaml` icindeki `model.scoring_month` ile secilir:

```yaml
model:
  scoring_month: last
```

`last` kaynak datadaki en buyuk donemi skorlar. Sabit ay skorlamak icin `YYYYMM`, `YYYYMMDD` veya tarih benzeri format yazabilirsin:

```yaml
model:
  scoring_month: 202603
```

Asagidaki de ayni ay olarak yorumlanir:

```yaml
model:
  scoring_month: 20260301
```

```yaml
model:
  scoring_month: 2026-03-01
```

Son 3 ay musteri rejimi sinyali `configs/anomaly.yaml` icinde yonetilir:

```yaml
model:
  score_aggregation:
    recent_regime:
      enabled: true
      min_recent_months: 3
      max_recent_range_log: 0.35
```

Bu sinyal sadece son 3 calendar ay tam ve stabilse skora girer; aksi halde karar surecine etki etmez.

Sezon korumasi da ayni bloktan yonetilir:

```yaml
model:
  score_aggregation:
    recent_regime:
      seasonal_guard_enabled: true
      seasonal_guard_max_abs_season_z: 1.50
      seasonal_guard_min_abs_recent_z: 2.50
```

Bu ayar acikken son-3-ay rejim sapmasi sert olsa bile ayni ay sezon z-score'u normalse production karar evidence'ina girmez. Detail'de son-3-ay z-score'u gorunmeye devam eder; normal reason metni sapmanin sezonla aciklandigini yazar.

## Kalite ve Challenger Ayarlari

Peer objective agirliklari `configs/anomaly.yaml` icinde `peer_selection.objective_weights` altindadir. Varsayilan:

- `representability`: 0.20
- `distribution`: 0.30
- `calibration`: 0.20
- `stability`: 0.10
- `specificity`: 0.10
- `support`: 0.10

`calibration`, scoring ayini kullanmadan onceki aylarda peer'in bir sonraki ay referansi olarak ne kadar iyi calistigini olcer. Detail tabloda `PEER_KALIBRASYON_SKORU`, kalibrasyon ay adedi, median absolute residual, interval coverage ve false alarm orani bulunur.

`distribution`, peer'in normal dagilip dagilmadigini degil robust sekilde karsilastirilabilir olup olmadigini olcer. Bilesenleri `peer_selection.distribution_quality` altindan parametriktir: scoring ay log IQR/MAD, gecmis aylik log IQR/MAD medyani, robust tail-rate, log skew/kurtosis ve dusuk agirlikli raw ortalama/medyan ile std/medyan diagnostikleri. Temsil skoru destek/spesifiklik tabanini `0.50 + 0.50 * PEER_DAGILIM_SKORU / 100` carpanindan gecirdigi icin genis ama kalabalik peer otomatik strong temsil sayilmaz.

Objective skor kalite cap'i `peer_selection.objective_quality_caps` altindadir. Varsayilan olarak `PEER_DAGILIM_SKORU < 40` veya `PEER_TEMSIL_SKORU < 40` ise `PEER_OBJECTIVE_SKORU` 55'i asamaz; 40-60 bandinda ise 70'i asamaz. Cap kademelidir: esikten her puan uzaklasma `*_penalty_per_point` katsayisiyla tavani biraz daha dusurur. Boylece zayif peerler ayni puana sikismaz, yine kendi icinde daha iyi dagilim/temsil sunan segment secilir. Karar katmaninda `model.score_aggregation.peer_reliability.min_peer_objective_score = 60` ve `min_peer_distribution_score = 40` esikleri vardir. Bu esikleri gecemeyen peer `PEER_WEAK` olur; final anomaly score/flag uretiminde karar driver'i olamaz, detail tabloda sadece diagnostic olarak kalir.

`behavior_level_bucket`, `behavior_volatility_bucket`, `behavior_trend_bucket` ve `behavior_cluster` sadece scoring ayindan onceki musteri gecmisiyle uretilir. Bu alanlar peer segmentasyonuna otomatik girmez; detail/model diagnostic olarak kalir. `peer_selection.candidate_strategy: objective_lattice` sadece `variables.segment_variables` kolonlari arasindan adaylari otomatik kurar. Branch/sube gibi cok parcalayan kolonlar segment listesine yaziliysa destek gecmezse objective tarafindan elenir veya daha genis peer'e dusulur.

Feature ratio skora girmeden once `model.derived_features.feature_ratio.quality_gate` ile kontrol edilir. Global gate gecmezse veya secilen peer icinde `min_peer_ratio_rows` / `min_peer_ratio_mad` gecmezse oran sadece diagnostic kalir. Detail tabloda gate sonucu ve nedeni `FEATURE_ORAN_*_GATE_*` kolonlariyla izlenir.

Challenger modeller `model.score_aggregation.challenger_models` altindan yonetilir. PCA, Isolation Forest ve LOF production kararini degistirmez; detail tabloda aggregate `model_challenger_score`, model bazli `pca/if/lof_challenger_score`, `model_challenger_warning` ve `pca/if/lof_challenger_anomaly_flag` alanlari uretilir. Bu alanlar scoring ay diagnostic'i oldugu icin yalniz scoring ay satirinda doludur; gecmis seri satirlarinda bos kalir.

Challenger feature setine rule-derived veya karar-parametrik kolonlar verilmez. Model sadece `model.score_aggregation.challenger_models.feature_columns` altinda yazan fonksiyonel residual transformasyonlariyla calisir: musteri gecmis/trend/sezon/son-3-ay z skorlari, peer gecmis/guncel/trend z skorlari ve referans feature oran z skoru. `PRIMARY_SINYAL_P_DEGERI`, `ANA_SINYAL_SKORU`, `GUVEN_SKORU`, `MUSTERI_ACIKLANABILIRLIK_SKORU`, `EVIDENCE_CONFLICT_FLAG`, `VERI_YETERLILIK_DURUMU`, data-gap skoru, peer kalite skorlari ve final beklenen/gercek orani challenger modele sokulmaz; bunlar rule/diagnostic katmaninda kalir.

Challenger input hygiene `min_feature_valid_rate` ve `min_feature_unique_values` ile kontrol edilir. Tamamen bos, gate nedeniyle uretilmeyen veya sabit kalan residual feature modele girmez.

Aggregate `MODEL_CHALLENGER_SKORU`, `challenger_models.aggregate_methods` listesindeki modellerden hesaplanir. Varsayilan `isolation_forest + pca`dir; LOF skoru ve flag'i uretilir ama mevcut validasyonda zayif hizalandigi icin aggregate'e dahil edilmez.

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
python3 src/anomaly_pipeline.py --config configs/anomaly.yaml --skip-peer-quality-report
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
python3 src/anomaly_pipeline.py \
  --config configs/anomaly.yaml \
  --enable-oracle-output \
  --skip-peer-quality-report
```

## Outputlar

CSV/local source veya local sink run'larinda decision/detail CSV ve contract JSON uretilir.

Oracle source + Oracle sink run'inda decision/detail zaten Oracle'a yazildigi icin lokal decision/detail CSV, contract JSON ve staging source snapshot uretilmez. Peer quality raporu aciksa sadece `outputs/analysis/peer_quality_report` altindaki analiz CSV/XLSX/HTML/MD dosyalari uretilir.

Decision table:

- Tek satir = scoring ayindaki musteri
- Ham input kolonlari
- `ANOMALI_FLAG`
- `ANOMALI_SKORU`
- `ANOMALI_NEDENI`

Detail table:

- Tek satir = scoring musterileri icin musteri-ay
- Musteri seri gorunumu
- Peer aylik medyan/ortalama metrikleri
- Trend, sezon, p-value, z-score, evidence driver ve reason detaylari
- Challenger diagnostic, peer kalite, peer kalibrasyon ve feature-ratio gate alanlari
- `ANOMALI_SKORU` ham p-value skoru degil, final etiketle uyumlu ay ici operasyonel risk skorudur. Ham kanit gucu `ANA_SINYAL_SKORU`, `PRIMARY_SINYAL_SKORU`, `MUSTERI_SINYAL_SKORU` ve `PEER_SINYAL_SKORU` alanlarinda izlenir.
- Decision tablosundaki `ANOMALI_NEDENI` human-readable karar metnidir; guven yuzdesi bu metinde yazmaz. Guven detayi sadece detail tablosundaki `GUVEN_SKORU` alaninda izlenir.
- `ANOMALI_FLAG` tum detail satirlarinda 0/1 olarak doludur; scoring ayinda anomaly ise 1, diger satirlar 0 olur.
- Fiziksel kolon sirasi sabittir: ham input, seri/data quality, peer aylik metrikler, scoring beklenen/gercek metrikler, musteri sinyalleri, peer sinyalleri, challenger, peer kalite, behavior, onceki skor diagnostigi, evidence driver ve final karar alanlari.
- Oracle yaziminda tablo kolon seti veya kolon sirasi degisirse `create_table: true` iken tablo yeniden olusturulur; boylece Oracle fiziksel kolon sirasi da dokumandaki sirayla uyumlu kalir.

Detail tabloda yorumlama icin onemli kolon ornekleri:

- `ANA_METRIK_EKSIK_MI`
- `PEER_AYLIK_ANA_METRIK_MEDYAN`
- `PEER_AYLIK_ANA_METRIK_ORTALAMA`
- `MUSTERI_PEER_ANA_METRIK_ORANI`
- `ORAN_PAY_KOLON`
- `ORAN_PAYDA_KOLON`
- `MUSTERI_ANA_METRIK_PAYDA_ORANI`
- `PEER_AYLIK_ORAN_PAYDA_MEDYAN`
- `PEER_AYLIK_ANA_METRIK_PAYDA_ORAN_MEDYAN`
- Customer/peer trend, sezon, z-score, p-value, reason ve data-quality alanlari

Modelin ozet karar akisi:

- Scoring ayi fit icinde kullanilmaz.
- Once customer-first kanit aranir: kendi gecmis, trend, sezon ve son 3 ay rejimi.
- Customer kaniti yetersizse adaptif peer secimi devreye girer.
- Peer secimi segment degiskenleri ve config ile acikca tanimlanan turev peer degiskenleri uzerinden objective score ile yapilir.
- Ana metrik eksik aylar doldurulmaz; gap ve coverage sinyali olarak detail tabloda tasinir.
- Final karar evidence-first p-value konsolidasyonu ile verilir; agirlikli ortalama target modeli degildir.

## Peer Quality Raporu

Peer quality raporu instance bazinda peer temsil ve dagilim kalitesini analiz etmek icindir; karar tablosu degildir. Instance raporunda ana metrik dagilimi icin su kolonlar bulunur:

- `peer_guncel_ana_metrik_ortalama`
- `peer_guncel_ana_metrik_medyan`
- `peer_guncel_ana_metrik_ortalama_medyan_oran`
- `peer_guncel_ana_metrik_std`
- `peer_guncel_ana_metrik_std_medyan_oran`
- `peer_guncel_ana_metrik_min`
- `peer_guncel_ana_metrik_max`
- `peer_gecmis_ana_metrik_ortalama`
- `peer_gecmis_ana_metrik_medyan`
- `peer_gecmis_ana_metrik_ortalama_medyan_oran`
- `peer_gecmis_ana_metrik_std`
- `peer_gecmis_ana_metrik_std_medyan_oran`
- `peer_gecmis_ana_metrik_min`
- `peer_gecmis_ana_metrik_max`

## Backtest ve Output Integrity Monitor

Her ay yeni data eklendiginde production scoring sonrasi validation monitor calistirilir:

Onerilen operasyonel sira:

1. Production scoring run'i calistir.
2. Peer quality raporunu kontrol et.
3. Validation monitor'u calistir.
4. `validation_output_integrity.csv` icinde `FAIL` var mi kontrol et.
5. `validation_stability_flags.csv` icinde ani rate, scoreability veya peer calibration kaymasi var mi kontrol et.

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_validation_report.ps1
```

Linux/macOS:

```bash
./scripts/run_validation_report.sh
```

Dogudan Python:

```bash
python3 src/anomaly_validation.py --config configs/anomaly.yaml
```

Parametreler `configs/anomaly.yaml` icindeki `reports.validation` altindan gelir:

- `backtest_months`: son kac scoring ayi rolling OOT test edilecek.
- `stress_test_sample_size`: gercek scoring datasindan spike/drop stres testi icin kac musteri secilecek.
- `stress_test_spike_factor`: stres testinde skor ayindaki ana metrik kac kat yukseltilerek test edilecek.
- `stress_test_drop_factor`: stres testinde skor ayindaki ana metrik hangi carpanla dusurulerek test edilecek.
- `skip_stress_test`: true ise perturbation stres testi atlanir; gercek data rolling backtest her durumda calisir.

Uretilen dosyalar `outputs/analysis/validation_report` altindadir:

- `validation_monthly_summary.csv`
- `validation_stability_flags.csv`
- `validation_scoreability_breakdown.csv`
- `validation_label_breakdown.csv`
- `validation_top_examples.csv`
- `validation_output_integrity.csv`
- `validation_stress_test_sensitivity.json`
- `validation_report_<YYYYMM>.md`

`validation_output_integrity.csv` decision/detail satir sayisi, `ANOMALI_FLAG` missing/binary kontrolu, decision reason boslugu, detail extreme missing kolonlari ve normal satirlarin flagged satir skor tabanini asmamasi kontrolunu izler. Bu dosyada `FAIL` varsa ilgili run production'a alinmadan incelenmelidir; `WARN` varsa operasyonel yorum etkisi degerlendirilmelidir.

## Oracle Write Mode

- `append`: tabloya ekler
- `delete_insert`: decision icin ayni `DONEM_AY`, detail icin ayni `MODEL_DONEM_AY` silinir ve yeniden yazilir
- `truncate_insert`: tablo bosaltilir ve yeniden yazilir
- `replace`: tablo drop/create edilir

Varsayilan `delete_insert` operasyonel run icin uygundur.

Decision tabloda `MODEL_DONEM_AY` tutulmadigi icin `delete_insert` ayni run'da insert edilecek ham donem kolonunun gercek degerlerini siler. Bu nedenle `YYYYMM`, `YYYY-MM`, `1.04.2026` gibi farkli ham donem formatlarinda duplicate insert olusmaz. Detail tabloda silme `MODEL_DONEM_AY` uzerinden yapilir.

## Performans ve Loglama

- Output build artik alt adim bazinda loglanir: `augment_scores_for_outputs`, `decision_table_build`, `detail_table_build`, `oracle_dataframe_prepare`, `oracle_insert`.
- Oracle detail insert sirasinda her 100 bin satirda progress log'u basilir.
- Peer quality postprocess'te peer key/review reason ve peer instance ana metrik std hesaplari vektorizelestirildi. Local benchmark sonucu:
  - `add_peer_instance_keys`: 200 bin satirda 5.98 sn -> 0.25 sn.
  - `weak_peer_review`: 100 bin satirda 1.94 sn -> 0.13 sn.
  - `aggregate_main_metric_stats`: 300 bin satir, 6 bin grup icin 0.67 sn -> 0.07 sn.
- Level bazli thread paralelizasyonu test edildi ama ayni benchmarkta hizlanma saglamadi (`34.27 sn` sequential, `36.54 sn` 4 thread); bu nedenle production'a alinmadi.

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
