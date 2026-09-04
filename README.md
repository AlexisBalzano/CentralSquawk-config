# CentralSquawk config

Configuration and navdata for the CentralSquawk API. Pushing to `main` fires a
webhook that makes the server re-ingest this repo, so **a push here changes what
production is running**.

## Contents

| File | Regenerate | What it is |
| --- | --- | --- |
| `fix.txt` | every AIRAC | `<ident> <lat> <lon>` for every fix **inside** the Mode S area |
| `airway.txt` | every AIRAC | `AIRWAY <name>` blocks with their **complete** fix list |
| `procedure.txt` | every AIRAC | `<airport> <SID\|STAR> <designator>` for in-area airports |
| `modes_area.geojson` | rarely | The Mode S conspicuity area, as FIR/UIR rings |
| `ssr_pool.json` | per CAL release | French ORCAM code ranges and their destinations |
| `config.json` | by hand | Policy: AOR, code classes, timing, exclusions |
| `.github/workflows/` | — | Notifies the server after a push |

`fix.txt` is clipped to the Mode S area and `airway.txt` is not. That asymmetry
is load-bearing: membership in `fix.txt` *is* the containment test, so the server
never runs point-in-polygon at runtime, and an airway that leaves the area stays
detectable because it names fixes that `fix.txt` does not contain. Each file's
header explains its own rules — read those before changing a generator.


## Telling the server

`.github/workflows/notify-server.yml` fires on a push to `main` and POSTs to the
API's `/api/config-webhook`, which pulls this repository and swaps in a new
config snapshot. It can also be run by hand from the Actions tab.

Two repository secrets are required (Settings → Secrets and variables → Actions):

| Secret | Value |
| --- | --- |
| `CENTRALSQUAWK_API_URL` | Base URL of the API, e.g. `https://centralsquawk.vatsim.fr` |
| `CENTRALSQUAWK_WEBHOOK_SECRET` | Must equal the API's `GH_SECRET` |

The workflow signs its payload with `x-hub-signature-256`, the same scheme a
native GitHub webhook uses and the one the API verifies. Two details in it are
load-bearing:

- The HMAC is computed with `printf`, not `echo`. `echo` appends a newline, the
  digest would cover it, and the server would hash different bytes and reject
  every request with a 403 that looks like a wrong secret.
- `curl --data-raw` sends the payload verbatim, so what is signed is what is
  sent.

It only triggers on the six files the server actually parses. A change to
this README cannot alter what is served and does not reload
anything. Before notifying, it checks those files exist, that the JSON parses,
and that the three navdata files agree on one AIRAC cycle — the mistake that
otherwise silently changes eligibility.

A rejected config comes back as HTTP 422 and fails the job, with the server's
reason in the log. The running snapshot is left untouched.