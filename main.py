import os
import asyncio
from typing import Optional, List, Dict, Any, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import httpx
from dotenv import load_dotenv


load_dotenv()

EXTERNAL_API_BASE_URL = "https://insidethekingdom.online/api"

# يمكن قراءة التوكن من متغير بيئة، وإذا لم يوجد نستخدم التوكن الذي أعطيتني إياه
# ملاحظة: أمنيّاً الأفضل دائماً استخدام متغير بيئة، لكن هنا نسهّل التشغيل مباشرة على جهازك.
DEFAULT_TOKEN = "2|GzzXjnvexTyfIQEuaKM9zLTysy4lHf4uG2x6k4Fk853120e5"
API_TOKEN = os.getenv("INSIDE_KINGDOM_TOKEN", DEFAULT_TOKEN)

COMMON_HEADERS = {
    "Accept": "application/json",
    # إرسال التوكن بنفس شكل Postman: Authorization: Bearer <token>
    "Authorization": f"Bearer {API_TOKEN}",
}


app = FastAPI(
    title="Smart Travel Planner",
    description=(
        "خدمة لتخطيط الرحلات اعتماداً على بيانات خارجية من insidethekingdom.online\n\n"
        "- إدخال: عدد الأشخاص، الميزانية الكلية، مدة الرحلة، الوجهة\n"
        "- إخراج: اقتراح فنادق، أنشطة، ووسائل نقل مناسبة حسب الميزانية لكل فئة\n"
        "هذه الخدمة لا تقوم بالحجز، فقط تعرض بيانات وروابط للمنصات الخارجية."
    ),
    version="1.0.0",
)


# ======================== نماذج البيانات (Schemas) ======================== #


class BudgetPercentages(BaseModel):
    """نِسَب توزيع الميزانية اليومية على الفئات المختلفة."""

    hotels: float = Field(
        0.4, ge=0, le=1, description="نسبة ميزانية الفنادق من الميزانية اليومية"
    )
    food: float = Field(
        0.25, ge=0, le=1, description="نسبة ميزانية الطعام من الميزانية اليومية"
    )
    activities: float = Field(
        0.2, ge=0, le=1, description="نسبة ميزانية الأنشطة من الميزانية اليومية"
    )
    transport: float = Field(
        0.15, ge=0, le=1, description="نسبة ميزانية المواصلات / الطيران من الميزانية اليومية"
    )


class TripRequest(BaseModel):
    """طلب حساب خطة رحلة."""

    total_budget: float = Field(..., gt=0, description="الميزانية الكلية بالريال السعودي")
    people_count: int = Field(..., gt=0, description="عدد الأشخاص في الرحلة")
    days: int = Field(..., gt=0, description="مدة الرحلة بالأيام")
    destination: str = Field(
        ..., description="اسم المدينة أو المنطقة (مثال: Riyadh). يمكن تجاهله إذا أرسلت city_id."
    )
    city_id: Optional[int] = Field(
        None,
        description=(
            "معرّف المدينة من API المدن (/cities). "
            "إذا تم إرساله سيتم استخدام اسم المدينة المرتبط به لتصفية البيانات."
        ),
    )
    percentages: Optional[BudgetPercentages] = Field(
        None,
        description="نسب مخصصة لتوزيع الميزانية. إن لم تُرسل تُستخدم القيم الافتراضية.",
    )
    # حقول جديدة لإرسال البيانات إلى /api/budgets
    name: Optional[str] = Field(None, description="اسم الرحلة (لإرسالها إلى /api/budgets)")
    address: Optional[str] = Field(None, description="عنوان الرحلة (لإرسالها إلى /api/budgets)")
    from_city_id: Optional[int] = Field(None, description="معرّف المدينة المنطلقة")
    to_city_id: Optional[int] = Field(None, description="معرّف المدينة الوجهة (إذا لم يُرسل سيستخدم city_id)")
    user_id: Optional[int] = Field(None, description="معرّف المستخدم (لإرسالها إلى /api/budgets)")


