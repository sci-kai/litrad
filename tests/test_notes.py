"""Notes as Markdown files (notes.py): naming, front matter, sync, conflicts, preview."""

import re
import zipfile
from datetime import datetime

import pytest
import yaml
from sqlmodel import Session, select
from typer.testing import CliRunner

import litrad.cli as cli
from conftest import make_profile, profile_cfg
from litrad import archive, history, notes, profiles
from litrad.backup import restore, run_backup, save_profile_archive
from litrad.config import Config, load_config
from litrad.db import (
    Library,
    LibraryPaper,
    NoteFile,
    Paper,
    Reading,
    ReadStatus,
    XpEvent,
    init_db,
)
from litrad.reading import add_note, record_reading
from test_web import app_env, celebration, paper_id, post  # noqa: F401 - fixtures

AT = datetime(2026, 10, 7, 8, 30, 0)
runner = CliRunner()


@pytest.fixture
def env(cfg):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        papers = [
            Paper(
                title="Klonale Evolution über Zeit",
                doi="10.1/a",
                bibtex_key="Müller2020:ab c",
                authors=["Müller, Jörg", "Doe, A"],
                year=2020,
                venue="Nature",
                pmid="111",
                tags=["tumour evolution"],
            ),
            Paper(title="Fibroblasts", doi="10.1/b"),
            Paper(title="No identifiers"),
        ]
        session.add_all(papers)
        session.commit()
        yield cfg, session, papers
    engine.dispose()


def folder(cfg):
    return notes.notes_dir(cfg)


def file_of(session, cfg, paper):
    return notes.path_for(session, cfg, paper)


def xp(session, kind):
    return sum(e.points for e in session.exec(select(XpEvent).where(XpEvent.kind == kind)).all())


# --- names ----------------------------------------------------------------------------


def test_safe_names():
    assert notes.safe_name("Müller2020:ab c") == "Muller2020-ab-c"
    assert notes.safe_name("a/b\\c*?<>|\"' d") == "a-b-c-d"
    assert notes.safe_name("..hidden..") == "hidden"
    assert notes.safe_name("日本") is None
    assert notes.safe_name("CON") == "CON-"
    assert re.fullmatch(r"[A-Za-z0-9._-]+", notes.safe_name("x" * 300))


def test_file_names_fallbacks_and_collisions(env):
    cfg, session, (a, b, c) = env
    assert file_of(session, cfg, a).name == "Muller2020-ab-c.md"
    assert file_of(session, cfg, b).name == "doi-10.1-b.md"
    assert file_of(session, cfg, c).name == f"paper-{c.id}.md"
    # another paper's file has the name already: a suffix
    other = Paper(title="Same key elsewhere", doi="10.1/z")
    session.add(other)
    session.commit()
    session.add(Library(id=9, name="L"))
    session.add(LibraryPaper(library_id=9, paper_id=other.id, citation_key="Müller2020:ab-c"))
    session.commit()
    for paper in (a, other):
        record_reading(session, cfg, paper, ReadStatus.read, note=f"note of {paper.title}", at=AT)
    assert file_of(session, cfg, a).name == "Muller2020-ab-c.md"
    assert file_of(session, cfg, other).name == "Muller2020-ab-c-2.md"
    assert "note of Same key" in file_of(session, cfg, other).read_text()


# --- format ---------------------------------------------------------------------------


def test_new_file_format(env):
    cfg, session, (a, *_) = env
    record_reading(session, cfg, a, ReadStatus.thorough, rating=5, note="Key figure 3", at=AT)
    text = file_of(session, cfg, a).read_bytes().decode("utf-8")
    assert "\r" not in text and text.startswith("---\n")
    front, body = notes.split(text)
    meta = yaml.safe_load(front)
    assert list(meta) == list(notes.MANAGED_KEYS)
    assert meta["litrad_id"] == a.id and meta["title"] == "Klonale Evolution über Zeit"
    assert meta["authors"] == ["Müller, Jörg", "Doe, A"] and meta["year"] == 2020
    assert meta["journal"] == "Nature" and meta["doi"] == "10.1/a" and meta["pmid"] == "111"
    assert meta["citekey"] == "Müller2020:ab c" and meta["status"] == "thorough"
    assert meta["rating"] == 5 and meta["tags"] == ["tumour-evolution"]
    assert meta["format"] == "litrad-note/1"
    assert body.strip() == f"[{AT.isoformat()}] Key figure 3"


