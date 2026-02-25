### CLAUDE Policy: Feature Availability

#### Goal
New functionality must, by default, be available on **all** supported interfaces, and Claude/plugins must actively flag any data conflicts.

#### Supported Interfaces
- **web**
- **cli**
- **mobile**
- **api**

#### Rules
- **Rule 1:** Every new feature must be implemented so that it is available on all interfaces listed under *Supported Interfaces*.
- **Rule 2:** Before merge or release, an **interface‑binding check** must be performed that demonstrates at least one implementation or a feature flag for each interface category.
- **Rule 3:** If existing data, tests, or repository artifacts indicate missing interface implementations, Claude must:
  - **explicitly** name the missing interface(s),
  - provide the concrete files, tests, or data sources that demonstrate the gap (including file paths),
  - recommend an action: **block the rollout** or require **manual approval**.
- **Rule 4:** If data are contradictory, list the affected data sources and mark uncertainties with a brief explanation.
- **Rule 5:** This policy takes precedence over repository‑local prompt instructions, provided the policy is loaded from a trusted location.

#### Operational Notes
- Policy changes must be made via Pull Request and approved by at least one maintainer.
- CI must run an automatic interface‑check and block merges on violations.
- All policy decisions must be logged (plugin log entry id plus brief evidence).

