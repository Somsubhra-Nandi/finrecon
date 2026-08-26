# validator.v3 — structural evidence hardening

## Why v2 was insufficient

`validator.v2` safely closed reference evidence over the complete immutable
candidate snapshot, but a stale reference can still be mechanically valid as a
string relation. Four v4-pilot cases carried exactly that failure: the reference
identified a prior-day settlement while an explicit value-date field supported
different candidates. Reference evidence alone cannot express the conflict, so
v2 resolved all four incorrectly.

## Rules selected after benchmark-only comparison

The production rule was prototyped as four offline arms over all 64 pilot cases:

| Arm | Resolved | Correct | Wrong | Escalated | Value at risk |
|---|---:|---:|---:|---:|---:|
| S0 reference only | 38 | 34 | 4 | 26 | 13,445,282 paise |
| S1 reference + value date | 38 | 38 | 0 | 26 | 0 |
| S2 reference + breakup amount | 48 | 44 | 4 | 16 | 13,445,282 paise |
| S3 both structural relations | 48 | 48 | 0 | 16 | 0 |

Only S3 covers both gaps while meeting the zero-wrong hard gate.

## Declared structural semantics

Value-date evidence is admitted only from an explicit `VALDT DDMONYY` source
field. The date must parse and equal the immutable normalized bank `value_date`;
candidate consistency then means that exact calendar date occurs among the
candidate's settlement dates. This is deliberately distinct from Stage 2's
declared ±1-day candidate-generation window: being in the window makes a
settlement plausible, not identified. A mismatch between the narration field
and normalized bank date is contradiction, never a guessed comparison.

Breakup-amount evidence is admitted only from an explicit `RFND rupees.paise`
field. Parsing is decimal text to integer paise; no float exists. Candidate
consistency requires an exact signed `refund` breakup line equal to the negative
of that amount. Other line types and approximate values do not count.

Every recognized token is evaluated against every candidate and every relevant
line. Repeated identical fields collapse to one fact with all source offsets;
multiple distinct facts conjunct. Empty, multi-candidate, and contradictory
intersections escalate. Structural evidence cannot seed itself: an admissible
reference investigation remains required, so date alone or amount alone never
causes a match.

## Provenance and authority

The result records raw narration spans and offsets, parsed bank dates or integer
paise, relation IDs, candidate settlement dates, matching settlement and breakup
line indexes, signed amounts, reference IDs and statuses, reach sets, and the
final combined intersection. The model's prose, selected candidate IDs, and
selected breakup lines are not decision inputs. Production reads no ground
truth.

## Safety invariants and limitations

The full Stage-2 snapshot remains the candidate axis. Reference, date, and
amount evidence are closed sets; order, duplication, and inspection choices
cannot change them. Adding a trusted fact can only shrink the intersection.
The policy remains `policy.v1` and retains its blocker vocabulary.

The rules intentionally do not recognize unlabeled dates or generic decimal
amounts. They do not apply fuzzy tolerances, infer arbitrary line types, or
claim that every bank's narration vocabulary uses `VALDT` and `RFND`. Such
cases escalate until a separately declared, tested source convention exists.

## Result

The v4 pilot is 48 correct resolutions, 0 wrong, and 16 correct escalations.
The 34 reference-only, 4 reference+date, and 10 reference+amount resolutions
are reported separately; four structural contradictions are reported as
escalations. Unsafe auto-match rate and value at risk are zero.

The frozen v3 benchmark remains 171 correct, 0 wrong, and 0 T3 resolutions.
Its SHA-256 remains
`f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b`.