def test_user_keys_and_formatting_survive_and_body_is_never_rewritten(env):
    cfg, session, (a, *_) = env
    record_reading(session, cfg, a, ReadStatus.skimmed, note="first", at=AT)
    path = file_of(session, cfg, a)
    front, _ = notes.split(path.read_text())
    body = "\n\nMy *own*   text  \n\n- [ ] todo   \n\n\n"
    front = front.replace("tags:\n  - tumour-evolution\n", "tags: [mine]\n")
    path.write_text("---\n" + front + "aliases: [Mueller 2020]  # keep me\n---" + body)
    notes.refresh_paper(session, cfg, a)  # imports the edit
    # status and rating change: only the front matter changes
    record_reading(session, cfg, a, ReadStatus.thorough, rating=4, at=AT)
    text = path.read_text()
    assert text.endswith("---" + body)
    assert "aliases: [Mueller 2020]  # keep me" in text
    assert "tags: [mine, tumour-evolution]" in text  # flow style kept
    meta = yaml.safe_load(notes.split(text)[0])
    assert meta["status"] == "thorough" and meta["rating"] == 4
    assert meta["tags"] == ["mine", "tumour-evolution"]  # tags are only added


def test_crlf_and_bom_are_normalized(env):
    cfg, session, (a, *_) = env
    record_reading(session, cfg, a, ReadStatus.read, note="x", at=AT)
    path = file_of(session, cfg, a)
    front, _ = notes.split(path.read_text())
    windows = ("﻿---\n" + front + "---\n\nline one\nline two\n").replace("\n", "\r\n")
    path.write_bytes(windows.encode("utf-8"))
    notes.refresh_paper(session, cfg, a)
    assert session.get(Reading, a.id).notes == "line one\nline two"
    record_reading(session, cfg, a, ReadStatus.thorough, at=AT)  # rewrites the front matter
    assert b"\r" not in path.read_bytes()


def test_unicode_and_invalid_front_matter(env):
    cfg, session, (a, *_) = env
    record_reading(session, cfg, a, ReadStatus.read, note="Ünïcödé ✍️ 日本語", at=AT)
    path = file_of(session, cfg, a)
    assert "Ünïcödé ✍️ 日本語" in path.read_text(encoding="utf-8")
    path.write_text("---\ntitle: [unclosed\n---\nbody stays\n", encoding="utf-8")
    state = notes.refresh_paper(session, cfg, a)
    assert session.get(Reading, a.id).notes == "body stays"
    assert "not valid YAML" in state.warning
    assert path.read_text() == "---\ntitle: [unclosed\n---\nbody stays\n"  # untouched


# --- sync -----------------------------------------------------------------------------


def test_external_edit_refreshes_cache_and_awards_reflection_once(env):
    cfg, session, (a, *_) = env
    record_reading(session, cfg, a, ReadStatus.read, note="short", at=AT)
    assert xp(session, "reflection") == 0
    path = file_of(session, cfg, a)
    path.write_text(path.read_text() + "\n" + "long thoughts " * 30)
    state = notes.refresh_paper(session, cfg, a)
    assert state.actions == ["imported"]
    assert "long thoughts" in session.get(Reading, a.id).notes
    awarded = xp(session, "reflection")
    assert awarded > 0
    path.write_text(path.read_text() + "\nmore " * 50)
    notes.refresh_paper(session, cfg, a)
    notes.save(session, cfg, a, "x" * 400, notes.refresh_paper(session, cfg, a).base)
    add_note(session, cfg, a, "y" * 300, at=AT)
    assert xp(session, "reflection") == awarded  # never twice


