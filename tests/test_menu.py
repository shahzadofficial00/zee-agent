"""Menu is local now (was Supabase). Run: python -m tests.test_menu

Guards the three things that actually break the menu card:
  1. a fresh DB seeds itself — restaurant.db is gitignored, so _SEED_MENU is
     the only tracked copy of the menu;
  2. price comes back an int — the card does str(price), and the old REAL
     column rendered "10.0";
  3. every seeded row has the fields menu_service.py reads.
"""
import asyncio
import os
import tempfile

import db.connection as conn_mod

_tmp = os.path.join(tempfile.mkdtemp(), "test.db")
conn_mod.DB_PATH = _tmp

from db import init_db, get_menu_items  # noqa: E402  (must follow the DB_PATH swap)
from db.menu import _SEED_MENU  # noqa: E402


def main():
    init_db()
    items = asyncio.run(get_menu_items())

    assert len(items) == len(_SEED_MENU), f"seeded {len(items)}, expected {len(_SEED_MENU)}"
    print(f"[1/4] fresh DB seeds {len(items)} items")

    for i in items:
        assert isinstance(i["price"], int), f"{i['name']}: price is {type(i['price'])}"
        assert str(i["price"]).isdigit(), f"{i['name']}: price renders as {i['price']!r}"
    print("[2/4] every price is an int (card does str(price))")

    for i in items:
        for field in ("id", "name", "price", "image", "category", "description"):
            assert field in i, f"{i['name']} missing {field}"
        assert i["name"] and i["category"], f"blank name/category on id {i['id']}"
    print("[3/4] every row has the fields menu_service reads")

    # Seeding is idempotent: a second init must not duplicate the menu.
    init_db()
    assert len(asyncio.run(get_menu_items())) == len(_SEED_MENU), "re-init duplicated rows"
    print("[4/4] re-init does not re-seed")

    print(f"\nAll 4 checks passed ({len(items)} items, "
          f"{len({i['category'] for i in items})} categories)")


if __name__ == "__main__":
    main()
