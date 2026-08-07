"""Calculator card (epic JNO-233, story JNO-237).

Outbound only from this side — no table, no state, no cleanup, same shape as
countdown_service.py. The inbound half (`calculator_result`, JNO-236) is a
branch in bot/router/dsl_text_events.py, and it echoes back its own labels and
values precisely *because* nothing here remembers the card that was sent.

**Python never evaluates the formula.** The client recomputes it live on every
keystroke (JNO-235), so the only job here is to refuse to ship one that isn't
plain arithmetic over the declared fields.

The check is an `ast` walk with a node allowlist rather than a regex, because a
charset regex does not stop `__import__("os").system(...)` — that string is
entirely letters, underscores, quotes and parens. Anything that isn't a number,
a field name, `+ - * /`, a unary sign or a parenthesis is a parse error.

⚠️ This is the FIRST of two gates, not the security boundary. DSL also reaches
the room straight from Supabase Edge Functions, which never pass through
safe_send_dsl() (CLAUDE.md gap #4). On that path the client's own parser is the
only thing standing between a payload and the evaluator, so the Dart side
implements the identical grammar independently.
"""
import ast
import keyword
import logging
import re as _re

from bot.matrix_client import matrix_client
from bot.dsl_validator import safe_send_dsl

logger = logging.getLogger(__name__)

# JNO-237: "number of fields is capped at a fixed maximum". Also mirrored as
# maxItems in schema.json and re-checked client-side — quicktype drops maxItems
# from the generated model, so the schema copy is documentation, not enforcement.
MAX_FIELDS = 8

# A calculator whose *rate* is a customer-entered number quotes the customer
# back their own guess and renders it as if the cafe had computed it. Observed
# in testing: the model had the real menu prices in context and still built
# `guests * item_price` with "Price per item" as an input, so 20 coffees came
# back as "Estimated total 40 PKR".
#
# The customer supplies quantities; the cafe supplies rates — as constants in
# the formula or as numeric `value`s on a choice field.
#
# Matched on WORD boundaries, not as substrings: "How many crates?" contains
# "rate" and "Feet of decking" contains "fee", and a plain `in` test rejected
# both. Underscores are normalised to spaces first so a key like "item_price"
# still trips it — `_` is a word character, so \bprice\b would not match inside
# it otherwise.
# ponytail: word list, not semantics — upgrade only if real labels slip past.
_RATE_WORD_RE = _re.compile(r'\b(?:price|cost|rate|charge|fee)s?\b')

# The entire permitted formula language. Note what is deliberately absent:
#   ast.Pow  — `9**9**9` is a one-token DoS on whichever runtime evaluates it
#   ast.Mod  — not in the epic's "+, -, x, /, parentheses, field references"
#   ast.Call / ast.Attribute / ast.Subscript — arbitrary code execution
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp, ast.UnaryOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.UAdd, ast.USub,
    ast.Constant, ast.Name, ast.Load,
)


def validate_formula(formula: str, keys: set[str]) -> str | None:
    """Return a human-readable reason the formula is unsafe, or None if it's fine.

    `keys` is the set of declared field keys — an identifier outside it is
    rejected, so a formula can never reference a variable the card doesn't
    render an input for.
    """
    if not isinstance(formula, str) or not formula.strip():
        return "formula is empty"
    try:
        tree = ast.parse(formula, mode="eval")
    except (SyntaxError, ValueError):
        return "formula is not a valid arithmetic expression"

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return f"formula uses {type(node).__name__}, which is not allowed"
        if isinstance(node, ast.Name) and node.id not in keys:
            return f"formula references unknown field {node.id!r}"
        if isinstance(node, ast.Constant):
            # bool is a subclass of int — exclude it explicitly, same care the
            # auction amounts take about floats in canonical JSON.
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                return "formula may only contain numeric constants"
        # A literal `/ 0` is the one division-by-zero we can catch before
        # sending. Everything else depends on runtime input and is the client's
        # "can't calculate yet" state (JNO-235).
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            if isinstance(right, ast.Constant) and right.value == 0:
                return "formula divides by a literal zero"
    return None


def validate_fields(fields) -> str | None:
    """Return a reason the field list is unusable, or None if it's fine."""
    if not isinstance(fields, list) or not fields:
        return "at least one field is required"
    if len(fields) > MAX_FIELDS:
        return f"too many fields ({len(fields)} > {MAX_FIELDS})"

    seen: set[str] = set()
    for f in fields:
        if not isinstance(f, dict):
            return "each field must be an object"

        key = str(f.get("key", "")).strip()
        # The key becomes an identifier in the formula, so it has to parse as
        # one — "2x" or "total price" would make every formula a SyntaxError,
        # and a Python keyword like "if" would parse as something else entirely.
        if not key.isidentifier() or keyword.iskeyword(key):
            return f"field key {key!r} is not a valid identifier"
        if key in seen:
            return f"duplicate field key {key!r}"
        seen.add(key)

        if not str(f.get("label", "")).strip():
            return f"field {key!r} has no label"

        ftype = f.get("type")
        if ftype not in ("number", "choice"):
            return f"field {key!r} has unsupported type {ftype!r}"

        if ftype == "number":
            haystack = f"{key} {f.get('label', '')}".lower().replace("_", " ")
            if _RATE_WORD_RE.search(haystack):
                return (
                    f"field {key!r} asks the customer to type a price — rates must "
                    "come from the menu, as constants in the formula or as numeric "
                    "option values on a choice field"
                )

        if ftype == "choice":
            options = f.get("options")
            if not isinstance(options, list) or not options:
                return f"choice field {key!r} has no options"
            for opt in options:
                if not isinstance(opt, dict) or not str(opt.get("label", "")).strip():
                    return f"choice field {key!r} has an option with no label"
                value = opt.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    # The option's value is the number the formula sees; its
                    # label is what the customer reads. Keeping the value
                    # numeric is what lets the grammar stay at four operators
                    # instead of needing a lookup construct.
                    return f"choice field {key!r} has a non-numeric option value"
    return None


async def send_calculator_card(
    room_id: str,
    title: str,
    fields: list[dict],
    formula: str,
    result_label: str,
    subtitle: str = "",
    result_unit: str = "",
) -> bool:
    """Send a calculator card. Returns True if it went out.

    `subtitle`/`result_unit` are omitted from the payload when empty rather than
    sent as "", so the client falls back to its own default — same convention as
    the countdown card.
    """
    if not str(title).strip() or not str(result_label).strip():
        logger.error("❌ calculator needs both a title and a result_label")
        return False

    error = validate_fields(fields)
    if not error:
        error = validate_formula(formula, {str(f.get("key", "")).strip() for f in fields})
    if error:
        logger.error(f"❌ Calculator rejected | {error} | formula={formula!r}")
        return False

    dsl = {
        "v": 1,
        "type": "calculator",
        "data": {
            "title": title,
            "fields": fields,
            "formula": formula,
            "result_label": result_label,
            **({"subtitle": subtitle} if subtitle else {}),
            **({"result_unit": result_unit} if result_unit else {}),
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ calculator DSL invalid, not sending")
        return False

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={"msgtype": "m.text", "body": title, "ai.jaeno.dsl": dsl},
    )
    logger.info(f"🧮 Calculator sent | {title} | {len(fields)} fields | {formula}")
    return True