def test_unchanged_file_is_not_rewritten(env):
    cfg, session, (a, *_) = env
    record_reading(session, cfg, a, ReadStatus.read, note="n", at=AT)
    path = file_of(session, cfg, a)
    before = path.stat().st_mtime_ns
    for _ in range(3):
        assert notes.refresh_paper(session, cfg, a).actions == []
    assert path.stat().st_mtime_ns == before


def test_renamed_file_is_found_again(env):
    cfg, session, (a, *_) = env
    record_reading(session, cfg, a, ReadStatus.read, note="n", at=AT)
    path = file_of(session, cfg, a)
    moved = path.parent / "sub" / "Renamed in Obsidian.md"
    moved.parent.mkdir()
    path.rename(moved)
    moved.write_text(moved.read_text() + "\nedited after the move\n")
    state = notes.refresh_paper(session, cfg, a)
    assert state.path == moved and "relocated" in state.actions
    assert session.get(NoteFile, a.id).path == "sub/Renamed in Obsidian.md"
    assert "edited after the move" in session.get(Reading, a.id).notes


def test_obsidian_created_file_is_linked(env):
    """Clicking [[key]] in Obsidian creates an empty file named after the key."""
    cfg, session, (a, b, _) = env
    record_reading(session, cfg, b, ReadStatus.read, at=AT)
    (folder(cfg) / "doi-10.1-b.md").write_text("Written in Obsidian\n")
    state = notes.refresh_paper(session, cfg, b)
    assert state.actions == ["imported", "metadata"]
    assert session.get(Reading, b.id).notes == "Written in Obsidian"
    text = (folder(cfg) / "doi-10.1-b.md").read_text()
    assert text.startswith("---\n") and text.endswith("Written in Obsidian\n")


def test_conflict_on_editor_save_keeps_both(env):
    cfg, session, (a, *_) = env
    record_reading(session, cfg, a, ReadStatus.read, note="original", at=AT)
    opened = notes.refresh_paper(session, cfg, a).base
    path = file_of(session, cfg, a)
    path.write_text(path.read_text().replace("original", "edited in Obsidian"))
    with pytest.raises(notes.NoteConflict) as caught:
        notes.save(session, cfg, a, "my edit", opened)
    assert "edited in Obsidian" in caught.value.theirs and caught.value.mine == "my edit"
    assert "edited in Obsidian" in path.read_text() and "my edit" not in path.read_text()
    # saving again from the conflict view, with the file's hash, wins
    notes.save(session, cfg, a, "merged", caught.value.base)
    assert notes.split(path.read_text())[1].strip() == "merged"
    assert session.get(Reading, a.id).notes == "merged"


def test_both_sides_changed_is_a_listed_conflict(env):
    cfg, session, (a, *_) = env
    record_reading(session, cfg, a, ReadStatus.read, note="base", at=AT)
    path = file_of(session, cfg, a)
    path.write_text(path.read_text().replace("base", "file side"))
    reading = session.get(Reading, a.id)
    reading.notes = "database side"  # e.g. a progress import
    session.add(reading)
    session.commit()
    state = notes.refresh_paper(session, cfg, a)
    assert state.conflict and "file side" in state.file_body and state.body == "database side"
    assert "file side" in path.read_text()
    report = notes.sync(session, cfg)
    assert [c[0] for c in report.conflicts] == [a.id]
    notes.save(session, cfg, a, state.file_body, state.base)  # "Keep file"
    assert not session.get(NoteFile, a.id).conflict
    assert "file side" in session.get(Reading, a.id).notes


def test_log_dialog_appends_to_the_current_file(env):
    cfg, session, (a, *_) = env
    record_reading(session, cfg, a, ReadStatus.skimmed, note="first", at=AT)
    path = file_of(session, cfg, a)
    path.write_text(path.read_text() + "\nadded in Obsidian\n")
    record_reading(session, cfg, a, ReadStatus.read, note="second", at=AT)
    body = notes.split(path.read_text())[1]
    assert body.index("first") < body.index("added in Obsidian") < body.index("second")
    assert session.get(Reading, a.id).notes == body.strip()


