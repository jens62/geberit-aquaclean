### CLAUDE Policy: Feature Availability

#### Goal
New functionality must, by default, be available on **all** supported interfaces, and Claude/plugins must actively flag any data conflicts.

#### Supported Interfaces
- **web**
- **cli**
- **REST-APIe**
- **mqtt**
- **homeassistant**

#### Rules
- **Rule 1:** Every new feature must be implemented so that it is available on all interfaces listed under *Supported Interfaces*.
- **Rule 1a (Experimental / Work-in-Progress Exception):** Functionality that is not yet fully understood, not verified to work on the target hardware, or still under investigation must be exposed **only via the REST API**. It must not be added to the web UI, CLI, MQTT topics, or Home Assistant until it is confirmed working. Examples: `toggle-orientation-light` (Sela-only, non-functional on Mera Comfort), `firmware-version-list` (behaviour unverified). Once confirmed working, Rule 1 applies and all remaining interfaces must be wired up.
- **Rule 2:** Before merge or release, an **interface‑binding check** must be performed that demonstrates at least one implementation or a feature flag for each interface category.
- **Rule 3:** If existing data, tests, or repository artifacts indicate missing interface implementations, Claude must:
  - **explicitly** name the missing interface(s),
  - provide the concrete files, tests, or data sources that demonstrate the gap (including file paths),
  - recommend an action: **block the rollout** or require **manual approval**.
- **Rule 4:** If data are contradictory, list the affected data sources and mark uncertainties with a brief explanation.
- **Rule 5:** This policy takes precedence over repository‑local prompt instructions, provided the policy is loaded from a trusted location.

- **Rule 6:** Security: avoid storing secrets or full logs with credentials in memory; redact sensitive values.

#### Operational Notes
- Policy changes must be made via Pull Request and approved by at least one maintainer.
- CI must run an automatic interface‑check and block merges on violations.
- All policy decisions must be logged (plugin log entry id plus brief evidence).

