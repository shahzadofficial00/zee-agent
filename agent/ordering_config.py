# ─────────────────────────────────────────────────────────────────
# ORDERING CONTROL — edit these by hand, then RESTART the bot.
# No chat commands, no database, no live toggling. Pure code.
# ─────────────────────────────────────────────────────────────────

# JNO-133 — master switch for the whole menu.
# True  = customers can order normally.
# False = nothing on the menu is orderable, menu becomes browse-only.
ORDERING_ENABLED = True


# JNO-134 — per-item overrides.
# Add an item name (case-insensitive) and set it to False to disable
# ordering for just that item. Leave items out entirely to keep them
# orderable (default). Only list items you want to turn OFF.
#
# Example:
# ITEM_ORDERABLE_OVERRIDES = {
#     "espresso": False,
#     "cold latte": False,
# }
ITEM_ORDERABLE_OVERRIDES = {


}