# CentralSquawk config

Configuration and navdata for the CentralSquawk API. Pushing to `main` fires a
webhook that makes the server re-ingest this repo, so **a push here changes what
production is running**.

Everything except the Mode S area polygon is derived from a Navigraph DFD
database and has to be regenerated each AIRAC cycle (every 28 days).

## Contents

| File | Regenerate | What it is |
| --- | --- | --- |
| `fix.txt` | every AIRAC | `<ident> <lat> <lon>` for every fix **inside** the Mode S area |
| `airway.txt` | every AIRAC | `AIRWAY <name>` blocks with their **complete** fix list |
| `procedure.txt` | every AIRAC | `<airport> <SID\|STAR> <designator>` for in-area airports |
| `modes_area.geojson` | rarely | The Mode S conspicuity area, as FIR/UIR rings |
| `tools/` | — | The generators |
| `db.s3db` | — | Build input. **Gitignored** — see below |

`fix.txt` is clipped to the Mode S area and `airway.txt` is not. That asymmetry
is load-bearing: membership in `fix.txt` *is* the containment test, so the server
never runs point-in-polygon at runtime, and an airway that leaves the area stays
detectable because it names fixes that `fix.txt` does not contain. Each file's
header explains its own rules — read those before changing a generator.

## Refreshing for a new AIRAC

Requires Python 3.9+ and nothing else. Both generators are standard library only:
no venv, no `pip install`.

**1. Drop the new cycle's database in the repo root as `db.s3db`.**

It is the Navigraph DFD SQLite export. It is gitignored, so it stays local.

**2. Regenerate the three text files.**

```bash
py tools/generate_navdata.py
```

**3. Check the summary it prints against the previous cycle.**

A normal cycle moves these numbers by a fraction of a percent. Anything moving by
more than a few percent means either a genuine AIRAC restructure or a broken
input — find out which before you push.

```
AIRAC 2606
fix.txt     9,449 fixes inside the area (of 94,200 worldwide)
              NDB        189
              VOR/DME    1,157
              WAYPOINT   8,103
              915 duplicate idents inside the area, first kept
airway.txt  1,784 airways, 13,140 fixes listed
              3,892 of those are OUTSIDE the area (kept on purpose)
              91 designators span more than one block
procedure.txt 11,172 designators at 2,007 in-area airports
              SID 7,032   STAR 4,140
```

Two numbers are worth a second look every time:

- **fixes inside the area** collapsing toward zero means the polygon failed to
  load or no longer overlaps the data. The generator will still exit 0.
- **fixes OUTSIDE the area in `airway.txt`** climbing sharply means airway chain
  splitting has broken and unrelated airways sharing a designator have fused. If
  it does, check `MAX_LEG_NM` in `tools/generate_navdata.py`.

**4. Confirm the AIRAC line matches the cycle you intended to install.**

The generators read the cycle straight from the database and print it. If it says
the old cycle, you regenerated against the old file.

**5. Commit and push.**

```bash
git add fix.txt airway.txt procedure.txt && git commit -m "AIRAC 2607"
```

Pushing to `main` triggers the webhook and the server reloads. Push only when the
numbers in step 3 look right.

## Rebuilding the Mode S area

`modes_area.geojson` is **not** part of the AIRAC cycle. Rebuild it only when the
list of participating states changes, or when you want the FIR boundaries
themselves refreshed:

```bash
py tools/build_modes_area.py
```

It reconstructs the area as the union of the FIR/UIR extents of the participating
states, read from `tbl_uf_fir_uir` in the same database. The state list is the
`PARTICIPATING` constant at the top of the script — 22 ICAO region prefixes,
decoded from the airport regex that CCAMS uses in production. Edit that list to
change the area.

After rebuilding, spot-check it before regenerating anything: open the file in
<https://geojson.io> and confirm the coverage looks like the intended states.
Then re-run `generate_navdata.py`, because every text file is derived from it.

## Notes

**`db.s3db` is never committed.** It is ~160 MB, over GitHub's 100 MB per-file
limit, so a push carrying it fails outright. It is also Navigraph-licensed data,
and redistributing it through a repository is a licensing question you do not
want to answer by accident. Keep it local; commit only generated output.

**The generators are deterministic.** Same database in, same files out. If a
regeneration produces a diff you did not expect, the input changed.

**Order matters.** `modes_area.geojson` feeds all three text files. Rebuilding
the polygon without re-running `generate_navdata.py` leaves the repo internally
inconsistent, and the server will happily ingest it.