class ExternalItem(BaseModel):
    """تمثيل عنصر (فندق/نشاط/طيران...) قادم من الـ API الخارجي بعد الترتيب والتنقية."""

    id: Optional[int] = None
    name: str
    price: float  # عادة أقل سعر متاح
    min_price: Optional[float] = Field(
        None, description="أقل سعر (من price_range.min أو ما يشابه) إن وُجد"
    )
    max_price: Optional[float] = Field(
        None, description="أعلى سعر (من price_range.max أو ما يشابه) إن وُجد"
    )
    location: Optional[str] = None
    url: Optional[str] = None
    raw: Optional[Dict[str, Any]] = Field(
        None,
        description="البيانات الخام كاملة من الـ API الخارجي، لتسهيل العرض في الواجهة إن احتجت.",
    )


class CategorySuggestion(BaseModel):
    """اقتراحات لفئة معينة (فنادق، أنشطة، ...)."""

    budget_per_day: float
    suggested_items: List[ExternalItem]
    within_budget: bool = Field(
        True,
        description="هل كل الاقتراحات ضمن الميزانية اليومية لهذه الفئة؟",
    )
    message: Optional[str] = Field(
        None,
        description="رسالة توضيحية (مثلاً: قم بزيادة الميزانية لهذه الفئة).",
    )


class TripPlanResponse(BaseModel):
    """استجابة خطة الرحلة بالكامل."""

    per_person_total: float
    per_person_per_day: float
    budgets_per_day: Dict[str, float]
    hotels: CategorySuggestion
    food: CategorySuggestion
    activities: CategorySuggestion
    transport: CategorySuggestion


# ======================== دوال مساعدة للميزانية ======================== #


def calculate_budgets(req: TripRequest) -> Tuple[float, float, Dict[str, float]]:
    """حساب الميزانيات الأساسية:
    - الميزانية لكل شخص
    - الميزانية لكل شخص في اليوم
    - توزيع الميزانية اليومية على الفئات
    """
    per_person_total = req.total_budget / req.people_count
    per_person_per_day = per_person_total / req.days

    percentages = req.percentages or BudgetPercentages()
    total_ratio = (
        percentages.hotels
        + percentages.food
        + percentages.activities
        + percentages.transport
    )

    if abs(total_ratio - 1.0) > 1e-6:
        raise HTTPException(
            status_code=400,
            detail="مجموع النسب (hotels + food + activities + transport) يجب أن يساوي 1.0",
        )

    budgets_per_day = {
        "hotels": per_person_per_day * percentages.hotels,
        "food": per_person_per_day * percentages.food,
        "activities": per_person_per_day * percentages.activities,
        "transport": per_person_per_day * percentages.transport,
    }

    return per_person_total, per_person_per_day, budgets_per_day


# ======================== استهلاك APIs خارجية ======================== #


async def fetch_list_from_external(
    endpoint: str, params: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """استدعاء GET بسيط من الـ API الخارجي وإرجاع قائمة عناصر.

    ملاحظة: شكل الاستجابة الحقيقي قد يكون:
    - قائمة مباشرة []
    - أو كائن فيه مفتاح data: { "data": [...] }
    لذلك نحاول دعم الحالتين.
    """
    url = f"{EXTERNAL_API_BASE_URL}/{endpoint.strip('/')}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=COMMON_HEADERS, params=params)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"External API error at {endpoint} (status={resp.status_code})",
        )

    data = resp.json()

    if isinstance(data, dict):
        # إن كان شكل الاستجابة: { "data": [...] }
        if "data" in data and isinstance(data["data"], list):
            return data["data"]
        # أو مباشرة قائمة داخل مفتاح آخر (تحتاج تعديل بعد معرفة الشكل الحقيقي)
        if "items" in data and isinstance(data["items"], list):
            return data["items"]

    if isinstance(data, list):
        return data

    # إن وصلنا هنا فالشكل غير متوقع
    raise HTTPException(
        status_code=502,
        detail=f"Unexpected response format from external API at {endpoint}",
    )


