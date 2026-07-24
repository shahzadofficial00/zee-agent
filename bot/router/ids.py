import secrets
import string

# ─────────────────────────────────────────────────────────────────────────────
# ORDER ID GENERATION — human-readable stable_order_id (ORD-XXXXXX)
# ─────────────────────────────────────────────────────────────────────────────
def generate_order_id() -> str:
    alphabet = string.ascii_uppercase + string.digits
    suffix = ''.join(secrets.choice(alphabet) for _ in range(6))
    return f"ORD-{suffix}"

async def generate_unique_order_id() -> str:
    from db import order_id_exists
    for _ in range(5):
        candidate = generate_order_id()
        if not order_id_exists(candidate):
            return candidate
    return generate_order_id() + secrets.choice(string.ascii_uppercase)


def generate_tip_id() -> str:
    alphabet = string.ascii_uppercase + string.digits
    suffix = ''.join(secrets.choice(alphabet) for _ in range(6))
    return f"TIP-{suffix}"

async def generate_unique_tip_id() -> str:
    from db import order_id_exists
    for _ in range(5):
        candidate = generate_tip_id()
        if not order_id_exists(candidate):
            return candidate
    return generate_tip_id() + secrets.choice(string.ascii_uppercase)
