"""Generate ssr_pool.json from the EUROCONTROL ICAO EUR Code Allocation List.

The CAL is the authoritative ORCAM allocation document. It is distributed as a
legacy .xls workbook whose 'CAL' sheet has one row per allocated code range:

    Series | From | To | PA | Unit | CIV/MIL | APP | ... | Destination | ...

Series, From and To are OCTAL DIGIT PAIRS, not numbers: Series 04, From 01,
To 77 means codes 0401 through 0477. They are read as text and never converted
arithmetically, because Excel stores some of them as numbers (4.0, 1.0, 77.0)
and the digits are what matter.

WHAT IS KEPT

  * Rows allocated EXCLUSIVELY to France: every Unit token is 'LF' or a
    four-letter French unit such as 'LFMM'. Rows sharing a range across the
    EUR-B area ('EB ED EDYY EG EH EI LF LS') are DROPPED -- those are held
    jointly and issuing from them unilaterally is how you collide with Belgium.
  * Civil allocations, meaning CIV and CIV_MIL. Pure MIL rows are dropped.
  * Both ENR and APP allocations.

DESTINATIONS

ORCAM allocates by destination, so each range carries the destinations it may
be issued for. Tokens are ICAO prefixes of one, two or four characters and are
matched against the arrival aerodrome. 'ALL' becomes ["*"], meaning any.

Usage:  py tools/build_pool.py --cal path/to/eurocontrol-icao-eur-cal-v4-13.xls
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

try:
    import xlrd
except ImportError:  # pragma: no cover
    raise SystemExit("xlrd is required to read the .xls CAL: py -m pip install xlrd")

# Column indices in the 'CAL' sheet.
COL_SERIES, COL_FROM, COL_TO = 0, 1, 2
COL_UNIT, COL_CIVMIL, COL_APP = 4, 5, 6
COL_DESTINATION = 9

OCTAL_PAIR = re.compile(r"^[0-7]{2}$")


def cell(sheet, row: int, col: int) -> str:
    """A cell as text, with Excel's numeric coercion undone.

    A Series of 4.0 is the digit pair '04'; treating it as the number four and
    formatting it later would be the same thing, but only by accident. Padding
    here keeps the octal-digits-as-text invariant explicit.
    """
    value = sheet.cell_value(row, col)
    if isinstance(value, float):
        return f"{int(value):02d}"
    return str(value).strip()


def is_french_exclusive(units: list[str]) -> bool:
    """True when every unit on the row is France, and none is anybody else."""
    if not units:
        return False
    return all(u == "LF" or (u.startswith("LF") and len(u) == 4) for u in units)


def parse_destinations(raw: str) -> list[str]:
    tokens = raw.split()
    if not tokens or any(t.upper() == "ALL" for t in tokens):
        return ["*"]
    return sorted({t.upper() for t in tokens})


def build(cal_path: Path) -> tuple[list[dict], dict]:
    book = xlrd.open_workbook(str(cal_path))
    sheet = book.sheet_by_name("CAL")

    ranges: list[dict] = []
    stats = Counter()
    versions = Counter()

    for row in range(1, sheet.nrows):
        units = cell(sheet, row, COL_UNIT).split()
        if not is_french_exclusive(units):
            stats["dropped_shared_or_foreign"] += 1
            continue

        civmil = cell(sheet, row, COL_CIVMIL).upper()
        if "CIV" not in civmil:
            stats["dropped_military"] += 1
            continue

        series = cell(sheet, row, COL_SERIES)
        first = cell(sheet, row, COL_FROM)
        last = cell(sheet, row, COL_TO)
        if not (OCTAL_PAIR.match(series) and OCTAL_PAIR.match(first) and OCTAL_PAIR.match(last)):
            stats["dropped_malformed"] += 1
            continue

        low, high = series + first, series + last
        if int(low, 8) > int(high, 8):
            stats["dropped_inverted"] += 1
            continue

        ranges.append({
            "from": low,
            "to": high,
            "destinations": parse_destinations(cell(sheet, row, COL_DESTINATION)),
            "unit": " ".join(units),
            "use": cell(sheet, row, COL_APP) or "ENR",
        })
        stats["kept"] += 1
        versions[cell(sheet, row, 13)] += 1

    ranges.sort(key=lambda r: int(r["from"], 8))
    return ranges, {"counts": stats, "versions": versions}


def report(ranges: list[dict]) -> None:
    total = sum(int(r["to"], 8) - int(r["from"], 8) + 1 for r in ranges)
    print(f"  {len(ranges)} ranges, {total} codes")

    # A code appearing in two ranges can only belong to one of them, so the
    # second range silently loses it. Surface that rather than let it hide.
    seen: dict[int, dict] = {}
    overlaps = []
    for r in ranges:
        for code in range(int(r["from"], 8), int(r["to"], 8) + 1):
            if code in seen:
                overlaps.append((f"{code:04o}", seen[code], r))
            else:
                seen[code] = r
    if overlaps:
        pairs = {(o[1]["from"] + "-" + o[1]["to"], o[2]["from"] + "-" + o[2]["to"]) for o in overlaps}
        print(f"  {len(overlaps)} codes appear in more than one range:")
        for a, b in sorted(pairs):
            print(f"    {a} overlaps {b}")

    # Four-character destination tokens are FIR identifiers (LFMM, EDGG), not
    # aerodrome prefixes, so nothing will ever match them. Those ranges are
    # effectively unusable until aerodrome-to-FIR resolution exists.
    unusable = [r for r in ranges
                if all(len(d) == 4 and d != "*" for d in r["destinations"])]
    if unusable:
        codes = sum(int(r["to"], 8) - int(r["from"], 8) + 1 for r in unusable)
        print(f"  {len(unusable)} ranges ({codes} codes) are destined to FIR identifiers")
        print("    rather than aerodrome prefixes, so they can never be matched:")
        for r in unusable:
            print(f"    {r['from']}-{r['to']} -> {' '.join(r['destinations'])}")

    wildcard = sum(int(r["to"], 8) - int(r["from"], 8) + 1
                   for r in ranges if r["destinations"] == ["*"])
    print(f"  {wildcard} codes are issuable to any destination")


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cal", type=Path, required=True, help="the CAL .xls workbook")
    ap.add_argument("--out", type=Path, default=here / "ssr_pool.json")
    args = ap.parse_args()

    ranges, info = build(args.cal)
    if not ranges:
        print("no French allocations found -- is this the right workbook?")
        return 1

    version = max(info["versions"], key=lambda v: info["versions"][v]) if info["versions"] else "?"
    document = {
        "_comment": (
            "GENERATED by tools/build_pool.py from the EUROCONTROL ICAO EUR Code "
            "Allocation List. Do not edit by hand: re-run the generator against a "
            "newer CAL instead. Only ranges allocated exclusively to France are "
            "included; ranges shared across the EUR-B area are held jointly and "
            "cannot be issued unilaterally."
        ),
        "source": f"EUROCONTROL ICAO EUR CAL ({args.cal.name})",
        "ranges": ranges,
    }
    args.out.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")

    print(f"CAL rows: {info['counts']['kept']} kept, "
          f"{info['counts']['dropped_shared_or_foreign']} shared/foreign, "
          f"{info['counts']['dropped_military']} military")
    report(ranges)
    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
