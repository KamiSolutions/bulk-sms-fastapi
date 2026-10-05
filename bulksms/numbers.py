"""North American (NANP) phone number normalisation for the US and Canada.

The US and Canada share country code +1, so a number's country is decided by
its area code. Other places that also use +1 (Caribbean islands, US
territories) are rejected so nobody is charged international rates by surprise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Canadian geographic and non-geographic area codes (CRTC / CNA list).
CANADA_AREA_CODES = frozenset(
    """
    204 226 236 249 250 257 263 289 306 343 354 365 367 368 382 387 403 416
    418 428 431 437 438 450 460 468 474 506 514 519 548 579 581 584 587 600
    604 613 622 639 647 672 683 705 709 742 753 778 780 782 807 819 825 867
    873 879 902 905 942
    """.split()
)

# +1 area codes that are neither the US mainland/Alaska/Hawaii nor Canada:
# Caribbean nations and US territories (Puerto Rico, USVI, Guam, CNMI, AS).
OTHER_NANP_AREA_CODES = frozenset(
    """
    242 246 264 268 284 340 345 441 473 649 658 664 670 671 684 721 758 767
    784 787 809 829 849 868 869 876 939
    """.split()
)

# Toll-free codes cannot receive SMS as ordinary handsets.
TOLL_FREE_AREA_CODES = frozenset("800 833 844 855 866 877 888".split())


class InvalidNumber(ValueError):
    """Raised when a value is not a sendable US or Canadian mobile number."""


@dataclass(frozen=True)
class NANPNumber:
    e164: str  # e.g. +14165550123
    country: str  # "US" or "CA"

    @property
    def msisdn(self) -> str:
        """The number without the leading plus, as BulkSMS also accepts."""
        return self.e164[1:]


_STRIP = re.compile(r"[\s().\-/]")


def normalize_nanp(raw: str, allowed_countries: frozenset[str] = frozenset({"US", "CA"})) -> NANPNumber:
    """Turn '(416) 555-0123', '1-416-555-0123', '+1 416 555 0123' etc. into E.164.

    Raises InvalidNumber with a human-readable reason if the value can't be sent to.
    """
    if raw is None:
        raise InvalidNumber("empty")
    value = str(raw).strip()
    # Drop a trailing extension: "416-555-0123 x22", "ext. 22".
    value = re.split(r"(?i)\s*(?:ext\.?|x|#)\s*\d+$", value)[0]
    value = _STRIP.sub("", value)
    if not value:
        raise InvalidNumber("empty")

    if value.startswith("+"):
        digits = value[1:]
        if not digits.isdigit():
            raise InvalidNumber("contains non-digit characters")
        if not digits.startswith("1"):
            raise InvalidNumber("not a +1 (US/Canada) number")
        digits = digits[1:]
    else:
        if value.startswith("011"):  # NANP international dialling prefix
            value = value[3:]
            if not value.startswith("1"):
                raise InvalidNumber("not a +1 (US/Canada) number")
        elif value.startswith("00"):  # international prefix used elsewhere
            value = value[2:]
            if not value.startswith("1"):
                raise InvalidNumber("not a +1 (US/Canada) number")
        if not value.isdigit():
            raise InvalidNumber("contains non-digit characters")
        if len(value) == 11 and value.startswith("1"):
            digits = value[1:]
        else:
            digits = value

    if len(digits) != 10:
        raise InvalidNumber(f"expected 10 digits after +1, got {len(digits)}")

    area, exchange = digits[:3], digits[3:6]
    if area[0] in "01" or exchange[0] in "01":
        raise InvalidNumber("area code and exchange cannot start with 0 or 1")
    if area[1:] == "11" or exchange[1:] == "11":
        raise InvalidNumber("N11 service codes are not phone numbers")
    if area in TOLL_FREE_AREA_CODES:
        raise InvalidNumber("toll-free numbers cannot receive SMS")
    if area in OTHER_NANP_AREA_CODES:
        raise InvalidNumber(f"area code {area} is outside the US and Canada")

    country = "CA" if area in CANADA_AREA_CODES else "US"
    if country not in allowed_countries:
        raise InvalidNumber(f"{country} numbers are not enabled")
    return NANPNumber(e164="+1" + digits, country=country)
