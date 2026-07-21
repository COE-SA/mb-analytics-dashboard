# 🧁 Munch Bakery — لوحة التحكم المالية والتشغيلية

داشبورد تحليلي شامل يتصل مباشرة بـ Odoo ويُحدَّث تلقائياً كل يوم.

## 📊 المحتوى

| الصفحة | المحتوى |
|--------|---------|
| **نظرة عامة** | KPIs اليوم/أمس/الأسبوع/الشهر/السنة، مبيعات يومية، شهرية، ساعة بساعة |
| **الفروع** | مقارنة الفروع الستة، حصة كل فرع، أداء شهري تفصيلي |
| **التطبيقات** | هنقر ستيشن، كيتا، تيكر، نينجا، جاهز، ذا شيفز، مارسول، وغيرها |
| **المنتجات** | أفضل 30 منتج بالإيرادات والكمية وهامش الربح |
| **الربح والخسارة** | P&L شهري، هامش الربح، المشتريات |
| **طرق الدفع** | Online, Mada, Visa, Master, Amex, Cash |
| **التاريخ الكامل** | كامل البيانات منذ أكتوبر 2024 |

## ⚙️ الإعداد

### 1. أضف Secrets في GitHub
```
Settings → Secrets and variables → Actions → New repository secret
```

| Secret | القيمة |
|--------|--------|
| `ODOO_URL` | `https://munchbakerydev-compass.odoo.com` |
| `ODOO_DB` | `munchbakerydev-compass-live-15510994` |
| `ODOO_USER` | `HASSAN` |
| `ODOO_API_KEY` | مفتاح API الخاص بك |

### 2. فعّل GitHub Pages
```
Settings → Pages → Source: Deploy from a branch → Branch: gh-pages
```

### 3. شغّل أول تحديث يدوي
```
Actions → Daily Data Update → Run workflow
```

## 🔄 التحديث التلقائي
يعمل كل يوم الساعة **6:00 صباحاً بتوقيت السعودية** تلقائياً.