async def fetch_all_external() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """جلب بيانات الأنشطة، الفنادق، والطيران (plains) بالتوازي."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        # تجهيز الـ coroutines
        activities_coro = client.get(
            f"{EXTERNAL_API_BASE_URL}/activities", headers=COMMON_HEADERS
        )
        hotels_coro = client.get(
            f"{EXTERNAL_API_BASE_URL}/hotels", headers=COMMON_HEADERS
        )
        plains_coro = client.get(
            f"{EXTERNAL_API_BASE_URL}/plains", headers=COMMON_HEADERS
        )

        # تشغيل الطلبات بالتوازي باستخدام asyncio.gather
        res_activities, res_hotels, res_plains = await asyncio.gather(
            activities_coro, hotels_coro, plains_coro
        )

    for response, name in [
        (res_activities, "activities"),
        (res_hotels, "hotels"),
        (res_plains, "plains"),
    ]:
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"External API error at {name} (status={response.status_code})",
            )

    def normalize(resp: httpx.Response) -> List[Dict[str, Any]]:
        data = resp.json()
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]
        if isinstance(data, list):
            return data
        # fallback
        return []

    return (
        normalize(res_activities),
        normalize(res_hotels),
        normalize(res_plains),
    )


async def fetch_cities() -> List[Dict[str, Any]]:
    """جلب قائمة المدن من /cities."""
    return await fetch_list_from_external("cities")


async def find_city_id_by_name(city_name: str) -> Optional[int]:
    """البحث عن معرف المدينة من اسمها."""
    cities = await fetch_cities()
    city_name_lower = city_name.lower().strip()
    for city in cities:
        if city.get("name") and city.get("name").lower().strip() == city_name_lower:
            city_id = city.get("id")
            if isinstance(city_id, int):
                return city_id
    return None


async def send_budget_to_api(
    req: TripRequest,
    plan_response: TripPlanResponse,
    destination_city_id: Optional[int],
) -> Dict[str, Any]:
    """إرسال بيانات الميزانية إلى /api/budgets."""
    # تحديد معرف المدينة الوجهة
    to_city_id = req.to_city_id or destination_city_id
    if to_city_id is None:
        # محاولة البحث عن المدينة من الاسم
        to_city_id = await find_city_id_by_name(req.destination)
    
    if to_city_id is None:
        raise HTTPException(
            status_code=400,
            detail="لم يتم العثور على معرف المدينة الوجهة. يرجى إرسال to_city_id أو city_id.",
        )
    
    if req.from_city_id is None:
        raise HTTPException(
            status_code=400,
            detail="يجب إرسال from_city_id (معرّف المدينة المنطلقة).",
        )
    
    if req.user_id is None:
        raise HTTPException(
            status_code=400,
            detail="يجب إرسال user_id (معرّف المستخدم).",
        )
    
    # حساب النسب المئوية
    percentages = req.percentages or BudgetPercentages()
    hotel_percentage = int(percentages.hotels * 100)
    food_percentage = int(percentages.food * 100)
    activities_percentage = int(percentages.activities * 100)
    transport_percentage = int(percentages.transport * 100)
    
    # بناء قائمة budget_sub
    budget_sub = []
    
    # المطاعم (restaurant)
    if plan_response.food.suggested_items:
        restaurant_items = []
        for item in plan_response.food.suggested_items:
            if item.id is not None:
                restaurant_items.append({
                    "type_id": item.id,
                    "amount": item.price,
                    "types": "meal day"
                })
        
        if restaurant_items:
            budget_sub.append({
                "type": "restaurant",
                "presentaige": food_percentage,
                "description": plan_response.food.message or "Restaurant budget",
                "items": restaurant_items
            })
    
    # الفنادق (hotel)
    if plan_response.hotels.suggested_items:
        hotel_items = []
        for item in plan_response.hotels.suggested_items:
            if item.id is not None:
                hotel_items.append({
                    "type_id": item.id,
                    "amount": item.price,
                    "types": "night"
                })
        
        if hotel_items:
            budget_sub.append({
                "type": "hotel",
                "presentaige": hotel_percentage,
                "description": plan_response.hotels.message or "Hotel budget",
                "items": hotel_items
            })
    
    # الأنشطة (activities)
    if plan_response.activities.suggested_items:
        activity_items = []
        for item in plan_response.activities.suggested_items:
            if item.id is not None:
                activity_items.append({
                    "type_id": item.id,
                    "amount": item.price,
                    "types": "activity"
                })
        
        if activity_items:
            budget_sub.append({
                "type": "activities",
                "presentaige": activities_percentage,
                "description": plan_response.activities.message or "Activities budget",
                "items": activity_items
            })
    
    # الطيران (plane)
    if plan_response.transport.suggested_items:
        plane_items = []
        for item in plan_response.transport.suggested_items:
            if item.id is not None:
                plane_items.append({
                    "type_id": item.id,
                    "amount": item.price,
                    "types": "round trip"
                })
        
        if plane_items:
            budget_sub.append({
                "type": "plane",
                "presentaige": transport_percentage,
                "description": plan_response.transport.message or "Transport budget",
                "items": plane_items
            })
    
    # بناء البيانات الكاملة
    budget_data = {
        "name": req.name or f"Trip Budget - {req.destination}",
        "address": req.address or "123 Budget St",
        "teams_number": req.people_count,
        "days": req.days,
        "amount": f"{req.total_budget:.2f}",
        "from_city_id": req.from_city_id,
        "to_city_id": to_city_id,
        "user_id": req.user_id,
        "budget_sub": budget_sub
    }
    
    # إرسال البيانات إلى /api/budgets
    url = f"{EXTERNAL_API_BASE_URL}/budgets"
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=COMMON_HEADERS, json=budget_data)
    
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Error sending budget to API (status={resp.status_code}): {resp.text}",
        )
    
    return resp.json()


# ======================== تنقية وتحويل بيانات الـ API ======================== #


def filter_and_map_items(
    raw_items: List[Dict[str, Any]],
    max_price_per_day: float,
    destination: str,
) -> List[ExternalItem]:
    """ترشيح وتحويل العناصر القادمة من الـ APIs إلى بنية موحدة."""

    destination_lower = destination.lower().strip()

    price_field_candidates = [
        "price",
        "price_per_night",
        "min_price",
        "max_price",
        "amount",
        "cost",
    ]

    location_field_candidates = [
        "city",
        "city_name",
        "region",
        "location",
        "destination",
        "area",
        "address",
    ]

    name_field_candidates = [
        "name",
        "title",
        "hotel_name",
        "activity_name",
    ]

    url_field_candidates = [
        "url",
        "link",
        "website",
        "booking_url",
    ]

    def coerce_price(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.replace(",", "").strip())
            except ValueError:
                return None
        return None

    def extract_price(item: Dict[str, Any]) -> Optional[float]:
        # مباشرة من الحقول المحتملة
        for key in price_field_candidates:
            if key in item:
                price = coerce_price(item[key])
                if price is not None:
                    return price

        # price_range مثل { "min": 500, "max": 1200 } أو { "from": "45.00" }
        price_range = item.get("price_range")
        if isinstance(price_range, dict):
            for key in ("min", "from", "start", "low", "price", "amount", "minimum"):
                if key in price_range:
                    price = coerce_price(price_range[key])
                    if price is not None:
                        return price

        # بعض المطاعم تحتوي على foods مع price_range داخلها
        foods = item.get("foods")
        entries: List[Dict[str, Any]] = []
        if isinstance(foods, dict):
            entries = foods.get("data") or []
        elif isinstance(foods, list):
            entries = foods

        best_food_price: Optional[float] = None
        for entry in entries:
            if isinstance(entry, dict):
                price = extract_price(entry)
                if price is not None:
                    if best_food_price is None or price < best_food_price:
                        best_food_price = price
        if best_food_price is not None:
            return best_food_price

        return None

    def extract_location(item: Dict[str, Any]) -> Optional[str]:
        for key in location_field_candidates:
            if key in item and item[key]:
                value = item[key]
                if isinstance(value, str):
                    return value
                if isinstance(value, dict):
                    # مثلاً item["city"] = { "name": "Riyadh", ... }
                    for sub_key in ("name", "city", "region", "address"):
                        sub_val = value.get(sub_key)
                        if isinstance(sub_val, str) and sub_val:
                            return sub_val

        # fallback إذا كان هناك city dict
        city = item.get("city")
        if isinstance(city, dict):
            name = city.get("name")
            if isinstance(name, str):
                return name
        return None

    def extract_name(item: Dict[str, Any]) -> str:
        for key in name_field_candidates:
            if key in item and item[key]:
                return str(item[key])
        # fallback للمدينة أو العنوان
        city = item.get("city")
        if isinstance(city, dict) and city.get("name"):
            return str(city["name"])
        if item.get("address"):
            return str(item["address"])
        return "Unknown"

    def extract_url(item: Dict[str, Any]) -> Optional[str]:
        for key in url_field_candidates:
            if key in item and item[key]:
                return str(item[key])
        return None

    results: List[ExternalItem] = []

    for item in raw_items:
        price_value = extract_price(item)
        if price_value is None:
            continue

        # نطاق الأسعار إن وجد
        min_price_val: Optional[float] = None
        max_price_val: Optional[float] = None
        price_range = item.get("price_range")
        if isinstance(price_range, dict):
            min_price_val = coerce_price(
                price_range.get("min") or price_range.get("from")
            )
            max_price_val = coerce_price(
                price_range.get("max") or price_range.get("to")
            )

        location_value = extract_location(item)
        if location_value:
            if destination_lower not in location_value.lower():
                continue

        name_value = extract_name(item)
        url_value = extract_url(item)

        item_id: Optional[int] = None
        if "id" in item:
            try:
                item_id = int(item["id"])
            except (TypeError, ValueError):
                item_id = None

        results.append(
            ExternalItem(
                id=item_id,
                name=name_value,
                price=price_value,
                min_price=min_price_val or price_value,
                max_price=max_price_val or price_value,
                location=location_value,
                url=url_value,
                raw=item,
            )
        )

    results.sort(key=lambda x: x.price)
    return results


# ======================== Endpoints رئيسية ======================== #


@app.post(
    "/plan-trip",
    response_model=TripPlanResponse,
    summary="اقتراح خطة سفر كاملة",
    tags=["trip-planner"],
)
async def plan_trip(req: TripRequest) -> TripPlanResponse:
    """يستقبل بيانات الرحلة ويقترح خطة كاملة.

    - إدخال:
        * الميزانية الكلية
        * عدد الأشخاص
        * مدة الرحلة
        * الوجهة (مدينة/منطقة)
        * (اختياري) نسب مخصصة لتوزيع الميزانية
    - إخراج:
        * تكلفة لكل شخص
        * تكلفة لكل شخص في اليوم
        * الميزانية اليومية لكل فئة
        * قائمة بالاقتراحات (فنادق، أنشطة، مواصلات) مناسبة للميزانية والوجهة
    """
    per_person_total, per_person_per_day, budgets_per_day = calculate_budgets(req)

    # إذا تم إرسال city_id نحوله إلى اسم المدينة من API المدن
    destination_name = req.destination
    if req.city_id is not None:
        cities = await fetch_cities()
        city_match = next((c for c in cities if c.get("id") == req.city_id), None)
        if city_match is None:
            raise HTTPException(
                status_code=400,
                detail=f"لم يتم العثور على مدينة بالمعرّف {req.city_id}",
            )
        city_name = city_match.get("name")
        if isinstance(city_name, str) and city_name.strip():
            destination_name = city_name.strip()

    activities_raw, hotels_raw, plains_raw = await fetch_all_external()
    restaurants_raw = await fetch_list_from_external("restaurants")

    def build_category(
        raw: List[Dict[str, Any]],
        budget: float,
        label: str,
    ) -> CategorySuggestion:
        # أولاً نرشّح حسب المدينة ونحوّل إلى عناصر موحدة
        mapped = filter_and_map_items(
            raw,
            max_price_per_day=budget,  # الميزانية تستخدم فقط كنقطة مرجعية للسعر
            destination=destination_name,
        )
        if not mapped:
            return CategorySuggestion(
                budget_per_day=budget,
                suggested_items=[],
                within_budget=False,
                message=f"لا توجد اختيارات متاحة لفئة {label} في هذه المدينة.",
            )

        # عناصر ضمن الميزانية
        affordable = [item for item in mapped if item.price <= budget]
        affordable.sort(key=lambda x: x.price)

        if affordable:
            # نرجع حتى 3 اقتراحات ضمن الميزانية
            return CategorySuggestion(
                budget_per_day=budget,
                suggested_items=affordable[:3],
                within_budget=True,
                message=None,
            )

        # لا يوجد أي عنصر ضمن الميزانية -> نرجع أرخص عنصر واحد، مع رسالة زيادة الميزانية
        cheapest = mapped[0]
        return CategorySuggestion(
            budget_per_day=budget,
            suggested_items=[cheapest],
            within_budget=False,
            message=(
                f"أقل اختيار متاح لفئة {label} أغلى من الميزانية اليومية لهذه الفئة؛ "
                f"قم بزيادة الميزانية أو تقليل مدة الرحلة أو عدد الأشخاص."
            ),
        )

    hotels_cat = build_category(hotels_raw, budgets_per_day["hotels"], "الفنادق")
    activities_cat = build_category(activities_raw, budgets_per_day["activities"], "الأنشطة")
    transport_cat = build_category(plains_raw, budgets_per_day["transport"], "المواصلات/الطيران")
    food_cat = build_category(restaurants_raw, budgets_per_day["food"], "المطاعم")

    plan_response = TripPlanResponse(
        per_person_total=per_person_total,
        per_person_per_day=per_person_per_day,
        budgets_per_day=budgets_per_day,
        hotels=hotels_cat,
        food=food_cat,
        activities=activities_cat,
        transport=transport_cat,
    )
    
    # تحديد معرف المدينة الوجهة
    destination_city_id = req.city_id
    if destination_city_id is None:
        # محاولة البحث عن المدينة من الاسم
        destination_city_id = await find_city_id_by_name(destination_name)
    
    # إرسال البيانات إلى /api/budgets إذا كانت الحقول المطلوبة موجودة
    if req.from_city_id is not None and req.user_id is not None:
        try:
            budget_api_response = await send_budget_to_api(
                req, plan_response, destination_city_id
            )
            # يمكن إضافة budget_api_response إلى الاستجابة إذا أردت
        except HTTPException:
            # إذا فشل الإرسال، نرجع الخطة فقط بدون إيقاف العملية
            pass
    
    return plan_response


@app.get(
    "/external-preview",
    summary="معاينة سريعة للاستجابات الخام من APIs خارجية",
    tags=["external"],
)
async def external_preview(limit: int = 3) -> Dict[str, Any]:
    """للاستخدام أثناء التطوير فقط:
    يرجع أول (limit) عناصر من كل API خارجي
    حتى ترى شكل الـ JSON وتعدّل الحقول في filter_and_map_items.
    """
    activities_raw, hotels_raw, plains_raw = await fetch_all_external()
    return {
        "activities_sample": activities_raw[:limit],
        "hotels_sample": hotels_raw[:limit],
        "plains_sample": plains_raw[:limit],
    }


@app.get(
    "/external/activities",
    summary="عرض البيانات الخام للأنشطة من الـ API الخارجي",
    tags=["external"],
)
async def external_activities() -> List[Dict[str, Any]]:
    """Proxy بسيط يعرض نفس البيانات التي ترجعها
    `https://insidethekingdom.online/api/activities`
    لكن عبر خدمتك، مع ظهورها في الـ API Docs.
    """
    return await fetch_list_from_external("activities")


@app.get(
    "/external/hotels",
    summary="عرض البيانات الخام للفنادق من الـ API الخارجي",
    tags=["external"],
)
async def external_hotels() -> List[Dict[str, Any]]:
    """Proxy بسيط يعرض نفس البيانات التي ترجعها
    `https://insidethekingdom.online/api/hotels`
    لكن عبر خدمتك، مع ظهورها في الـ API Docs.
    """
    return await fetch_list_from_external("hotels")


@app.get(
    "/external/plains",
    summary="عرض البيانات الخام للطيران/المواصلات من الـ API الخارجي",
    tags=["external"],
)
async def external_plains() -> List[Dict[str, Any]]:
    """Proxy بسيط يعرض نفس البيانات التي ترجعها
    `https://insidethekingdom.online/api/plains`
    لكن عبر خدمتك، مع ظهورها في الـ API Docs.
    """
    return await fetch_list_from_external("plains")


@app.get(
    "/external/restaurants",
    summary="عرض بيانات المطاعم من الـ API الخارجي",
    tags=["external"],
)
async def external_restaurants() -> List[Dict[str, Any]]:
    """Proxy يعرض محتوى https://insidethekingdom.online/api/restaurants"""
    return await fetch_list_from_external("restaurants")


@app.get(
    "/external/cities",
    summary="عرض قائمة المدن من الـ API الخارجي",
    tags=["external"],
)
async def external_cities() -> List[Dict[str, Any]]:
    """يعرض نفس استجابة https://insidethekingdom.online/api/cities"""
    return await fetch_cities()


@app.get(
    "/health",
    summary="فحص بسيط أن الخدمة تعمل",
    tags=["meta"],
)
async def health() -> Dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


