## Smart Travel Planner (Python / FastAPI)

مشروع API بسيط لتخطيط الرحلات بالاعتماد على بيانات خارجية من `insidethekingdom.online`.

- إدخال من المستخدم:
  - عدد الأشخاص في الرحلة
  - الميزانية الكلية
  - مدة الرحلة (أيام)
  - الوجهة (مدينة / منطقة)
- يقوم النظام بـ:
  - حساب الميزانية لكل شخص
  - حساب الميزانية لكل شخص في اليوم
  - توزيع الميزانية اليومية على:
    - الفنادق
    - الأكل
    - الأنشطة
    - المواصلات / الطيران
  - استهلاك بيانات من APIs خارجية:
    - `/activities`
    - `/hotels`
    - `/plains`
  - اقتراح عناصر مناسبة من كل فئة حسب:
    - السعر (ضمن الميزانية اليومية المخصصة للفئة)
    - الوجهة (المنطقة / المدينة)

> ملاحظة: هذا المشروع يعرض بيانات فقط ولا يقوم بأي عملية حجز فعلية، بل يعرض روابط إلى المواقع الخارجية (إن توفّرت).

---

### 1) التحضير

1. **أنشئ بيئة عمل افتراضية (اختياري لكن مستحسن)**:

```bash
python -m venv venv
venv\Scripts\activate  # على Windows
```

2. **ثبّت المتطلبات**:

```bash
pip install -r requirements.txt
```

3. **إعداد متغير البيئة للتوكن**

لا تضع التوكن مباشرة في الكود. استخدم متغير بيئة:

- أنشئ ملف `.env` في نفس مجلد `main.py` وضع فيه:

```bash
INSIDE_KINGDOM_TOKEN=Bearer_Token_هنا
```

> مثال: لو كان التوكن الذي تستخدمه في Postman هو  
> `Authorization: Bearer 20|XXXX...`  
> فالقيمة في `.env` تكون:
>
> ```bash
> INSIDE_KINGDOM_TOKEN=20|XXXX...
> ```

الكود سيضيف كلمة `Bearer` تلقائياً في الـ Header.

---

### 2) تشغيل السيرفر

من مجلد المشروع:

```bash
uvicorn main:app --reload
```

سيفتح السيرفر على:

- `http://127.0.0.1:8000/docs` → واجهة Swagger (توثيق + تجربة الـ API)
- `http://127.0.0.1:8000/redoc` → توثيق بديل

---

### 3) تجربة Endpoint `/plan-trip`

من خلال Swagger أو Postman أرسل طلب `POST` إلى:

- `http://127.0.0.1:8000/plan-trip`

مع Body (JSON) مثلاً:

```json
{
  "total_budget": 30000,
  "people_count": 5,
  "days": 7,
  "destination": "Riyadh",
  "percentages": {
    "hotels": 0.4,
    "food": 0.25,
    "activities": 0.2,
    "transport": 0.15
  }
}
```

الاستجابة سترجع:

- `per_person_total` : الميزانية الكلية لكل شخص
- `per_person_per_day` : الميزانية اليومية لكل شخص
- `budgets_per_day` : توزيع الميزانية اليومية على الفئات
- `hotels`, `activities`, `transport`, `food` : اقتراحات مع الأسعار والموقع والرابط (إن توفر)

---

### 4) تعديل أسماء الحقول حسب شكل الـ API الخارجي

بما أن شكل الـ JSON من:

- `https://insidethekingdom.online/api/activities`
- `https://insidethekingdom.online/api/hotels`
- `https://insidethekingdom.online/api/plains`

قد يختلف من مشروع لآخر، تم وضع منطق عام في الدالة:

- `filter_and_map_items` داخل `main.py`

هذه الدالة تفترض أسماء محتملة للحقول مثل:

- السعر: `price`, `price_per_night`, `min_price`, `amount`, `cost`
- الموقع: `city`, `region`, `location`, `destination`, `area`
- الاسم: `name`, `title`, `hotel_name`, `activity_name`
- الرابط: `url`, `link`, `website`, `booking_url`

**ما عليك فعله:**

1. من Postman أرسل طلب على كل API خارجي باستخدام التوكن:
   - `GET https://insidethekingdom.online/api/activities`
   - `GET https://insidethekingdom.online/api/hotels`
   - `GET https://insidethekingdom.online/api/plains`
2. خذ مثال JSON من كل واحد، وانظر لأسماء الحقول الحقيقية:
   - أين السعر؟
   - أين المدينة/المنطقة؟
   - أين رابط الحجز / التفاصيل؟
3. عدّل القوائم `price_field_candidates` ، `location_field_candidates`، `name_field_candidates`، `url_field_candidates` في `main.py` لتطابق أسماء الحقول الحقيقية.

يمكنك أيضاً استعمال Endpoint للمساعدة على المعاينة:

- `GET http://127.0.0.1:8000/external-preview`

سيعرض لك عينة من بيانات كل API حتى تراها في المتصفح مباشرة.

---

### 5) ملاحظات

- هذا المشروع للعرض والتخطيط فقط، لا يتعامل مع أي عملية دفع أو حجز.
- لا تشارك التوكن مع أي طرف آخر، وحافظ عليه في `.env` أو في إعدادات السيرفر (متغيرات بيئة).
- يمكن تعديل نسب الميزانية الافتراضية في `BudgetPercentages` في `main.py`.


