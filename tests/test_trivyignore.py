""".trivyignore.yaml: every exception is time-limited and names its governance record."""
import datetime
import re
from pathlib import Path

IGNORE = Path(__file__).parent.parent / ".trivyignore.yaml"


def _entries():
    # One "- id:" block per exception; parsed without PyYAML, which CI does not install.
    text = IGNORE.read_text()
    return [b for b in re.split(r"\n  - (?=id:)", text)[1:]]


def test_every_exception_expires_within_a_year_and_cites_govr():
    entries = _entries()
    assert entries
    today = datetime.date.today()
    for block in entries:
        ident = re.match(r"id: (\S+)", block)[1]
        expiry = re.search(r"^\s+expired_at: (\d{4}-\d{2}-\d{2})\s*$", block, re.M)
        assert expiry, f"{ident} has no expired_at"
        days = (datetime.date.fromisoformat(expiry[1]) - today).days
        assert days <= 366, f"{ident} expires more than a year ahead"
        assert re.search(r"statement: .*GOVR-\d+", block), f"{ident} statement does not cite a GOVR record"
        assert re.search(r"^\s+- pkg:", block, re.M), f"{ident} is not limited to a package (purls)"
