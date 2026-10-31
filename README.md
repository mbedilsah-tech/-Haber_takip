# Haber Takip

Savaşlar, jeopolitik, iç siyaset, savunma sanayii ve Türkiye ekonomisi için kişisel haber ve veri takip uygulaması. Hedef: X'e girmeden, tek uygulamadan haber + veri takibi. Hepsi **ücretsiz** altyapıyla.

## Ekranlar
| Sekme | İçerik |
|---|---|
| **Akış** | Kaynaklardan gelen haberler sınıflanır, aynı olayı anlatanlar tek kartta birleşir. Kartta özet, kaynak sayısı ve "Resmî" / "Doğrulanmadı" etiketi bulunur. |
| **Veriler** | USD/TRY, EUR/TRY, gram altın, BIST 100, Brent ve CDS grafikleri. Ayrıca enflasyon, faiz, işsizlik gibi makro göstergeler ve ekonomik takvim. |
| **Cepheler** | Çatışma bölgelerinde son 7 günün olay sayısı ve değişimi. |
| **Savunma** | Savunma sanayii gündemi, test ve teslimat takvimi, ihracat verisi. |

Prototip: [`prototype/index.html`](prototype/index.html). Bu sürümde haberler gerçek, sayılar örnek veridir.

## Ücretsiz mimari
```
GitHub Actions (her 30 dk)  ──►  collector/ (Python)
   ├─ RSS: AA, TRT Haber, SANA, Reuters, Al Jazeera, Savunma Sanayii ST ...
   ├─ Telegram: herkese açık kanalların web önizlemesi (t.me/s/<kanal>)
   ├─ TCMB EVDS API: kur, faiz, enflasyon (ücretsiz anahtar)
   └─ Çatışma verisi: ACLED (ücretsiz kayıt, kullanım koşullarına uyarak)
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

## Yol haritası
1. [x] Tıklanabilir prototip
2. [ ] Toplayıcı: RSS + Telegram → `data/news.json`, sınıflama ve birleştirme
3. [ ] Veri: TCMB EVDS → `data/markets.json`, `data/macro.json`
4. [ ] Arayüzü gerçek veriye bağlama (GitHub Pages)
5. [ ] Capacitor ile Android APK, Actions'ta otomatik derleme
6. [ ] Cepheler (ACLED) ve savunma takvimi
7. [ ] Bildirim: önemli gelişmede telefona uyarı
