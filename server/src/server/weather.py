"""Active National Weather Service alerts for the home location (US only, no API key)."""
import httpx

NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"


def parse_alerts(data: dict) -> list[dict]:
    alerts = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        alerts.append({
            "event": props.get("event"),        # e.g. "Severe Thunderstorm Warning"
            "severity": props.get("severity"),  # e.g. "Severe", "Extreme"
            "urgency": props.get("urgency"),    # e.g. "Immediate", "Expected"
            "onset": props.get("onset"),
            "ends": props.get("ends") or props.get("expires"),
            "headline": props.get("headline"),
        })
    return alerts


def get_alerts(lat: float, lon: float, contact: str) -> list[dict]:
    # NWS asks for a User-Agent that identifies the app and a contact
    response = httpx.get(
        NWS_ALERTS_URL,
        params={"point": f"{lat},{lon}"},
        headers={"User-Agent": f"Powerwall-MCP-Agent, contact: {contact}"},
        timeout=10.0,
    )
    response.raise_for_status()
    return parse_alerts(response.json())
