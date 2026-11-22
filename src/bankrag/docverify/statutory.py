"""SIMPLIFIED Malaysian payroll deductions, shared by the synthetic generator and the statutory check.

Not the official tables: EPF employee share 11% rounded up to the next ringgit; SOCSO 0.5% and EIS 0.2% of
wages capped at RM6,000; PCB from a simplified progressive scale with RM9,000 personal relief, EPF relief
up to RM4,000 and the RM400 rebate. Good enough to give documents realistic structure.
"""
from __future__ import annotations

import math

WAGE_CEILING = 6000


def epf(gross: float) -> float:
    return float(math.ceil(round(0.11 * gross, 6)))


def epf_employer(gross: float) -> float:
    return float(math.ceil(round(0.13 * gross, 6)))


def socso(gross: float) -> float:
    return round(0.005 * min(gross, WAGE_CEILING), 2)


def eis(gross: float) -> float:
    return round(0.002 * min(gross, WAGE_CEILING), 2)


def pcb(gross: float, epf_amt: float, relief_factor: float = 1.0) -> float:
    chargeable = max(0.0, 12 * gross - min(12 * epf_amt, 4000) - 9000)
    tax, left = 0.0, chargeable
    for width, rate in ((5000, 0), (15000, .01), (15000, .03), (15000, .06), (20000, .11), (30000, .19),
                        (300000, .25), (float("inf"), .26)):
        part = min(left, width)
        tax += part * rate
        left -= part
        if left <= 0:
            break
    if chargeable <= 35000:
        tax = max(0.0, tax - 400)
    return round(tax / 12 * relief_factor, 2)
