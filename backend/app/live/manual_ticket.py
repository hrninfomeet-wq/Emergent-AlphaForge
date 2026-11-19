"""Capabilities of the existing supervised manual executor, without I/O."""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any, Dict


def manual_ticket_verdict(ticket: Dict[str, Any]) -> Dict[str, Any]:
    """Never approve a ticket which the one-lot executor would silently change."""
    supported = (
        str(ticket.get('side', '')).upper() == 'B'
        and type(ticket.get('lots')) is int and ticket['lots'] == 1
        and str(ticket.get('product', '')).upper() == 'MIS'
        and str(ticket.get('order_type', '')).upper() == 'LIMIT'
    )
    reason = 'Manual placement supports one-lot BUY, MIS, LIMIT only; other tickets are preview-only.'
    buffer = ticket.get('buffer_pct')
    if supported and buffer is not None:
        supported = (isinstance(buffer, (int, float)) and not isinstance(buffer, bool)
                     and math.isfinite(buffer) and buffer == 0.5)
        reason = 'Manual placement uses a 0.5% limit buffer; a different preview buffer cannot be approved.'
    return {'check': 'manual_execution', 'ok': supported,
            'detail': 'Supported manual instruction: one-lot BUY, MIS, LIMIT.' if supported else reason}


def same_approved_entry(expected: Any, actual: Any) -> bool:
    """Compare execution fields, excluding fresh transport IDs and account IDs."""
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return False
    for key in ('exch', 'tsym', 'trantype', 'prd', 'prctyp', 'ret'):
        if not expected.get(key) or expected[key] != actual.get(key):
            return False
    try:
        for key in ('qty', 'prc'):
            a, b = Decimal(str(expected.get(key))), Decimal(str(actual.get(key)))
            if not a.is_finite() or not b.is_finite() or a <= 0 or a != b:
                return False
        # A LIMIT entry has no trigger; tolerate the broker's explicit zero.
        for item in (expected, actual):
            if item.get('trgprc') not in (None, '', 0, 0.0, '0', '0.0'):
                return False
    except (InvalidOperation, ValueError, TypeError):
        return False
    return True
