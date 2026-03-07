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

- **Rule 7 — Interface Parity (UX similarity):** When implementing a feature on a new interface (e.g. HACS integration), the information shown and the user experience must be as similar as possible to the existing reference interface (the standalone webapp). Specifically:
  - The same connection panels must exist: ESPHome proxy status (badge + connection string) and Geberit BLE status (badge + device name + address).
  - The same status states must be represented: connecting / connected / disconnected / error.
  - The same fields must be shown: device name, address/host:port, error hint.
  - Missing parity items must be explicitly tracked in the TODO list and in `memory/hacs-connection-status.md`.
  - Acceptable exceptions: transient states (e.g. `connecting` mid-poll) that are architecturally unavailable on the target interface. Document why they cannot be shown.

- **Rule 8 — Check Memory Before Coding:** Before writing any new code, Claude must read `MEMORY.md` and relevant memory topic files to determine whether the feature already exists on some interfaces. Assumptions about what is and is not implemented must be verified against memory, not guessed. Specifically: before planning or implementing a feature, state explicitly which interfaces already have it and which are missing — based on memory, not inference.

- **Rule 9 — Pre-release version suffix:** Pre-release versions must always use the `-pre` suffix in **both** `pyproject.toml` and `custom_components/geberit_aquaclean/manifest.json` (e.g. `2.4.36-pre`). Only stable releases use a plain version number (e.g. `2.4.36`). The GitHub Release must be created with `--prerelease` for `-pre` versions and without it for stable versions. Never bump to a plain version number for a pre-release.

#### Operational Notes
- Policy changes must be made via Pull Request and approved by at least one maintainer.
- CI must run an automatic interface‑check and block merges on violations.
- All policy decisions must be logged (plugin log entry id plus brief evidence).

---

### CLAUDE Policy: Plan Before Code

Before writing or modifying any code, Claude must always:
1. State what it found (relevant files, current behaviour, root cause).
2. Describe the change it plans to make and why.
3. Wait for explicit user approval before touching any file.

This applies to all code changes — bug fixes, refactors, new features, and one-liners alike.

