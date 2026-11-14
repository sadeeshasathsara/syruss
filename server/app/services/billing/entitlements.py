"""
What an organization may do now: its plan's limits, plus what its add-ons add, with any override on top. Worked out every
time from the subscription; nothing here is stored. None means unlimited.
"""
from typing import Dict, Iterable, Mapping, Optional, Tuple
from app.services.billing.limits import LIMITS

Limits = Dict[str, Optional[int]]


def compose(plan: Mapping[str, Optional[int]], addons: Iterable[Tuple[Mapping[str, int], int]] = (),
            overrides: Mapping[str, Optional[int]] = {}) -> Limits:
    """
    plan: the plan version's limits; a limit it doesn't name is unlimited.
    addons: (what one unit adds, quantity) for each add-on; adding to an unlimited limit leaves it unlimited.
    overrides: values that replace the result, in the order given (organization first, then the workspace's own).
    """
    out: Limits = {key: plan.get(key) for key in LIMITS}
    for grants, quantity in addons:
        for key, amount in grants.items():
            if key in out and out[key] is not None:
                out[key] += int(amount) * quantity
    for key, value in overrides.items():
        if key in out:
            out[key] = value
    return out
