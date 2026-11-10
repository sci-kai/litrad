# Contributing to litrad

Thanks for using and testing litrad! Reports of what breaks are the most
useful contribution. Contributions are accepted under the project's license
(MIT with the Commons Clause, see [LICENSE](LICENSE)).

## Reporting problems and ideas

Open an issue: **Bug report** for something that does not work as described
in the [documentation](docs/documentation.md), **Feature request** for ideas,
**Testing feedback** for how it worked for you over a week or a month (see
[TESTING.md](TESTING.md)). What is planned is in [ROADMAP.md](ROADMAP.md);
please comment on an existing issue instead of opening a duplicate.
Please include `litrad version`, your system and the exact steps. Never
paste your BibTeX export, notes, passwords or API keys. Security problems:
see [SECURITY.md](SECURITY.md).

## Development set-up

```bash
git clone <repository-url> litrad && cd litrad
python3 install.py --dev          # .venv with test and lint tools, config/config.yaml
.venv/bin/pytest                  # offline test suite (a few minutes on a small ARM board)
.venv/bin/pytest --network        # also against the real APIs (slow, uses free API budget)
.venv/bin/ruff check src tests install.py
.venv/bin/ruff format src tests install.py
```

Details on fixtures, the test modules and recording new API responses are in
the [Development chapter](docs/documentation.md#14-development).

## Development cycle

```
feat-*/issueNN-*  ──PR──►  dev  ──fast-forward──►  main  ──tag vX.Y.Z──►  GitHub release
```

- **`main`** holds releases only. Nothing is committed to it directly; it is
  fast-forwarded to `dev` when a release is made.
- **`dev`** is the development branch. All work starts here and comes back
  here; it is kept linear (no merge commits).
- **Feature branches** are named `feat-<short-name>` or `issueNN-<short-name>`
  (for issue #NN), use only `a-z`, `0-9` and `-`, start from `dev` and are
  deleted after merging.
- **Pull requests** target `dev`. CI (lint and tests on Python 3.11 to 3.13)
  must pass. PRs are merged with *rebase and merge*: no merge commits, no
  squash commits. Clean up your history first (`git rebase -i dev`).

```bash
git switch dev && git pull
git switch -c issue12-queue-limit
# ... commits ...
git fetch && git rebase origin/dev
git push -u origin issue12-queue-limit     # then open a pull request into dev
```

### What a change includes

- tests for new behaviour and for every fixed bug;
- the documentation (`docs/documentation.md`). The web UI renders it, and a
  test checks that all its internal links work. The package ships a real
  copy (`src/litrad/web/documentation.md`, no symlink: those break in
  wheels and Windows checkouts); after editing run
  `python tools/sync_docs.py` (the same for `config/config.example.yaml`),
  a test fails if the copies differ. Changes to the scoring also
  update the "How recommendations are made" panel
  (`src/litrad/web/templates/_how_recommend.html`);
- a line in `CHANGELOG.md` under `## Unreleased`;
- new dependencies credited in "Implementation and credits".

Code style: PEP 8 via ruff (line length 100), docstrings for modules and
non-trivial functions, comments that explain *why*. Dates and times are ISO
8601 (`2026-10-09`, `2026-10-09T08:42:05`); file names use only `a-z`, `0-9`,
`-`, `_` and `.`; tables are TSV, configuration is YAML.

## Versions and releases

Versions follow [semantic versioning](https://semver.org): `MAJOR.MINOR.PATCH`,
pre-releases as `1.0.0-rc.1`, development states as `1.1.0-dev`. Within 1.x
every version reads and upgrades the data, settings and archives of every
earlier 1.x version; changes only add (see the
[roadmap principles](ROADMAP.md#principles)).

To release (maintainers):

1. On `dev`: rename the CHANGELOG section `## Unreleased` to
   `## X.Y.Z (YYYY-MM-DD)` and run the full test suite, including `--network`.
2. A **separate commit** only bumps the version: `pyproject.toml`,
   `src/litrad/__init__.py`, `CITATION.cff` (version and date).
3. Fast-forward `main` to `dev`, tag and push:

   ```bash
   git switch main && git merge --ff-only dev
   git tag -a vX.Y.Z -m "litrad X.Y.Z"
   git push origin main dev vX.Y.Z
   ```

4. The release workflow tests again, builds the wheel and source archive and
   publishes a GitHub release with the CHANGELOG section as notes
   (pre-releases for versions with a `-` suffix).
5. On `dev`, add the release's compatibility fixtures
   (`tools/make_compat_fixtures.py --tags vX.Y.Z`, folder `tests/data/compat/X.Y.Z/`)
   and commit them. The first are those of `v1.0.0`, added at that release;
   **every later release adds its own**, and fixtures of a released version
   are never regenerated. Until the first folder exists, `tests/test_compat.py`
   runs the same checks on fixtures the builder writes with the current code. They prove that later versions
   still read what this one wrote. A change of the database models needs a
   migration step and a higher `SCHEMA_VERSION`
   (`src/litrad/migrations.py`); a change of the archive content a
   higher `DATA_FORMAT_VERSION` (`src/litrad/dataformat.py`) and its
   schemas (see the [Data formats chapter](docs/documentation.md#11-data-formats)).
6. On `dev`, bump to the next `-dev` version in its own commit.

Two more workflows run for every `v*` tag; each first runs the lint and
test job of CI (Python 3.11–3.13) and publishes nothing if it fails:

- **`publish-pypi.yml`** uploads the wheel and source archive to PyPI for
  releases (`vX.Y.Z`) and pre-releases PEP 440 accepts (`v1.1.0rc1`,
  `v1.0.0-rc.1`). `-dev` tags (`1.1.0-dev` is PEP 440 `1.1.0.dev0`) are
  skipped, and a tag that differs from the version in `pyproject.toml`
  fails. "Run workflow" uploads to TestPyPI instead.
- **`docker.yml`** builds the image for `linux/amd64` and `linux/arm64` and
  pushes it to `ghcr.io/<owner>/litrad` (tag = version; `latest` only
  for releases). Docker is not needed locally; to try it:
  `docker build -t litrad .`

One-time set-up (repository owner):

1. PyPI: *Your projects → Publishing → Add a pending publisher* with the
   project name `litrad`, this repository, workflow `publish-pypi.yml`
   and environment `pypi`. Do the same on test.pypi.org with environment
   `testpypi`. No token is stored in GitHub (Trusted Publishing).
2. GitHub: *Settings → Environments*: create `pypi` and `testpypi`
   (optionally require your approval for `pypi`).
3. GitHub: after the first image push, *Packages → litrad → Package
   settings*: set the visibility to public and link it to the repository.
4. TODO: add `[project.urls]` (Homepage, Issues, Changelog) to
   `pyproject.toml` once the public repository URL is fixed; PyPI then links
   to it.