# --- migration ------------------------------------------------------------------------


def test_migration_is_idempotent_and_conflict_safe(env):
    cfg, session, (a, b, c) = env
    for paper, text in ((a, "old note a"), (b, "old note b")):
        session.add(Reading(paper_id=paper.id, status=ReadStatus.read, notes=text))
    session.add(Reading(paper_id=c.id, status=ReadStatus.read))
    session.commit()
    # a file for b exists already with other content
    existing = folder(cfg) / "doi-10.1-b.md"
    existing.write_text(f"---\nlitrad_id: {b.id}\n---\nkept as it is\n")
    report = notes.refresh_all(session, cfg)
    assert report.counts == {"created": 1, "conflict": 1}
    assert [cid for cid, *_ in report.conflicts] == [b.id]
    assert existing.read_text().endswith("kept as it is\n")
    assert "old note a" in file_of(session, cfg, a).read_text()
    assert not file_of(session, cfg, c).exists()  # no notes, no file
    again = notes.refresh_all(session, cfg)
    assert again.counts == {"conflict": 1}
    assert len(list(folder(cfg).glob("*.md"))) == 2


def test_folder_unavailable_never_blocks_reading(env, tmp_path):
    cfg, session, (a, *_) = env
    gone = cfg.model_copy(
        update={"paths": cfg.paths.model_copy(update={"obsidian_vault": tmp_path / "unmounted"})}
    )
    record_reading(session, gone, a, ReadStatus.read, note="kept in the database", at=AT)
    assert session.get(Reading, a.id).notes.endswith("kept in the database")
    with pytest.raises(notes.NotesUnavailable):
        notes.notes_dir(gone)
    assert notes.refresh_all(session, gone).problem.startswith("Obsidian vault not found")
    # once the vault is there, the note file is written
    (tmp_path / "unmounted").mkdir()
    assert notes.refresh_all(session, gone).counts == {"created": 1}
    profile_folder = cfg.paths.database.parent.name
    assert (tmp_path / "unmounted" / "litrad" / profile_folder / "Muller2020-ab-c.md").exists()


# --- template and preview ---------------------------------------------------------------


def test_template_placeholders(env):
    cfg, session, (a, b, _) = env
    assert notes.template(cfg, session, b) == notes.BUILTIN_TEMPLATE
    (folder(cfg) / notes.TEMPLATE_FILE).write_text(
        "# {title}\n{authors} ({year}) {journal}\ndoi {doi} pmid {pmid} [[{citekey}]] {date}\n"
        "{unknown} {{literal}}\n"
    )
    text = notes.template(cfg, session, a, today=AT.date())
    assert text == (
        "# Klonale Evolution über Zeit\nMüller, Jörg; Doe, A (2020) Nature\n"
        "doi 10.1/a pmid 111 [[Müller2020:ab c]] 2026-10-07\n{unknown} {{literal}}\n"
    )
    assert notes.scan(folder(cfg)) == []  # the template is no note


def test_preview_escapes_html_and_links_papers(env):
    cfg, session, (a, b, _) = env
    html = notes.render_html(
        session,
        "<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n"
        "[x](javascript:alert(1)) [ok](https://example.org) ![pic](https://e.org/p.png)\n"
        "[[Müller2020:ab c]] [[Muller2020-ab-c|alias]] [[Unknown2000]] `[[code]]` **bold**",
    )
    assert "<script" not in html and "<img" not in html
    assert "&lt;script&gt;" in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert 'href="javascript' not in html and "<a>x</a>" in html
    assert '<a href="https://e.org/p.png" rel="noopener noreferrer">🖼 pic</a>' in html
    assert '<a href="https://example.org" rel="noopener noreferrer">ok</a>' in html
    assert f'<a class="wikilink" href="/papers/{a.id}">Müller2020:ab c</a>' in html
    assert f'<a class="wikilink" href="/papers/{a.id}">alias</a>' in html
    assert (
        '<span class="wikilink missing" title="No paper with this citation key">Unknown2000 (?)'
        in html
    )
    assert "<code>[[code]]</code>" in html and "<strong>bold</strong>" in html


