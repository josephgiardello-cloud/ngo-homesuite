import requests
from datetime import datetime

def get_historical_rate(base: str, target: str, date: str) -> float:
    """
    Fetch historical exchange rate for base/target on given date (YYYY-MM-DD).
    Uses exchangerate.host (free API).
    """
    url = f"https://api.exchangerate.host/{date}?base={base}&symbols={target}"
    resp = requests.get(url)
    data = resp.json()
    return data['rates'][target]
