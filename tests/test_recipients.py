from pathlib import Path

import pytest

from bulksms import load_recipients, load_suppression_list
from bulksms.client import estimate_parts

SAMPLE = (Path(__file__).resolve().parents[1] / "sample_recipients.csv").read_bytes()


def test_sample_csv():
    rep = load_recipients(SAMPLE, template="Hi {first_name}, see you {date}.")
    assert [r.phone for r in rep.recipients] == ["+14165550123", "+12125550147", "+16045550199"]
    assert rep.duplicates == 1
    assert {r.reason for r in rep.rejected} == {"area code 876 is outside the US and Canada", "toll-free numbers cannot receive SMS"}
    assert rep.recipients[0].body == "Hi Ava, see you Tuesday."
    assert rep.summary()["by_country"] == {"CA": 2, "US": 1}


def test_bom_semicolons_message_column_and_footer():
    data = "﻿Mobile;Message\n4165550123;Hello there\n".encode()
    rep = load_recipients(data, footer="Reply STOP to opt out")
    assert rep.recipients[0].body == "Hello there Reply STOP to opt out"


def test_suppression():
    sup = load_suppression_list(b"phone\n+1 (416) 555-0123\n")
    rep = load_recipients(SAMPLE, template="x", suppressed=sup)
    assert "+14165550123" not in [r.phone for r in rep.recipients]
    assert any(r.reason == "opted out" for r in rep.rejected)


def test_missing_phone_column():
    with pytest.raises(ValueError):
        load_recipients(b"name,email\na,b\n", template="x")


def test_unknown_placeholder_is_blank():
    rep = load_recipients(b"phone\n2125550147\n", template="Hi {first_name}!")
    assert rep.recipients[0].body == "Hi !"


def test_estimate_parts():
    assert estimate_parts("a" * 160) == ("GSM7", 1)
    assert estimate_parts("a" * 161) == ("GSM7", 2)
    assert estimate_parts("Café 😀") == ("UNICODE", 1)
    assert estimate_parts("é" * 70)[0] == "GSM7"  # é is in the GSM alphabet
    assert estimate_parts("ç" * 71) == ("UNICODE", 2)