# --- web ------------------------------------------------------------------------------


def web_paper(path):
    pid = paper_id(path, bibtex_key="Cheung2016-vl")
    return pid


def test_web_editor_preview_save_and_conflict(app_env):  # noqa: F811
    client, path, _ = app_env
    pid = web_paper(path)
    page = client.get(f"/papers/{pid}").text
    assert "Log a reading to add notes" in page
    post(client, f"/papers/{pid}/read", {"level": "read"})
    page = client.get(f"/papers/{pid}").text
    base = re.search(r'name="base" value="([0-9a-f]*)"', page).group(1)
    assert "## Summary" in page  # the built-in template fills a new note
    preview = post(
        client,
        f"/papers/{pid}/note",
        {"text": "**hi** <b>x</b>", "base": base, "action": "preview"},
    )
    assert "<strong>hi</strong>" in preview.text and "&lt;b&gt;x&lt;/b&gt;" in preview.text
    cfg = profile_cfg(path)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        assert not notes.path_for(session, cfg, session.get(Paper, pid)).exists()  # not saved
    payload = celebration(post(client, f"/papers/{pid}/note", {"text": "saved text", "base": base}))
    assert payload["kind"] == "success"
    with Session(engine) as session:
        note_path = notes.path_for(session, cfg, session.get(Paper, pid))
    assert note_path.read_text().endswith("saved text\n")
    # the file changes in Obsidian; saving from the old page is a conflict
    note_path.write_text(note_path.read_text().replace("saved text", "obsidian text"))
    page = post(client, f"/papers/{pid}/note", {"text": "stale edit", "base": base}).text
    assert "Conflict" in page and "obsidian text" in page and "stale edit" in page
    assert "Keep file" in page and "Keep mine" in page and "Save merged" in page
    assert note_path.read_text().endswith("obsidian text\n")
    engine.dispose()


