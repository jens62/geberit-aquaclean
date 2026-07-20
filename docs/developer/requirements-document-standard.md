# Requirements Definition Document — Standard

This document defines the structure and phrasing rules that every requirements definition
document in this repository follows. It is a meta-document — it does not itself specify any
system's behavior; it specifies how documents that do so must be written. Existing documents
already following most of this convention: `docs/developer/mock-service-requirements.md`
(`REQ-NNN`), `docs/developer/ble-relay-rest-api-requirements.md` (`RAPI-NNN`),
`docs/developer/application-layer-ble-relay-requirements.md` (`RELAY-NNN`).

**Why this exists:** requirements scattered across prose, commit messages, and chat history
can't be referenced unambiguously, can't be told apart from implementation-history narrative,
and drift silently out of sync with what was actually built. A fixed structure fixes all three:
every requirement and every issue gets a stable ID, every requirement's `Statement` says what
*is* true (or is intended to become true) rather than what someone thinks *should* happen, and
status/implementation-history lives in exactly one place per requirement instead of being
re-derived from memory each time someone asks "did we ever fix that?".

---

## Rule 1 — Unique requirement IDs

Every detailed requirement has a stable, unique ID of the form `<PREFIX>-NNN` (zero-padded,
e.g. `REQ-001`), assigned once and never reused or renumbered — not even if the requirement is
later marked `Superseded` or `Deferred`. IDs are the only thing anyone should ever need to
unambiguously reference a requirement, in a commit message, another document, or conversation.

**Choose a document-specific prefix.** A bare ID must never be ambiguous about which document
it belongs to, even quoted out of context. `mock-service-requirements.md` uses `REQ-`;
`ble-relay-rest-api-requirements.md` uses `RAPI-` specifically *because* it's a different
document from the first — reusing `REQ-` there would make `REQ-014` ambiguous between the two.
Pick a short, document-specific prefix before writing requirement 001.

## Rule 2 — Phrasing: the declarative mood, present indicative

A requirement's `Statement` is written as **the Declarative Mood, Present Indicative** — the
grammatical mood used to state facts and objective circumstances, not intentions, obligations,
or possibilities.

> "The program responds within three seconds."

Not:

> ~~"The program should respond within three seconds."~~
> ~~"The program shall respond within three seconds."~~
> ~~"The program must respond within three seconds."~~

**Why:** `should`/`shall`/`must` are the subjunctive/modal register — they describe a *desired*
state, leaving open whether it currently holds. The indicative describes the state itself, as
if it were already an indisputable fact of reality. This forces precision: you cannot write an
indicative sentence about a behavior you haven't actually pinned down, and it reads identically
whether the requirement is aspirational or already built — the `Status` field (Rule 3) is
where "is this actually true yet" lives, not the wording of the `Statement`.

This applies to the `Statement` field specifically. It does not require rewriting this
standard document itself in the same mood — a style guide instructing *how to write* something
is not itself describing a system's behavior.

## Rule 3 — Type, Status, and Implementation Details on every requirement

Every requirement carries:

- **`Type`** — `Functional` or `Technical`. A functional requirement describes user- or
  external-system-observable behavior ("A setting changed via the app survives a mock
  restart"). A technical requirement describes an internal design/implementation constraint
  with no directly observable behavioral difference on its own ("`mock_service.py` contains no
  protocol logic; one script, N devices"). When a requirement reads as both, split it — a
  technical requirement should be extractable as the *mechanism* behind one or more functional
  requirements' `Statement`, not a restatement of the same fact from a different angle.
- **`Status`** — one of `Open | In Progress | Done | Deferred | Superseded`. `Superseded`
  requirements name what superseded them (e.g. "Superseded (by REQ-012)") and are never
  deleted — the ID and its history stay, so anything that referenced it in the past still
  resolves to something.
- **`Implementation Details`** — present **whenever `Status` is not `Open`**, and absent when
  it is. This is where the actual history lives: root causes, decisions made and why, bugs
  found and fixed with dates/commits/exact bytes, verification evidence, dead ends ruled out.
  An `Open` requirement has nothing here yet by definition — there's no implementation to
  detail.

## Rule 4 — A dedicated Issues section

Every requirements document has one `## Issues` section, separate from the requirements
themselves. Requirements describe *what is or should be true*; issues describe *problems
encountered while getting there* — a bug found during implementation, a design tension between
two requirements, an external blocker, an open question that doesn't map cleanly onto a single
requirement's `Implementation Details`. If an issue is squarely about implementing one specific
requirement, prefer recording it in that requirement's `Implementation Details` instead —
the `Issues` section is for things that don't have one obvious home, or that are referenced
from multiple requirements and would otherwise be duplicated.

## Rule 5 — Unique issue IDs

Every issue gets a stable, unique ID, on the same terms as Rule 1 — never reused or renumbered.
Use a prefix that is unambiguous against this document's own requirement-ID prefix and against
every other document's issue IDs: `<REQ-PREFIX>-ISS-NNN` (e.g. `REQ-ISS-001` in
`mock-service-requirements.md`, `RAPI-ISS-001` in `ble-relay-rest-api-requirements.md`).

## Rule 6 — One section per requirement

Each requirement gets its own subsection (`### <ID> — <short title>`), with exactly these four
parts, in this order, each its own `####` heading:

```markdown
### REQ-042 — Short, specific title

#### Type

Functional

#### Statement

The mock's webui shows every setting the device tracks, not a curated subset.

#### Status

Open

#### Implementation Details

(omitted — Status is Open)
```

A top-of-document `## Requirements Index` table (`ID | Type | Status | Summary`) gives a
scannable overview; it is a convenience, not a substitute for the full per-requirement
sections — every row links to (or is immediately followed by, further down the document) the
full section with that ID.

Issues follow the identical shape, minus `Type`:

```markdown
### REQ-ISS-007 — Short, specific title

#### Statement

`_handle_button()`'s auto-release only fires once a BLE connection completes the A6 burst —
holding the button with nothing connected leaves it pressed indefinitely.

#### Status

Confirmed, not a defect

#### Details

...
```

---

## Applying this standard

New requirements documents follow this standard from their first commit. Existing documents
that predate a given rule are brought into compliance opportunistically (e.g. when Rule 4/5's
`Issues` section is added to this repo's existing requirements documents, add it there rather
than starting a new document) rather than requiring a disruptive one-time rewrite.
