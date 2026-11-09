# Compatibility fixtures

Golden files written by released litresearch versions. The current app is tested
to still open, read and upgrade them (see `tests/test_compat.py`).

Each folder `<version>/` was produced by `tools/make_compat_fixtures.py`: the
script checks out the git tag, installs it into a fresh virtual environment and
builds a small profile offline with **that version's own code** (BibTeX import of
`tests/data/paperpile_sample.bib`, readings at several depths with XP and badges,
ratings, notes, queue, one recommendation batch; from 0.2 a second library).
Network access is blocked while building.

| File | Content |
| --- | --- |
| `litresearch.sqlite` | SQLite snapshot of the profile database (`backup.snapshot`) |
| `settings.yaml` | the profile's settings file |
| `progress.json` | progress export (`backup.export_progress`) |
| `profile.json` | saved profile file (`backup.save_profile_file`) |
| `expected.json` | counts computed by that version (papers, readings, XP, ...) |
| `export.zip` | "export everything" archive (`archive.export_archive`, from 0.6 on) |

`dev-<YYYYMMDD>/` folders hold what the development branch wrote at that date
(e.g. the first data format version before its release); they were made with
`--source . --name dev-<YYYYMMDD>`.

**Never regenerate the fixtures of a released version**: they are the proof of
what that version wrote. Only a new version gets a new folder:

    tools/make_compat_fixtures.py --tags v0.3.0

Every release adds its fixtures (see CONTRIBUTING.md).
