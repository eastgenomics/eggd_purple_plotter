""".trivyignore.yaml: every exception is time-limited, scoped to a package and names its
governance record.

Parsed without PyYAML (CI does not install it), so the file must use the block style below;
anything else, such as flow-style entries, fails the test rather than being skipped."""
import datetime
import re
from pathlib import Path

import pytest

IGNORE = Path(__file__).parent.parent / ".trivyignore.yaml"
ENTRY = re.compile(
    r"  - id: (?P<id>\S+)\n"
    r"    purls:\n(?P<purls>(?:      - pkg:\S+\n)+)"
    r"    statement: \"(?P<statement>[^\"\n]*GOVR-\d+[^\"\n]*)\"\n"
    r"    expired_at: (?P<expiry>\d{4}-\d{2}-\d{2})\n"
)


def check(text: str, today: datetime.date) -> list[str]:
    """IDs of the exceptions in text; raises AssertionError if any line is not part of a valid entry."""
    body = "".join(line for line in text.splitlines(keepends=True)
                   if line.strip() and not line.lstrip().startswith("#"))
    assert body.startswith("vulnerabilities:\n"), "only a vulnerabilities list is expected"
    rest, ids = body[len("vulnerabilities:\n"):], []
    while rest:
        m = ENTRY.match(rest)
        assert m, f"not a complete exception entry: {rest.splitlines()[0]!r}"
        days = (datetime.date.fromisoformat(m["expiry"]) - today).days
        assert days <= 366, f"{m['id']} expires more than a year ahead"
        ids.append(m["id"])
        rest = rest[m.end():]
    assert ids, "no exceptions listed"
    return ids


def test_repo_ignore_file_is_valid():
    assert check(IGNORE.read_text(), datetime.date.today())


GOOD = ("vulnerabilities:\n  - id: GHSA-x\n    purls:\n      - pkg:npm/a@1\n"
        "    statement: \"see GOVR-1\"\n    expired_at: 2027-01-01\n")


@pytest.mark.parametrize("bad", [
    GOOD + "  - {id: GHSA-y}\n",                                   # flow style
    GOOD.replace("    purls:\n      - pkg:npm/a@1\n", ""),         # not scoped to a package
    GOOD.replace("see GOVR-1", "accepted"),                        # no governance record
    GOOD.replace("    expired_at: 2027-01-01\n", ""),               # no expiry
    GOOD.replace("2027-01-01", "2030-01-01"),                      # too far ahead
    GOOD + "misconfigurations:\n  - id: AVD-X\n",                  # other sections not reviewed here
])
def test_invalid_files_are_rejected(bad):
    with pytest.raises(AssertionError):
        check(bad, datetime.date(2026, 11, 4))


def test_good_file_passes():
    assert check(GOOD, datetime.date(2026, 11, 4)) == ["GHSA-x"]