def test_web_editor_keeps_the_draft_when_the_file_cannot_be_written(app_env, monkeypatch):  # noqa: F811
    client, path, _ = app_env
    pid = web_paper(path)
    post(client, f"/papers/{pid}/read", {"level": "read", "note": "on disk"})
    page = client.get(f"/papers/{pid}").text
    base = re.search(r'name="base" value="([0-9a-f]*)"', page).group(1)

    def full(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(notes, "_write", full)
    response = post(client, f"/papers/{pid}/note", {"text": "my long draft", "base": base})
    assert response.status_code == 200
    assert "No space left on device" in response.text and "my long draft" in response.text
    assert 'class="note-editor" open' in response.text
    cfg = profile_cfg(path)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        assert session.get(Reading, pid).notes.endswith("on disk")  # nothing changed
        with pytest.raises(notes.NotesError, match="could not be saved"):
            notes.save(session, cfg, session.get(Paper, pid), "again", base)
        assert session.get(Reading, pid).notes.endswith("on disk")
    engine.dispose()


def test_web_log_dialog_with_changed_file(app_env):  # noqa: F811
    client, path, _ = app_env
    pid = web_paper(path)
    post(client, f"/papers/{pid}/read", {"level": "abstract", "note": "first"})
    page = client.get(f"/papers/{pid}").text
    base = re.search(r'name="note_base" value="([0-9a-f]+)"', page).group(1)
    cfg = profile_cfg(path)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        note_path = notes.path_for(session, cfg, session.get(Paper, pid))
    engine.dispose()
    note_path.write_text(note_path.read_text() + "\nfrom obsidian\n")
    payload = celebration(
        post(
            client,
            f"/papers/{pid}/read",
            {"level": "read", "note": "second", "note_base": base, "next_url": f"/papers/{pid}"},
        )
    )
    assert "note file had changed" in payload["message"]
    body = notes.split(note_path.read_text())[1]
    assert body.index("from obsidian") < body.index("second")


def test_web_read_papers_search_sees_external_edits_and_settings_status(app_env):  # noqa: F811
    client, path, _ = app_env
    pid = web_paper(path)
    post(client, f"/papers/{pid}/read", {"level": "read", "note": "plain"})
    cfg = profile_cfg(path)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        note_path = notes.path_for(session, cfg, session.get(Paper, pid))
    engine.dispose()
    note_path.write_text(note_path.read_text() + "\nzebrafish\n")
    assert f"/papers/{pid}" in client.get("/read?q=zebrafish").text
    assert f"/papers/{pid}" not in client.get("/read?q=okapi").text
    page = client.get("/settings").text
    assert str(note_path.parent) in page and "1 file" in page and "no conflicts" in page
    (note_path.parent / "Orphan.md").write_text("---\nlitrad_id: 99999\n---\nlost\n")
    payload = celebration(post(client, "/notes/sync"))
    assert "orphans 1" in payload["message"]


# --- vault per profile -------------------------------------------------------------------


def test_vault_per_profile(app_env, tmp_path):  # noqa: F811
    client, path, _ = app_env
    base = load_config(path)
    slug = profiles.get_current(base)
    vault = tmp_path / "vault"
    vault.mkdir()
    profiles.write_overlay(
        base, slug, {"paths": {"obsidian_vault": str(vault), "notes_subfolder": "papers/lit"}}
    )
    cfg = profile_cfg(path)
    assert notes.notes_dir(cfg) == vault / "papers" / "lit" / slug
    pid = web_paper(path)
    post(client, f"/papers/{pid}/read", {"level": "read", "note": "into the vault"})
    written = list((vault / "papers" / "lit" / slug).glob("*.md"))
    assert written[0].read_text().endswith("into the vault\n")
    with pytest.raises(ValueError):
        Config.model_validate({"paths": {"notes_subfolder": "../outside"}})


def shared_vault_config(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        f"paths:\n  cache_dir: {tmp_path / 'cache'}\n  profiles_dir: {tmp_path / 'profiles'}\n"
        f"  obsidian_vault: {vault}\n"
    )
    return config, vault


def note_paper(cfg, text):
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        paper = Paper(title="Shared paper", doi="10.9/shared", bibtex_key="Shared2024")
        session.add(paper)
        session.commit()
        record_reading(session, cfg, paper, ReadStatus.read, note=text, at=AT)
        pid = paper.id
    engine.dispose()
    return pid


def test_profiles_in_one_vault_never_share_note_files(tmp_path):
    config, vault = shared_vault_config(tmp_path)
    base = load_config(config)
    a = profiles.create_profile(base, "Ada")
    cfg_a = profiles.profile_config(base, a)
    pid = note_paper(cfg_a, "Ada's note")
    file_a = vault / "litrad" / a / "Shared2024.md"
    assert notes.notes_dir(cfg_a) == file_a.parent and file_a.read_text().endswith("Ada's note\n")
    # a fresh second profile with the same paper does not pick up Ada's file
    b = profiles.create_profile(base, "Bob")
    cfg_b = profiles.profile_config(base, b)
    note_paper(cfg_b, "Bob's note")
    assert notes.notes_dir(cfg_b) == vault / "litrad" / b
    assert file_a.read_text().endswith("Ada's note\n")
    # Ada's saved profile loaded as a new profile gets its own copies of the files
    engine = init_db(cfg_a.paths.database)
    with Session(engine) as session:
        saved = save_profile_archive(session, cfg_a, tmp_path / "saved")
    engine.dispose()
    result = archive.import_new(base, saved, "Ada copy")
    cfg_c = profiles.profile_config(base, result.slug)
    file_c = vault / "litrad" / result.slug / "Shared2024.md"
    assert result.notes_written == 1 and result.notes_kept == 0
    assert file_c.read_text().endswith("Ada's note\n")
    engine = init_db(cfg_c.paths.database)
    with Session(engine) as session:
        paper = session.get(Paper, pid)
        state = notes.refresh_paper(session, cfg_c, paper)
        notes.save(session, cfg_c, paper, "edited in the copy", state.base, at=AT)
    engine.dispose()
    assert file_c.read_text().endswith("edited in the copy\n")
    assert file_a.read_text().endswith("Ada's note\n")
    engine = init_db(cfg_a.paths.database)
    with Session(engine) as session:
        assert notes.refresh_all(session, cfg_a).counts.get("imported") is None
        assert session.get(Reading, pid).notes.endswith("Ada's note")
    engine.dispose()


def test_a_vault_folder_of_another_profile_is_refused(tmp_path):
    config, vault = shared_vault_config(tmp_path)
    base = load_config(config)
    a = profiles.create_profile(base, "Ada")
    cfg_a = profiles.profile_config(base, a)
    folder_a = notes.notes_dir(cfg_a)
    assert (folder_a / notes.OWNER_FILE).read_text().strip() == str(cfg_a.paths.database.resolve())
    # another installation (other profiles folder) with the same profile id
    other = tmp_path / "other.yaml"
    other.write_text(
        f"paths:\n  cache_dir: {tmp_path / 'cache2'}\n  profiles_dir: {tmp_path / 'other'}\n"
        f"  obsidian_vault: {vault}\n"
    )
    other_base = load_config(other)
    assert profiles.create_profile(other_base, "Ada") == a
    cfg_other = profiles.profile_config(other_base, a)
    with pytest.raises(notes.NotesUnavailable, match="another profile"):
        notes.notes_dir(cfg_other)
    engine = init_db(cfg_other.paths.database)
    with Session(engine) as session:
        assert "another profile" in notes.refresh_all(session, cfg_other).problem
    engine.dispose()
    # once the first profile is gone, the folder can be taken over
    import shutil

    shutil.rmtree(profiles.folder(base, a))
    assert notes.notes_dir(cfg_other) == folder_a
    assert (folder_a / notes.OWNER_FILE).read_text().strip() == str(
        cfg_other.paths.database.resolve()
    )
    assert notes.scan(folder_a) == []  # the marker is not a note


# --- CLI --------------------------------------------------------------------------------


def write_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"paths:\n  cache_dir: {tmp_path / 'cache'}\n  profiles_dir: {tmp_path / 'profiles'}\n"
    )
    return config


