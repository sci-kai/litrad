"""Packaging and deployment: what ships in the wheel, the Docker image, the workflows."""

import json
import re
import tomllib
from importlib import resources
from pathlib import Path

import yaml
from typer.testing import CliRunner

from litrad import locations
from litrad.cli import app
from litrad.config import load_config
from litrad.web.auth import verify_password

REPO = Path(__file__).parents[1]
PACKAGE = REPO / "src" / "litrad"
runner = CliRunner()


def test_package_copies_match_their_sources():
    docs = (REPO / "docs" / "documentation.md").read_bytes()
    assert (PACKAGE / "web" / "documentation.md").read_bytes() == docs, (
        "web/documentation.md differs from docs/documentation.md; fix: python tools/sync_docs.py"
    )
    example = (REPO / "config" / "config.example.yaml").read_bytes()
    assert (PACKAGE / "config.example.yaml").read_bytes() == example, (
        "the bundled config.example.yaml differs from config/config.example.yaml; "
        "fix: python tools/sync_docs.py"
    )


def test_documentation_is_a_real_file_not_a_symlink():
    # symlinks become text files in Windows checkouts and are lost in some archives
    assert not (PACKAGE / "web" / "documentation.md").is_symlink()


def test_sync_tool_check_mode(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("sync_docs", REPO / "tools" / "sync_docs.py")
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    assert sync.main(["--check"]) == 0
    src, copy = tmp_path / "a.md", tmp_path / "b.md"
    src.write_text("new")
    copy.write_text("old")
    sync.PAIRS[:] = [(src, copy)]
    assert sync.main(["--check"]) == 1
    assert copy.read_text() == "old"
    assert sync.main([]) == 0 and copy.read_text() == "new"


def test_package_data_globs_cover_every_template_and_static_file():
    data = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["setuptools"][
        "package-data"
    ]
    web = PACKAGE / "web"
    for folder in ("templates", "static"):
        files = {p.relative_to(web).as_posix() for p in (web / folder).rglob("*") if p.is_file()}
        assert files, folder
        covered = {f for f in files for pattern in data["litrad.web"] if _glob(web, pattern, f)}
        assert files == covered, f"not covered by package-data: {sorted(files - covered)}"


def _glob(base: Path, pattern: str, relative: str) -> bool:
    return relative in {p.relative_to(base).as_posix() for p in base.glob(pattern) if p.is_file()}


def test_resources_are_found_through_importlib():
    web = resources.files("litrad.web")
    assert web.joinpath("documentation.md").read_text(encoding="utf-8").startswith("# ")
    assert web.joinpath("static", "app.css").is_file()
    assert web.joinpath("templates", "welcome.html").is_file()
    assert "web:" in resources.files("litrad").joinpath("config.example.yaml").read_text()


def test_classifiers_are_not_linux_only():
    project = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    assert "Operating System :: OS Independent" in project["classifiers"]
    assert not any("POSIX" in c for c in project["classifiers"])


# --- first run in a container: LITRAD_CONFIG pointing at a fresh folder -------------


def test_environment_config_is_created_on_first_use_with_data_next_to_it(tmp_path, monkeypatch):
    target = tmp_path / "data" / "config.yaml"
    monkeypatch.setenv("LITRAD_CONFIG", str(target))
    messages = []
    choice = locations.locate_config(announce=messages.append)
    assert choice.path == target and choice.rule == "env" and target.is_file()
    assert messages and str(target) in messages[0]
    cfg = load_config(target)
    assert cfg.paths.database.parent == target.parent
    assert cfg.backup.dir.parent == target.parent
    # the second run keeps the file and says nothing
    target.write_text(target.read_text() + "\n# mine\n")
    messages.clear()
    locations.locate_config(announce=messages.append)
    assert "# mine" in target.read_text() and not messages


def test_explicit_config_option_is_never_created(tmp_path, monkeypatch):
    monkeypatch.setenv("LITRAD_CONFIG", str(tmp_path / "env.yaml"))
    choice = locations.locate_config(tmp_path / "missing.yaml")
    assert choice.rule == "option" and not (tmp_path / "missing.yaml").exists()
    assert not (tmp_path / "env.yaml").exists()


# --- set-password --password-file ---------------------------------------------------------


def _config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("web:\n  port: 8080\n")
    return path


def test_set_password_from_file_uses_first_line_and_never_prints_it(tmp_path):
    path = _config(tmp_path)
    secret = tmp_path / "secret"
    secret.write_text("correct-horse-battery\nignored second line\n")
    result = runner.invoke(app, ["set-password", "-c", str(path), "--password-file", str(secret)])
    assert result.exit_code == 0, result.output
    assert "correct-horse-battery" not in result.output
    stored = yaml.safe_load(path.read_text())["web"]["password_hash"]
    assert verify_password("correct-horse-battery", stored)


def test_set_password_from_file_rejects_short_and_missing(tmp_path):
    path = _config(tmp_path)
    short = tmp_path / "short"
    short.write_text("abc\n")
    result = runner.invoke(app, ["set-password", "-c", str(path), "--password-file", str(short)])
    assert result.exit_code == 1 and "at least 8" in result.output
    assert "password_hash" not in path.read_text()
    result = runner.invoke(
        app, ["set-password", "-c", str(path), "--password-file", str(tmp_path / "none")]
    )
    assert result.exit_code == 1 and "Cannot read" in result.output
    empty = tmp_path / "empty"
    empty.write_text("")
    result = runner.invoke(app, ["set-password", "-c", str(path), "--password-file", str(empty)])
    assert result.exit_code == 1 and "password_hash" not in path.read_text()


# --- Dockerfile, .dockerignore and workflows (static checks; Docker is not run here) -----


def _dockerfile_instructions() -> list[list[str]]:
    text = (REPO / "Dockerfile").read_text().replace("\\\n", " ")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    return [(ln.split(None, 1) + [""])[:2] for ln in lines]


def test_dockerfile_is_a_multistage_non_root_server_image():
    ins = _dockerfile_instructions()
    froms = [arg for kw, arg in ins if kw == "FROM"]
    assert len(froms) == 2 and froms[0].endswith("AS build") and "python:3.13-slim" in froms[1]
    text = " ".join(f"{kw} {arg}" for kw, arg in ins)
    assert "COPY --from=build" in text
    assert not re.search(r"apt-get|build-essential|gcc", text), "no compilers in the image"
    user = [arg for kw, arg in ins if kw == "USER"]
    assert user and user[-1] not in ("root", "0")
    assert ["VOLUME", "/data"] in ins and ["EXPOSE", "8080"] in ins
    env = " ".join(arg for kw, arg in ins if kw == "ENV")
    assert "LITRAD_CONFIG=/data/config.yaml" in env
    health = " ".join(arg for kw, arg in ins if kw == "HEALTHCHECK")
    assert "/login" in health and "curl" not in health
    cmd = next(arg for kw, arg in ins if kw == "CMD")
    assert json.loads(cmd) == [
        "litrad", "serve", "--no-choose", "--network", "all", "--port", "8080",
    ]  # fmt: skip


def test_dockerignore_keeps_private_data_out_of_the_image():
    lines = (REPO / ".dockerignore").read_text().split()
    for private in ("data/", "config/config.yaml", ".venv", "*.bib", ".git"):
        assert private in lines


def test_workflows_are_valid_yaml_with_the_needed_permissions():
    publish = yaml.safe_load((REPO / ".github/workflows/publish-pypi.yml").read_text())
    assert publish["jobs"]["pypi"]["permissions"] == {"id-token": "write"}
    assert publish["jobs"]["testpypi"]["permissions"] == {"id-token": "write"}
    docker = yaml.safe_load((REPO / ".github/workflows/docker.yml").read_text())
    assert docker["permissions"]["packages"] == "write"
    build = next(s for s in docker["jobs"]["image"]["steps"] if "build-push" in s.get("uses", ""))
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"
