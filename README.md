# Haber Takip

Savaşlar, jeopolitik, iç siyaset, savunma sanayii ve Türkiye ekonomisi için kişisel haber ve veri takip uygulaması. Hedef: X'e girmeden, tek uygulamadan haber + veri takibi. Hepsi **ücretsiz** altyapıyla.

## Ekranlar
| Sekme | İçerik |
|---|---|
| **Akış** | Kaynaklardan gelen haberler sınıflanır, aynı olayı anlatanlar tek kartta birleşir. Kartta özet, kaynak sayısı ve "Resmî" / "Doğrulanmadı" etiketi bulunur. |
| **Veriler** | USD/TRY, EUR/TRY, gram altın, ONS/USD, gümüş, BIST 100, Brent ve CDS grafikleri. Aylık ihracat (geçen yılla karşılaştırmalı), sektör ve ülke kırılımı. Ayrıca makro göstergeler ve ekonomik takvim. |
| **Cepheler** | Çatışma bölgelerinde son 7 günün olay sayısı ve değişimi. Cepheye dokununca günlük grafik, olay listesi (saat, yer, tür), görsel önizleme ve kaynak linki açılır. |
| **Savunma** | Savunma sanayii gündemi, test ve teslimat takvimi, ihracat verisi. |
| **Erdoğan** | Cumhurbaşkanı'nın günlük programı, günün özeti ve konuşmalarının konu konu özeti (kaynak linkleriyle). |

Prototip: [`prototype/index.html`](prototype/index.html). Bu sürümde haberler gerçek, sayılar örnek veridir.

## Ücretsiz mimari
```
GitHub Actions (her 30 dk)  ──►  collector/ (Python)
   ├─ RSS: AA, TRT Haber, SANA, Reuters, Al Jazeera, Savunma Sanayii ST ...
   ├─ Telegram: herkese açık kanalların web önizlemesi (t.me/s/<kanal>)
   ├─ TCMB EVDS API: kur, faiz, enflasyon (ücretsiz anahtar)
   ├─ Çatışma verisi: ACLED (ücretsiz kayıt, kullanım koşullarına uyarak)
   ├─ İhracat: Ticaret Bakanlığı / TİM aylık açıklamaları
   └─ Cumhurbaşkanlığı: tccb.gov.tr program ve konuşmalar, İletişim Başkanlığı
        │
        ▼
   Sınıflama + tekrar birleştirme (kural tabanlı, ücretsiz)
   [isteğe bağlı] ücretsiz katmanlı bir yapay zekâ API'si ile kısa özet
        │
        ▼
   data/*.json  ──►  GitHub Pages  ──►  Android uygulama (Capacitor ile APK)
```
- APK da GitHub Actions'ta derlenir ve **Releases** sayfasından indirilip telefona kurulur. Play Store gerekmez.
- API anahtarları kodda tutulmaz, **GitHub Secrets** içinde saklanır.

### Önemli karar: repo herkese açık mı olsun?
- GitHub Pages, özel (private) repolarda ücretli plan istiyor.
- Actions dakikaları özel repolarda ayda 2.000 ile sınırlı. 30 dakikada bir çalışmak bu sınırı aşabilir.
- **Öneri:** Kod ve haber verisi herkese açık olsun, gizli bilgiler Secrets'ta kalsın. Tamamen ücretsiz kalmanın en kolay yolu bu.

## Klasörler
| Klasör | Ne var |
|---|---|
| `app/` | Uygulamanın kendisi (tek HTML dosyası). Verileri `data/` klasöründen okur. |
| `collector/` | Toplayıcı: `collect.py` ve kaynak listesi `sources.json`. Kaynak eklemek için sadece bu JSON'u düzenlemek yeterli. |
| `data/manual.json` | Elle güncellenen veriler: makro göstergeler, ihracat, takvimler. |
| `.github/workflows/collect.yml` | Her 30 dakikada toplayıp GitHub Pages'e yayınlayan iş akışı. |
| `prototype/` | İlk tıklanabilir prototip (örnek veri). |

## Yol haritası
1. [x] Tıklanabilir prototip
2. [x] Toplayıcı: RSS + Google Haberler + Telegram → `news.json`, sınıflama ve birleştirme
3. [x] Piyasa verisi (kur, altın, ONS, gümüş, BIST, Brent) → `markets.json`
3b. [ ] Makro ve ihracat verisini otomatik çekme (TCMB EVDS, TİM)
4. [x] Arayüzü gerçek veriye bağlama (GitHub Pages)
5. [ ] Capacitor ile Android APK, Actions'ta otomatik derleme
6. [ ] Cepheler (ACLED) ve savunma takvimi
7. [ ] Bildirim: önemli gelişmede telefona uyarı
