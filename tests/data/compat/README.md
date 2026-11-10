# Compatibility fixtures

Golden files written by released litrad versions, from 1.0.0 on. The current app
is tested to still open, read and upgrade them (see `tests/test_compat.py`).

Each folder `<version>/` was produced by `tools/make_compat_fixtures.py`: the
script checks out the git tag, installs it into a fresh virtual environment and
builds a small profile offline with **that version's own code** (BibTeX import of
`tests/data/paperpile_sample.bib`, a second library, readings at several depths
with XP and badges, ratings, notes, queue, one recommendation batch, an example
choice). Network access is blocked while building.

| File | Content |
| --- | --- |
| `litrad.sqlite` | SQLite snapshot of the profile database (`backup.snapshot`) |
| `settings.yaml` | the profile's settings file |
| `export.zip` | "export everything" archive (`archive.export_archive`) |
| `expected.json` | counts computed by that version (papers, readings, XP, ...) |

**Never regenerate the fixtures of a released version**: they are the proof of
what that version wrote. Every release adds its own folder (see CONTRIBUTING.md):

    tools/make_compat_fixtures.py --tags v1.0.0
