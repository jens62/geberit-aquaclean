# Release Process (MANDATORY)

## Release checklist

**Do not tag and create a release until all of the following docs are up to date:**

| File | What to check |
|------|--------------|
| `README.md` | Install steps, curl commands, feature list, documentation table |
| `docs/configuration.md` | New config keys documented in table and example block |
| `docs/cli.md` | New CLI flags or commands documented |
| `docs/home-assistant.md` | HA-facing changes reflected |
| `docs/hacs-integration.md` | HACS integration changes, version-specific notes |
| `homeassistant/SETUP_GUIDE.md` | Install steps, discovery, upgrading section |

Only bump `pyproject.toml` and run `gh release create` once all affected docs are updated
in the same commit (or in a preceding commit on the same push).

---

## Git tag vs GitHub Release — HACS will NOT see a bare git tag

**Pushing a git tag is not enough.** HACS exclusively reads GitHub Releases.
A bare `git push --tags` leaves the version invisible to HACS users.

**Mandatory release sequence:**
```bash
# 1. Bump versions in pyproject.toml + manifest.json, commit, push
git tag vX.Y.Z
git push origin main --tags

# 2. Create the GitHub Release — this is what HACS actually reads
gh release create vX.Y.Z --title "vX.Y.Z" --notes "- change 1\n- change 2"
```

Confirmed root cause (2026-02-24): v2.4.15 and v2.4.16 were pushed as git tags only;
neither appeared in HACS until `gh release create` was run for both.

---

## Pre-release before stable (MANDATORY)

Tag `vX.Y.Zb1` (pre-release) first; stable `vX.Y.Z` only after MuusLee confirms
on real hardware.

---

## Before every new release tag — check standalone install compatibility

1. `gh api repos/jens62/geberit-aquaclean/releases/latest --jq '.tag_name,.prerelease'`
   → must be non-prerelease (`false`) and point to the intended tag
2. The tag must include the correct `pyproject.toml` version — `aquaclean-bridge --version`
   must match the tag name
3. `custom_components/` is ignored by pip (`pyproject.toml` only includes
   `aquaclean_console_app*`) — safe to have on main
4. Pre-release tags (e.g. `v2.4.13-hacs-beta`) are excluded from `releases/latest` automatically

**Common mistake:** tagging before bumping `pyproject.toml` — the tag then reports the
old version. Always bump `pyproject.toml` (and `manifest.json`) BEFORE tagging.

---

## After every commit — always output update.sh curl (MANDATORY)

Use `update.sh`, not `install.sh` — user is on raspi-5 with an existing install:

```bash
curl -fsSL https://raw.githubusercontent.com/jens62/geberit-aquaclean/<SHA>/operation_support/update.sh | bash -s -- <SHA>
```

Get the full SHA with `git rev-parse HEAD` after committing.

---

## After every push to tools/ — always output curl download one-liner (MANDATORY)

```bash
curl -fsSL https://raw.githubusercontent.com/jens62/geberit-aquaclean/<FULL_SHA>/tools/<script>.py -o tools/<script>.py
```

Output this unprompted.
