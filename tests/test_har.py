from pathlib import Path

from apiforge import har

FIXTURE = Path(__file__).parent / "fixtures" / "sample.har"


def test_filters_noise_and_groups_endpoints():
    eps = har.summarize_har(FIXTURE)
    keys = {(e["method"], e["path_template"]) for e in eps}
    assert keys == {
        ("GET", "/reststop/search"),
        ("GET", "/api/notes/{id}"),
        ("POST", "/graphql"),
    }
    notes = next(e for e in eps if e["path_template"] == "/api/notes/{id}")
    assert notes["calls"] == 2


def test_query_params_collected():
    search = next(e for e in har.summarize_har(FIXTURE) if e["path_template"] == "/reststop/search")
    assert search["query_params"]["keyword"] == ["coffee", "tea"]


def test_secrets_are_redacted():
    text = FIXTURE.read_text()
    assert "SECRET123" in text  # sanity: the raw capture has it
    summary = str(har.summarize_har(FIXTURE))
    assert "SECRET123" not in summary
    assert "abc.def" not in summary
    assert "<REDACTED cookie" in summary


def test_large_arrays_are_trimmed():
    search = next(e for e in har.summarize_har(FIXTURE) if e["path_template"] == "/reststop/search")
    ex = search["response_example"]
    assert len(ex) == 4 and "more items" in ex[-1]


def test_path_template():
    assert har.path_template("/a/123/b/0f8fad5b-d9cb-469f-a165-70867728950e") == "/a/{id}/b/{id}"
