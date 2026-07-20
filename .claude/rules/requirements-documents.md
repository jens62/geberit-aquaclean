# Requirements Definition Documents (MANDATORY)

Read `docs/developer/requirements-document-standard.md` in full before creating a new
requirements definition document, or editing an existing one's requirements/issues.

Existing requirements documents, each with its own ID prefix (never reuse another document's
prefix): `docs/developer/mock-service-requirements.md` (`REQ-NNN`),
`docs/developer/ble-relay-rest-api-requirements.md` (`RAPI-NNN`),
`docs/developer/application-layer-ble-relay-requirements.md` (`RELAY-NNN`).

## Non-negotiable rules (full detail in the standard doc)

1. Every requirement and every issue gets a unique, stable ID (`<PREFIX>-NNN` /
   `<PREFIX>-ISS-NNN`) — never reused, never renumbered, even if later marked `Superseded`.
2. A requirement's `Statement` is phrased in the **declarative mood, present indicative** —
   "The program responds within three seconds," not "should/shall/must respond." States a
   fact (actual or intended), not an obligation.
3. Every requirement has `Type` (`Functional` | `Technical`), `Status` (`Open | In Progress |
   Done | Deferred | Superseded`), and `Implementation Details` **only when** `Status` is not
   `Open`.
4. Every requirements document has one `## Issues` section for implementation-time problems
   that don't map to a single requirement.
5. Each requirement/issue gets its own `###` section with `Type`/`Statement`/`Status`/
   `Implementation Details` as ordered `####` subsections (requirements) or
   `Statement`/`Status`/`Details` (issues, no `Type`).

## When this applies

- Adding, editing, or moving a requirement/issue in any of the three documents above.
- Creating a new requirements definition document for a new component/feature area — give it
  its own file under `docs/developer/`, its own unique ID prefix, and add it to both the
  standard doc's example list and this rule's list above.
- Restating content from `docs/roadmap.md` or another narrative doc as formal requirements —
  the narrative doc stays as the "canonical narrative" source; the requirements document
  restates it structurally and is what gets updated going forward.

**Common mistake, made once already (2026-07-20):** adding new requirements inline into an
existing document (e.g. `mock-service-requirements.md`) when the actual scope is a distinct
component that deserves its own standalone document. If in doubt whether new content belongs
in an existing requirements doc or a new one, ask before writing — moving content after the
fact means renumbering IDs, which Rule 1 above exists specifically to avoid.
