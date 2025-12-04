import os
import asyncio
from typing import Optional, List, Dict, Any, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import httpx
from dotenv import load_dotenv


load_dotenv()

EXTERNAL_API_BASE_URL = "https://insidethekingdom.online/api"

API_TOKEN = os.getenv("INSIDE_KINGDOM_TOKEN")
if not API_TOKEN:
    raise ValueError("INSIDE_KINGDOM_TOKEN environment variable is required")

COMMON_HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {API_TOKEN}",
}


app = FastAPI(
    title="Smart Travel Planner",
    description=(
        "Service for planning trips based on external data from insidethekingdom.online\n\n"
        "- Input: number of people, total budget, trip duration, destination\n"
        "- Output: hotel suggestions, activities, and transportation options suitable for the budget per category\n"
        "This service does not make bookings, only displays data and links to external platforms."
    ),
    version="1.0.0",
)


class BudgetPercentages(BaseModel):
    """Daily budget distribution percentages across different categories."""

    hotels: float = Field(
        0.4, ge=0, le=1, description="Hotel budget percentage of daily budget"
    )
    food: float = Field(
        0.25, ge=0, le=1, description="Food budget percentage of daily budget"
    )
    activities: float = Field(
        0.2, ge=0, le=1, description="Activities budget percentage of daily budget"
    )
    transport: float = Field(
        0.15, ge=0, le=1, description="Transportation/flight budget percentage of daily budget"
    )


class TripRequest(BaseModel):
    """Trip plan calculation request."""

    total_budget: float = Field(..., gt=0, description="Total budget in Saudi Riyal")
    people_count: int = Field(..., gt=0, description="Number of people on the trip")
    days: int = Field(..., gt=0, description="Trip duration in days")
    destination: str = Field(
        ..., description="City or region name (e.g., Riyadh). Can be ignored if city_id is sent."
    )
    city_id: Optional[int] = Field(
        None,
        description=(
            "City ID from cities API (/cities). "
            "If sent, the associated city name will be used to filter data."
        ),
    )
    percentages: Optional[BudgetPercentages] = Field(
        None,
        description="Custom budget distribution percentages. If not sent, default values are used.",
    )
    name: Optional[str] = Field(None, description="Trip name (to send to /api/budgets)")
    address: Optional[str] = Field(None, description="Trip address (to send to /api/budgets)")
    from_city_id: Optional[int] = Field(None, description="Departure city ID")
    to_city_id: Optional[int] = Field(None, description="Destination city ID (if not sent, city_id will be used)")
    user_id: Optional[int] = Field(None, description="User ID (to send to /api/budgets)")


class ExternalItem(BaseModel):
    """Representation of an item (hotel/activity/flight...) from external API after sorting and filtering."""

    id: Optional[int] = None
    name: str
    price: float
    min_price: Optional[float] = Field(
        None, description="Minimum price (from price_range.min or similar) if available"
    )
    max_price: Optional[float] = Field(
        None, description="Maximum price (from price_range.max or similar) if available"
    )
    location: Optional[str] = None
    url: Optional[str] = None
    raw: Optional[Dict[str, Any]] = Field(
        None,
        description="Complete raw data from external API for easy display in the interface if needed.",
    )


class CategorySuggestion(BaseModel):
    """Suggestions for a specific category (hotels, activities, ...)."""

    budget_per_day: float
    suggested_items: List[ExternalItem]
    within_budget: bool = Field(
        True,
        description="Are all suggestions within the daily budget for this category?",
    )
    message: Optional[str] = Field(
        None,
        description="Explanatory message (e.g., increase budget for this category).",
    )


class TripPlanResponse(BaseModel):
    """Complete trip plan response."""

    per_person_total: float
    per_person_per_day: float
    budgets_per_day: Dict[str, float]
    hotels: CategorySuggestion
    food: CategorySuggestion
    activities: CategorySuggestion
    transport: CategorySuggestion


