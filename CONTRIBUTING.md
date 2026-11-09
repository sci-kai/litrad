# Contributing to litresearch

Thanks for testing litresearch! It is in early development (`0.x`): things
will break, and reports of what breaks are the most useful contribution.

## Reporting problems and ideas

Open an issue: **Bug report** for something that does not work as described
in the [documentation](docs/documentation.md), **Feature request** for ideas,
**Testing feedback** for how it worked for you over a week or a month (see
[TESTING.md](TESTING.md)). What is planned is in [ROADMAP.md](ROADMAP.md);
please comment on an existing issue instead of opening a duplicate.
Please include `litresearch version`, your system and the exact steps. Never
paste your BibTeX export, notes, passwords or API keys. Security problems:
see [SECURITY.md](SECURITY.md).

## Development set-up

```bash
git clone <repository-url> litresearch && cd litresearch
python3 install.py --dev          # .venv with test and lint tools, config/config.yaml
.venv/bin/pytest                  # offline test suite, about 3 minutes on a Raspberry Pi 5
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
  copy (`src/litresearch/web/documentation.md`, no symlink: those break in
  wheels and Windows checkouts); after editing run
  `python tools/sync_docs.py` (the same for `config/config.example.yaml`),
  a test fails if the copies differ. Changes to the scoring also
  update the "How recommendations are made" panel
  (`src/litresearch/web/templates/_how_recommend.html`);
- a line in `CHANGELOG.md` under the unreleased version;
- new dependencies credited in "Implementation and credits".

Code style: PEP 8 via ruff (line length 100), docstrings for modules and
non-trivial functions, comments that explain *why*. Dates and times are ISO
8601 (`2026-10-09`, `2026-10-09T08:42:05`); file names use only `a-z`, `0-9`,
`-`, `_` and `.`; tables are TSV, configuration is YAML.

## Versions and releases

Versions follow [semantic versioning](https://semver.org): `MAJOR.MINOR.PATCH`,
pre-releases as `0.1.0-alpha.1`, development states as `0.2.0-dev`. Before
1.0.0, minor versions may change the data format or the configuration; such
changes are listed in the CHANGELOG.

To release (maintainers):

1. On `dev`: move the CHANGELOG section `## X.Y.Z (unreleased)` to
   `## X.Y.Z (YYYY-MM-DD)` and run the full test suite, including `--network`.
2. A **separate commit** only bumps the version: `pyproject.toml`,
   `src/litresearch/__init__.py`, `CITATION.cff` (version and date).
3. Fast-forward `main` to `dev`, tag and push:

   ```bash
   git switch main && git merge --ff-only dev
   git tag -a vX.Y.Z -m "litresearch X.Y.Z"
   git push origin main dev vX.Y.Z
   ```

4. The release workflow tests again, builds the wheel and source archive and
   publishes a GitHub release with the CHANGELOG section as notes
   (pre-releases for versions with a `-` suffix).
5. On `dev`, add the release's compatibility fixtures
   (`tools/make_compat_fixtures.py --tags vX.Y.Z`, folder `tests/data/compat/X.Y.Z/`)
   and commit them. **Every release adds its fixtures**; fixtures of a
   released version are never regenerated. They prove that later versions
   still read what this one wrote. A change of the database models needs a
   migration step and a higher `SCHEMA_VERSION`
   (`src/litresearch/migrations.py`); a change of the archive content a
   higher `DATA_FORMAT_VERSION` (`src/litresearch/dataformat.py`) and its
   schemas (see the [Data formats chapter](docs/documentation.md#11-data-formats)).
6. On `dev`, bump to the next `-dev` version in its own commit.

Two more workflows run for every `v*` tag:

- **`publish-pypi.yml`** uploads the wheel and source archive to PyPI for
  releases (`vX.Y.Z`) and pre-releases PEP 440 accepts (`v0.3.0rc1`,
  `v0.3.0-rc.1`). `-dev` tags (`0.2.0-dev` is PEP 440 `0.2.0.dev0`) are
  skipped, and a tag that differs from the version in `pyproject.toml`
  fails. "Run workflow" uploads to TestPyPI instead.
- **`docker.yml`** builds the image for `linux/amd64` and `linux/arm64` and
  pushes it to `ghcr.io/<owner>/<repository>` (tag = version; `latest` only
  for releases). Docker is not needed locally; to try it:
  `docker build -t litresearch .`

One-time set-up (repository owner):

1. PyPI: *Your projects → Publishing → Add a pending publisher* with the
   project name `litresearch`, this repository, workflow `publish-pypi.yml`
   and environment `pypi`. Do the same on test.pypi.org with environment
   `testpypi`. No token is stored in GitHub (Trusted Publishing).
2. GitHub: *Settings → Environments*: create `pypi` and `testpypi`
   (optionally require your approval for `pypi`).
3. GitHub: after the first image push, *Packages → litresearch → Package
   settings*: set the visibility to public and link it to the repository.
4. TODO: add `[project.urls]` (Homepage, Issues, Changelog) to
   `pyproject.toml` once the public repository URL is fixed; PyPI then links
   to it.