def test_cli_sync_check_path(tmp_path):
    config = write_config(tmp_path)
    make_profile(config)
    cfg = profile_cfg(config)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        paper = Paper(title="CLI paper", doi="10.5/x", bibtex_key="Cli2024")
        session.add(paper)
        session.commit()
        session.add(Reading(paper_id=paper.id, status=ReadStatus.read, notes="from before 0.5"))
        session.commit()
        pid = paper.id
    engine.dispose()
    notes_folder = notes.notes_dir(cfg)

    result = runner.invoke(cli.app, ["notes", "check", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert "files created: 1" in result.output and not list(notes_folder.glob("*.md"))

    result = runner.invoke(cli.app, ["notes", "path", f"#{pid}", "-c", str(config)])
    assert result.stdout.strip() == str(notes_folder / "Cli2024.md")

    result = runner.invoke(cli.app, ["notes", "sync", "-c", str(config)])
    assert result.exit_code == 0 and "files created: 1" in result.output
    assert (notes_folder / "Cli2024.md").read_text().endswith("from before 0.5\n")
    (notes_folder / "Stray.md").write_text("no front matter, no paper\n")
    (notes_folder / "Cli2024.md").write_text((notes_folder / "Cli2024.md").read_text() + "edited\n")
    result = runner.invoke(cli.app, ["notes", "sync", "-c", str(config)])
    assert "edits imported from files: 1" in result.output
    assert "orphans (no matching paper): 1" in result.output and "Stray.md" in result.output
    result = runner.invoke(cli.app, ["show", f"#{pid}", "-c", str(config)])
    assert "edited" in result.output


# --- backups and profiles ---------------------------------------------------------------


def test_backup_archives_notes_and_restore_brings_back_missing_files(env):
    cfg, session, (a, b, _) = env
    record_reading(session, cfg, a, ReadStatus.read, note="note a", at=AT)
    record_reading(session, cfg, b, ReadStatus.read, note="note b", at=AT)
    result = run_backup(cfg, session, AT)
    with zipfile.ZipFile(result.archive) as handle:
        names = sorted(n for n in handle.namelist() if n.startswith("notes/"))
        assert names == ["notes/Muller2020-ab-c.md", "notes/doi-10.1-b.md"]
    path_a, path_b = file_of(session, cfg, a), file_of(session, cfg, b)
    session.close()
    path_a.unlink()
    path_b.write_text(path_b.read_text().replace("note b", "changed after the backup"))
    restore(cfg, result.snapshot, AT.replace(hour=10))
    assert path_a.read_text().endswith("note a\n")  # missing: restored
    assert "changed after the backup" in path_b.read_text()  # never overwritten
    engine = init_db(cfg.paths.database)
    with Session(engine) as fresh:
        assert fresh.get(NoteFile, b.id).conflict  # restored copy and file differ
        assert not fresh.get(NoteFile, a.id).conflict
    engine.dispose()


def test_restore_takes_note_files_from_a_backup_made_before_1_0(env):
    """Backups before 1.0 had the note files in litrad_notes_<stamp>.zip."""
    cfg, session, (a, _, _) = env
    record_reading(session, cfg, a, ReadStatus.read, note="note a", at=AT)
    result = run_backup(cfg, session, AT)
    path_a = file_of(session, cfg, a)
    session.close()
    legacy = result.snapshot.with_name("litrad_notes_20261005_090000.zip")
    with zipfile.ZipFile(legacy, "w") as handle:
        handle.writestr(path_a.name, path_a.read_text())
    result.archive.unlink()  # as if made by 0.6
    path_a.unlink()
    restore(cfg, result.snapshot, AT.replace(hour=10))
    assert path_a.read_text().endswith("note a\n")


def test_archive_import_writes_note_files(app_env, tmp_path):  # noqa: F811
    client, path, _ = app_env
    pid = web_paper(path)
    post(client, f"/papers/{pid}/read", {"level": "read", "note": "travels along"})
    cfg = profile_cfg(path)
    engine = init_db(cfg.paths.database)
    with Session(engine) as session:
        note_path = notes.path_for(session, cfg, session.get(Paper, pid))
        note_path.write_text(note_path.read_text() + "\nedited before saving\n")
        notes.refresh_quietly(session, cfg)
        archive.export_archive(session, cfg, tmp_path / "x.zip")
    engine.dispose()
    base = load_config(path)
    slug = archive.import_new(base, tmp_path / "x.zip", "Copy").slug
    copy_cfg = profiles.profile_config(base, slug)
    files = list(notes.notes_dir(copy_cfg).glob("*.md"))
    assert len(files) == 1 and "edited before saving" in files[0].read_text()
    assert files[0].parent == profiles.folder(base, slug) / "notes"


def test_read_papers_search_sees_external_edit(env):
    cfg, session, (a, *_) = env
    record_reading(session, cfg, a, ReadStatus.read, note="plain", at=AT)
    path = file_of(session, cfg, a)
    path.write_text(path.read_text() + "\naxolotl\n")
    assert history.rows(session, history.HistoryFilter(q="axolotl")) == []
    notes.refresh_all(session, cfg)
    assert [r.paper_id for r in history.rows(session, history.HistoryFilter(q="axolotl"))] == [a.id]
