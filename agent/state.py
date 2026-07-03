import time

MENU_PRICES: dict[str, int] = {}
CACHED_MENU: dict = {"title": "Menu", "items": []}

_MENU_CACHE_TTL = 300  # seconds
_MENU_CACHE_AT: float = 0.0


def is_menu_cache_fresh() -> bool:
    return bool(MENU_PRICES) and (time.time() - _MENU_CACHE_AT) < _MENU_CACHE_TTL


def update_menu_cache(items: list) -> dict[str, int]:
    global _MENU_CACHE_AT
    MENU_PRICES.clear()
    MENU_PRICES.update({i["name"].lower(): int(i["price"]) for i in items})
    _MENU_CACHE_AT = time.time()
    return dict(MENU_PRICES)