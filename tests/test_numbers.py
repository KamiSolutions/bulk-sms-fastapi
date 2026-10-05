import pytest

from bulksms import InvalidNumber, normalize_nanp


@pytest.mark.parametrize(
    "raw, e164, country",
    [
        ("(416) 555-0123", "+14165550123", "CA"),
        ("1-604-555-0199", "+16045550199", "CA"),
        ("+1 212 555 0147", "+12125550147", "US"),
        ("212.555.0147", "+12125550147", "US"),
        ("2125550147", "+12125550147", "US"),
        ("011 1 212 555 0147", "+12125550147", "US"),
        ("212-555-0147 ext. 22", "+12125550147", "US"),
        ("907 555 0100", "+19075550100", "US"),  # Alaska
    ],
)
def test_valid(raw, e164, country):
    n = normalize_nanp(raw)
    assert (n.e164, n.country) == (e164, country)


@pytest.mark.parametrize(
    "raw",
    ["", "555-0123", "+44 20 7946 0958", "876-555-0100", "787-555-0100", "800-555-0100",
     "112-555-0100", "212-055-0100", "411-555-0100", "212-555-01234", "abc"],
)
def test_invalid(raw):
    with pytest.raises(InvalidNumber):
        normalize_nanp(raw)


def test_country_filter():
    with pytest.raises(InvalidNumber):
        normalize_nanp("416 555 0123", allowed_countries=frozenset({"US"}))