def calculate_budgets(req: TripRequest) -> Tuple[float, float, Dict[str, float]]:
    """Calculate basic budgets:
    - Budget per person
    - Budget per person per day
    - Daily budget distribution across categories
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
            detail="Sum of percentages (hotels + food + activities + transport) must equal 1.0",
        )

    budgets_per_day = {
        "hotels": per_person_per_day * percentages.hotels,
        "food": per_person_per_day * percentages.food,
        "activities": per_person_per_day * percentages.activities,
        "transport": per_person_per_day * percentages.transport,
    }

    return per_person_total, per_person_per_day, budgets_per_day


async def fetch_list_from_external(
    endpoint: str, params: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Simple GET call from external API and return list of items.

    Note: The actual response format may be:
    - Direct list []
    - Or object with data key: { "data": [...] }
    So we try to support both cases.
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
        if "data" in data and isinstance(data["data"], list):
            return data["data"]
        if "items" in data and isinstance(data["items"], list):
            return data["items"]

    if isinstance(data, list):
        return data

    raise HTTPException(
        status_code=502,
        detail=f"Unexpected response format from external API at {endpoint}",
    )


async def fetch_all_external() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fetch activities, hotels, and flights (plains) data in parallel."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        activities_coro = client.get(
            f"{EXTERNAL_API_BASE_URL}/activities", headers=COMMON_HEADERS
        )
        hotels_coro = client.get(
            f"{EXTERNAL_API_BASE_URL}/hotels", headers=COMMON_HEADERS
        )
        plains_coro = client.get(
            f"{EXTERNAL_API_BASE_URL}/plains", headers=COMMON_HEADERS
        )

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
        return []

    return (
        normalize(res_activities),
        normalize(res_hotels),
        normalize(res_plains),
    )


async def fetch_cities() -> List[Dict[str, Any]]:
    """Fetch list of cities from /cities."""
    return await fetch_list_from_external("cities")


async def find_city_id_by_name(city_name: str) -> Optional[int]:
    """Search for city ID by name."""
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
    """Send budget data to /api/budgets."""
    to_city_id = req.to_city_id or destination_city_id
    if to_city_id is None:
        to_city_id = await find_city_id_by_name(req.destination)
    
    if to_city_id is None:
        raise HTTPException(
            status_code=400,
            detail="Destination city ID not found. Please send to_city_id or city_id.",
        )
    
    if req.from_city_id is None:
        raise HTTPException(
            status_code=400,
            detail="from_city_id (departure city ID) must be sent.",
        )
    
    if req.user_id is None:
        raise HTTPException(
            status_code=400,
            detail="user_id (user ID) must be sent.",
        )
    
    percentages = req.percentages or BudgetPercentages()
    hotel_percentage = int(percentages.hotels * 100)
    food_percentage = int(percentages.food * 100)
    activities_percentage = int(percentages.activities * 100)
    transport_percentage = int(percentages.transport * 100)
    
    budget_sub = []
    
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
    
    url = f"{EXTERNAL_API_BASE_URL}/budgets"
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=COMMON_HEADERS, json=budget_data)
    
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Error sending budget to API (status={resp.status_code}): {resp.text}",
        )
    
    return resp.json()


def filter_and_map_items(
    raw_items: List[Dict[str, Any]],
    max_price_per_day: float,
    destination: str,
) -> List[ExternalItem]:
    """Filter and convert items from APIs to unified structure."""

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
        for key in price_field_candidates:
            if key in item:
                price = coerce_price(item[key])
                if price is not None:
                    return price

        price_range = item.get("price_range")
        if isinstance(price_range, dict):
            for key in ("min", "from", "start", "low", "price", "amount", "minimum"):
                if key in price_range:
                    price = coerce_price(price_range[key])
                    if price is not None:
                        return price

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
                    for sub_key in ("name", "city", "region", "address"):
                        sub_val = value.get(sub_key)
                        if isinstance(sub_val, str) and sub_val:
                            return sub_val

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


@app.post(
    "/plan-trip",
    response_model=TripPlanResponse,
    summary="Suggest complete travel plan",
    tags=["trip-planner"],
)
async def plan_trip(req: TripRequest) -> TripPlanResponse:
    """Receives trip data and suggests a complete plan.

    - Input:
        * Total budget
        * Number of people
        * Trip duration
        * Destination (city/region)
        * (Optional) Custom budget distribution percentages
    - Output:
        * Cost per person
        * Cost per person per day
        * Daily budget per category
        * List of suggestions (hotels, activities, transportation) suitable for budget and destination
    """
    per_person_total, per_person_per_day, budgets_per_day = calculate_budgets(req)

    destination_name = req.destination
    if req.city_id is not None:
        cities = await fetch_cities()
        city_match = next((c for c in cities if c.get("id") == req.city_id), None)
        if city_match is None:
            raise HTTPException(
                status_code=400,
                detail=f"City with ID {req.city_id} not found",
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
        mapped = filter_and_map_items(
            raw,
            max_price_per_day=budget,
            destination=destination_name,
        )
        if not mapped:
            return CategorySuggestion(
                budget_per_day=budget,
                suggested_items=[],
                within_budget=False,
                message=f"No options available for {label} category in this city.",
            )

        affordable = [item for item in mapped if item.price <= budget]
        affordable.sort(key=lambda x: x.price)

        if affordable:
            return CategorySuggestion(
                budget_per_day=budget,
                suggested_items=affordable[:3],
                within_budget=True,
                message=None,
            )

        cheapest = mapped[0]
        return CategorySuggestion(
            budget_per_day=budget,
            suggested_items=[cheapest],
            within_budget=False,
            message=(
                f"The cheapest available option for {label} category is more expensive than the daily budget for this category; "
                f"please increase the budget or reduce trip duration or number of people."
            ),
        )

    hotels_cat = build_category(hotels_raw, budgets_per_day["hotels"], "hotels")
    activities_cat = build_category(activities_raw, budgets_per_day["activities"], "activities")
    transport_cat = build_category(plains_raw, budgets_per_day["transport"], "transportation/flights")
    food_cat = build_category(restaurants_raw, budgets_per_day["food"], "restaurants")

    plan_response = TripPlanResponse(
        per_person_total=per_person_total,
        per_person_per_day=per_person_per_day,
        budgets_per_day=budgets_per_day,
        hotels=hotels_cat,
        food=food_cat,
        activities=activities_cat,
        transport=transport_cat,
    )
    
    destination_city_id = req.city_id
    if destination_city_id is None:
        destination_city_id = await find_city_id_by_name(destination_name)
    
    if req.from_city_id is not None and req.user_id is not None:
        try:
            budget_api_response = await send_budget_to_api(
                req, plan_response, destination_city_id
            )
        except HTTPException:
            pass
    
    return plan_response


@app.get(
    "/external-preview",
    summary="Quick preview of raw responses from external APIs",
    tags=["external"],
)
async def external_preview(limit: int = 3) -> Dict[str, Any]:
    """For development use only:
    Returns first (limit) items from each external API
    so you can see the JSON format and modify fields in filter_and_map_items.
    """
    activities_raw, hotels_raw, plains_raw = await fetch_all_external()
    return {
        "activities_sample": activities_raw[:limit],
        "hotels_sample": hotels_raw[:limit],
        "plains_sample": plains_raw[:limit],
    }


@app.get(
    "/external/activities",
    summary="Display raw activities data from external API",
    tags=["external"],
)
async def external_activities() -> List[Dict[str, Any]]:
    """Simple proxy that displays the same data returned by
    `https://insidethekingdom.online/api/activities`
    but through your service, with appearance in API Docs.
    """
    return await fetch_list_from_external("activities")


@app.get(
    "/external/hotels",
    summary="Display raw hotels data from external API",
    tags=["external"],
)
async def external_hotels() -> List[Dict[str, Any]]:
    """Simple proxy that displays the same data returned by
    `https://insidethekingdom.online/api/hotels`
    but through your service, with appearance in API Docs.
    """
    return await fetch_list_from_external("hotels")


@app.get(
    "/external/plains",
    summary="Display raw flights/transportation data from external API",
    tags=["external"],
)
async def external_plains() -> List[Dict[str, Any]]:
    """Simple proxy that displays the same data returned by
    `https://insidethekingdom.online/api/plains`
    but through your service, with appearance in API Docs.
    """
    return await fetch_list_from_external("plains")


@app.get(
    "/external/restaurants",
    summary="Display restaurants data from external API",
    tags=["external"],
)
async def external_restaurants() -> List[Dict[str, Any]]:
    """Proxy that displays content from https://insidethekingdom.online/api/restaurants"""
    return await fetch_list_from_external("restaurants")


@app.get(
    "/external/cities",
    summary="Display list of cities from external API",
    tags=["external"],
)
async def external_cities() -> List[Dict[str, Any]]:
    """Displays the same response as https://insidethekingdom.online/api/cities"""
    return await fetch_cities()


@app.get(
    "/health",
    summary="Simple check that the service is running",
    tags=["meta"],
)
async def health() -> Dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


