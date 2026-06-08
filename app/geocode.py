"""
Geocode UK postcodes using the free postcodes.io API.
Also provides postcode → council lookup.
"""
import httpx
from typing import Optional


async def postcode_to_latlng(postcode: str) -> Optional[tuple[float, float]]:
    """Returns (lat, lng) or None if invalid."""
    postcode = postcode.strip().upper().replace(" ", "")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"https://api.postcodes.io/postcodes/{postcode}")
            if r.status_code != 200:
                return None
            data = r.json()
            if data.get("status") != 200:
                return None
            result = data["result"]
            return result["latitude"], result["longitude"]
    except Exception:
        return None


async def postcode_to_council(postcode: str) -> Optional[str]:
    """Returns the council/admin district name for a postcode."""
    postcode = postcode.strip().upper().replace(" ", "")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"https://api.postcodes.io/postcodes/{postcode}")
            if r.status_code != 200:
                return None
            data = r.json()
            if data.get("status") != 200:
                return None
            result = data["result"]
            # Try admin_district first, then admin_county
            return (
                result.get("admin_district")
                or result.get("admin_county")
            )
    except Exception:
        return None


async def postcode_lookup(postcode: str) -> Optional[dict]:
    """Returns full postcode data including lat, lng, council name."""
    postcode = postcode.strip().upper().replace(" ", "")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"https://api.postcodes.io/postcodes/{postcode}")
            if r.status_code != 200:
                return None
            data = r.json()
            if data.get("status") != 200:
                return None
            result = data["result"]
            return {
                "postcode": result["postcode"],
                "lat": result["latitude"],
                "lng": result["longitude"],
                "council": result.get("admin_district") or result.get("admin_county"),
                "region": result.get("region"),
            }
    except Exception:
        return None
