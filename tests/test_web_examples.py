"""Web UI: the example lists for Semantic Scholar and the libraries used for recommendations."""

import re
from datetime import date

from sqlmodel import Session, select

from conftest import make_work, profile_cfg
from litrad import examples, libraries
from litrad.db import ExampleChoice, Library, Paper
from test_libraries import site  # noqa: F401  (fixture: empty profile)
from test_web import app_env, db, paper_id, post  # noqa: F401  (fixture: filled profile)

BARKLEY = "10.1038/s41588-022-01141-9"
BASSEZ = "10.1038/s41591-021-01323-8"


def example_url(client, kind: str) -> str:
    response = client.get(f"/library/examples/{kind}", follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"]


def labels_ok(html: str) -> None:
    """Every visible form field has a label (for= or aria-label or a wrapping label)."""
    ids = set(re.findall(r'<label[^>]*for="([^"]+)"', html))
    for field in re.findall(r"<(?:input|select|textarea)\b[^>]*>", html):
        if 'type="hidden"' in field:
            continue
        found = re.search(r'id="([^"]+)"', field)
        if "aria-label" in field or (found and found.group(1) in ids):
            continue
        # checkboxes inside <label>…</label>
        start = html.index(field)
        assert html.rfind("<label", 0, start) > html.rfind("</label>", 0, start), field


def test_pages_render_for_an_empty_profile(site):  # noqa: F811
    client, _ = site
    overview = client.get("/library").text
    assert "Examples for Semantic Scholar" in overview
    assert "Positive examples" in overview and "Negative examples" in overview
    assert "Used for recommendations" not in overview  # no library to choose yet
    for kind in ("positive", "negative"):
        page = client.get(example_url(client, kind)).text
        assert "How this list is chosen" in page and "This list is empty" in page
        assert "Delete library" not in page and "Import file" not in page
        labels_ok(page)
    assert "No libraries yet" in client.get(example_url(client, "positive")).text
    assert client.get("/library/examples/neutral").status_code == 404


def test_overview_and_list_of_a_filled_profile(app_env):  # noqa: F811
    client, path, _ = app_env
    overview = client.get("/library").text
    assert "Used for recommendations" in overview and "No library used" not in overview
    assert re.search(r"<b>\d+</b>/100 papers", overview)
    assert "Rules: liked → newest" in overview
    labels_ok(overview)
    url = example_url(client, "positive")
    page = client.get(url).text
    assert "rule: newest" in page and "skipped (no DOI)" in page
    assert "Libraries the library rules choose from" in page
    labels_ok(page)
    export = client.get(f"{url}/export?format=bibtex")
    assert export.status_code == 200 and BARKLEY in export.text.lower()
    assert "rule: newest" in export.text


def test_add_remove_restore_and_reset(app_env):  # noqa: F811
    client, path, _ = app_env
    url = example_url(client, "negative")
    done = post(client, f"{url}/add", {"ref": BARKLEY})
    assert "Added:" in done.text and "added by you" in done.text
    positive = example_url(client, "positive")
    assert BARKLEY not in client.get(positive).text.lower()  # never in both lists
    assert "Enter a DOI" in post(client, f"{url}/add", {"ref": " "}).text
    picked = post(client, f"{url}/add", {"ref": "zzzz qqqq"})  # no match: pick from a search
    assert (
        f"to={url.rsplit('/', 1)[1]}" in str(picked.url) and "Not among your papers" in picked.text
    )

    pid = paper_id(path, doi=BARKLEY)
    done = post(client, f"{url}/remove/{pid}")
    assert (
        "Removed. " in done.text
        and "can restore it" in done.text
        and "This list is empty" in done.text
    )
    assert "Not in this list" in post(client, f"{url}/remove/{pid}").text

    other = paper_id(path, doi=BASSEZ)
    page = post(client, f"{positive}/remove/{other}").text
    assert "Removed by you" in page and "Undo my changes" in page
    assert "You did not remove" in post(client, f"{url}/restore/{other}").text
    page = post(client, f"{positive}/restore/{other}").text
    assert "Restored" in page and "rule: newest" in page
    post(client, f"{positive}/remove/{other}")
    page = post(client, f"{positive}/reset").text
    assert "Undid 1 change." in page and "Removed by you" not in page
    # the generic routes refuse the example lists
    library_id = int(positive.rsplit("/", 1)[1])
    deleted = post(client, f"/library/{library_id}/delete", {"confirm_name": "Positive examples"})
    assert "cannot be deleted" in deleted.text


def test_rules_are_saved_in_the_profile(app_env):  # noqa: F811
    client, path, _ = app_env
    url = example_url(client, "positive")
    data = {"rule": ["cited", "liked"], "order_cited": "1", "order_liked": "2", "count": "3"}
    page = post(client, f"{url}/rules", data).text
    assert "Saved. The list now has" in page
    cfg = profile_cfg(path)
    assert cfg.selection.s2_positive_rules == ["cited", "liked"]
    assert cfg.selection.s2_positive_count == 3
    data = {"rule": ["cited", "liked"], "order_cited": "5", "order_liked": "2", "count": "3"}
    post(client, f"{url}/rules", data)
    assert profile_cfg(path).selection.s2_positive_rules == ["liked", "cited"]
    bad = post(client, f"{url}/rules", {"rule": ["liked"], "count": "0"}).text
    assert "Not saved" in bad and "Number of positive examples" in bad and "at least 1" in bad
    assert "whole number" in post(client, f"{url}/rules", {"count": "x"}).text
    assert (
        "must be a number"
        in post(client, f"{url}/rules", {"rule": ["liked"], "order_liked": "x", "count": "5"}).text
    )
    page = post(client, f"{url}/rules", {"count": "5"}).text
    assert "recommendations are skipped" in page
    negative = example_url(client, "negative")
    post(client, f"{negative}/rules", {"rule": ["low_rated", "dismissed"], "count": "0"})
    assert profile_cfg(path).selection.s2_negative_rules == ["dismissed", "low_rated"]
    assert profile_cfg(path).selection.s2_negative_count == 0


def test_paper_page_buttons(app_env):  # noqa: F811
    client, path, _ = app_env
    pid = paper_id(path, doi=BASSEZ)
    page = client.get(f"/papers/{pid}").text
    assert "Example for Semantic Scholar" in page and "rule: newest" in page
    assert "👎 Negative</button>" in page and "Not an example" in page
    page = post(client, f"/papers/{pid}/example", {"role": "negative"}).text
    assert "Used as negative example" in page and "Negative example" in page
    page = post(client, f"/papers/{pid}/example", {"role": "positive"}).text
    assert "Positive example" in page and "added by you" in page
    page = post(client, f"/papers/{pid}/example", {"role": "none"}).text
    assert "Not used as an example any more" in page and "you removed it" in page
    assert "Choose positive" in post(client, f"/papers/{pid}/example", {"role": "x"}).text
    engine = db(path)
    with Session(engine) as session:
        choices = {c.kind: c.action for c in session.exec(select(ExampleChoice)).all()}
        no_doi = session.exec(select(Paper).where(Paper.doi.is_(None))).first().id
    engine.dispose()
    assert choices == {"positive": "exclude"}
    page = post(client, f"/papers/{no_doi}/example", {"role": "positive"}).text
    assert "has no DOI" in page


def test_choose_the_libraries_used_for_recommendations(app_env):  # noqa: F811
    client, path, _ = app_env
    client.get("/library")  # migrates the old data into "My library"
    other = post(client, "/library/use", {})  # nothing checked
    assert "No library used: similarity is 0" in other.text
    assert "No library used" in client.get("/library").text
    positive = client.get(example_url(client, "positive")).text
    assert "rule: newest" not in positive and "No library used" in positive

    engine = db(path)
    with Session(engine) as session:
        mine = libraries.find(session, libraries.DEFAULT_NAME)
        extra = libraries.create(session, "Extra", use_for_recommendations=False)
        recommended = libraries.recommended_library(session)
        session.commit()
        ids = [mine.id, extra.id, recommended.id]
        every = session.exec(select(Library)).all()
        example_ids = [lib.id for lib in every if lib.kind in examples.KINDS]
    engine.dispose()
    overview = client.get("/library").text
    for library_id in ids:
        assert f'name="library" value="{library_id}"' in overview
    for library_id in example_ids:
        assert f'name="library" value="{library_id}"' not in overview
    labels_ok(overview)
    done = post(client, "/library/use", {"library": [str(i) for i in ids + example_ids]})
    assert "Used for recommendations: " in done.text
    engine = db(path)
    with Session(engine) as session:
        used = {lib.kind: lib.use_for_recommendations for lib in session.exec(select(Library))}
    engine.dispose()
    assert used == {"imported": True, "recommended": True, "positive": False, "negative": False}
    positive = client.get(example_url(client, "positive")).text
    assert "rule: newest" in positive
    back = client.post(
        "/library/use",
        data={"library": [str(mine.id)], "next_url": "/library/1#rules"},
        follow_redirects=False,
    )
    assert back.headers["location"].startswith("/library/1?c=")


def test_evidence_names_the_examples(app_env):  # noqa: F811
    client, _, apis = app_env
    apis.works.append(make_work("10.9/s2", "Found by Semantic Scholar", date.today().isoformat()))
    apis.s2_recommendations = ["10.9/s2"]
    post(client, "/recommendations/build", {"s2": "true"})
    page = client.get("/recommendations").text
    found = re.search(r"from (\d+) <a href=\"/library/examples/positive\">positive</a> / 0", page)
    assert "Semantic Scholar recommendation #1" in page and found and int(found.group(1)) > 0
    how = client.get("/how").text
    assert "/library/examples/positive" in how and "<code>liked</code> → <code>recent</code>" in how
