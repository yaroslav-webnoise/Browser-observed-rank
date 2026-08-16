from app import DEFAULT_MAX_PAGES, should_continue_scraping, normalize_domain, build_google_page_url


def test_default_max_pages_is_five():
    assert DEFAULT_MAX_PAGES == 5


def test_should_continue_scraping_for_short_pages():
    assert should_continue_scraping(page_results=[{"link": "https://example.com"}] * 3, page_num=0, max_pages=10) is False


def test_should_continue_scraping_for_full_pages():
    assert should_continue_scraping(page_results=[{"link": "https://example.com"}] * 10, page_num=0, max_pages=10) is True


def test_normalize_domain_strips_www_and_scheme():
    assert normalize_domain("https://www.example.com/path") == "example.com"


def test_google_page_url_uses_pagination_when_start_set():
    url = build_google_page_url("digital agency", "il", "he", "Tel Aviv", "google.co.il", start=20)
    assert "start=20" in url
    assert "uule=" in url
