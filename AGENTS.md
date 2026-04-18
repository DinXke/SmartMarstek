# SmartMarstek — Release Protocol

Every code change in this repo that ships a user-visible fix or feature **must** follow this
protocol before the tag is cut. Home Assistant reads the add-on version from `config.yaml`, not
from the git tag. A tag without a matching version bump is a dead release — HA will not offer an
update button.

## Steps (mandatory, in order)

1. **Bump `config.yaml`** — change `version: "X.Y.Z"` to the new version string.

2. **Add CHANGELOG entry** — insert a `## [X.Y.Z] - YYYY-MM-DD` section at the **top** of
   `CHANGELOG.md` (above the previous release). Group changes under `### Added`, `### Fixed`,
   `### Accessibility`, etc. Include Paperclip issue links like `[SCH-XX](/SCH/issues/SCH-XX)`.

3. **Commit** both files together:
   ```
   git add config.yaml CHANGELOG.md
   git commit -m "Release vX.Y.Z: <short summary> (<issue-ids>)"
   ```
   Append `Co-Authored-By: Paperclip <noreply@paperclip.ing>` to the commit message.

4. **Tag and push**:
   ```
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```
   The `build.yml` workflow triggers on `push: tags: v*` and builds/pushes
   `ghcr.io/dinxke/smartmarstek/{amd64,aarch64}:X.Y.Z` and `:latest` to GHCR.

5. **Verify CI** — check that the GitHub Actions run finished green:
   ```
   GET /repos/DinXke/SmartMarstek/actions/runs?per_page=1
   ```
   Do not close the Paperclip task until the run conclusion is `success`.

6. **Close the Paperclip task** — comment with: new tag, Actions run ID + conclusion, and
   confirmation that HA will now detect the update.

## Why this matters

- HA add-on store reads `version` from `config.yaml`, **not** from the git tag.
- A git tag without a `config.yaml` bump leaves HA believing the installed version is already
  current — no update button appears for the user.
- The CHANGELOG keeps human-readable history and is used in Paperclip task comments.
