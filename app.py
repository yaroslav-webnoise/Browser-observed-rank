import streamlit as st
from urllib.parse import urlparse, urlencode
import base64
import subprocess
import sys
import time

DEFAULT_MAX_PAGES = 5

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    import geonamescache
except Exception:
    geonamescache = None


@st.cache_resource(show_spinner="Installing Chromium browser (first run only)…")
def _ensure_chromium() -> tuple[bool, str]:
    """Download the Playwright Chromium binary if it isn't present yet."""
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Location helpers
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86400)
def get_city_options(gl_country: str, limit: int = 500) -> list[str]:
    if geonamescache is None:
        return ["No location"]

    gl_to_iso2 = {
        "il": "IL", "us": "US", "uk": "GB", "ca": "CA",
        "au": "AU", "de": "DE", "fr": "FR",
    }
    target_iso2 = gl_to_iso2.get(gl_country, gl_country.upper())

    gc = geonamescache.GeonamesCache()
    countries = gc.get_countries()
    cities = gc.get_cities()
    country_name = countries.get(target_iso2, {}).get("name", target_iso2)

    filtered = []
    for city in cities.values():
        if city.get("countrycode") != target_iso2:
            continue
        population = int(city.get("population") or 0)
        if population <= 0:
            continue
        city_name = (city.get("name") or "").strip()
        if city_name:
            filtered.append((population, city_name))

    filtered.sort(key=lambda item: item[0], reverse=True)
    seen = set()
    options = ["No location"]
    for _, city_name in filtered:
        label = f"{city_name}, {country_name}"
        if label not in seen:
            seen.add(label)
            options.append(label)
        if len(options) >= limit + 1:
            break
    return options


# UULE v1 encoding — encodes a location name for the Google uule= URL parameter.
_UULE_SECRET = [
    186, 12, 107, 67, 190, 237, 68, 60, 23, 236, 199, 55, 211, 8, 109, 87,
    3, 188, 141, 50, 80, 240, 12, 125, 19, 7, 195, 12, 39, 84, 21, 168,
]

def encode_uule(location_name: str) -> str:
    if not location_name:
        return ""
    loc_bytes = location_name.encode("utf-8")
    prefix_byte = _UULE_SECRET[len(loc_bytes) % len(_UULE_SECRET)]
    payload = bytes([prefix_byte]) + loc_bytes
    encoded = base64.b64encode(payload).decode().rstrip("=")
    return f"w+CAIQICIb{encoded}"


def build_google_url(query: str, gl: str, hl: str, location: str, google_domain: str) -> str:
    params = {
        "q": query,
        "num": "100",
        "gl": gl,
        "hl": hl,
        "pws": "0",    # disable personalised results
        "nfpr": "1",   # no fuzzy query rewriting
        "filter": "0", # don't omit similar results
    }
    if location:
        uule = encode_uule(location)
        if uule:
            params["uule"] = uule
    return f"https://www.{google_domain}/search?{urlencode(params)}"



def build_google_page_url(
    query: str, gl: str, hl: str, location: str, google_domain: str, start: int = 0
) -> str:
    params = {
        "q": query,
        "gl": gl,
        "hl": hl,
        "pws": "0",
        "nfpr": "1",
        "filter": "0",
    }
    if start:
        params["start"] = str(start)
    if location:
        uule = encode_uule(location)
        if uule:
            params["uule"] = uule
    return f"https://www.{google_domain}/search?{urlencode(params)}"


def is_google_captcha_page(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return "google.com/sorry" in lowered or "/sorry/index" in lowered


def _wait_for_captcha_resolution(page, timeout_seconds: int = 600) -> bool:
    """Wait while the user manually solves a Google CAPTCHA in the visible Chrome window."""
    deadline = time.monotonic() + timeout_seconds
    last_message = None
    while time.monotonic() < deadline:
        current_url = page.url
        if not is_google_captcha_page(current_url):
            return True
        if last_message != current_url:
            last_message = current_url
            st.warning(
                "Google is showing a CAPTCHA. Please solve it in the Chrome window, then the app will continue automatically."
            )
        time.sleep(2)
    return False


def _page_contains_target_match(page_results: list[dict] | None, target_domain: str, match_mode: str) -> bool:
    if not page_results or not target_domain:
        return False

    normalized_target = normalize_domain(target_domain)
    for result in page_results:
        link = result.get("link", "") or ""
        if match_mode == "Domain (any page on domain)":
            if domains_match(normalized_target, normalize_domain(link)):
                return True
        elif match_mode == "Exact URL (homepage/page only)":
            if exact_url_match(target_domain, link):
                return True
    return False


def should_continue_scraping(
    page_results: list[dict] | None,
    page_num: int,
    max_pages: int,
    min_results_per_page: int = 7,
) -> bool:
    """Stop paging early when Google gives a short final page or we have exhausted the requested window."""
    if page_num >= max_pages:
        return False
    if not page_results:
        return False
    if len(page_results) < min_results_per_page:
        return False
    return page_num + 1 < max_pages


_EXTRACT_JS = """() => {
    const results = [];
    const seen = new Set();
    const container = document.querySelector('#rso') || document.querySelector('#search') || document.body;
    container.querySelectorAll('a[href]').forEach(a => {
        const href = a.href;
        if (!href || !href.startsWith('http') || seen.has(href)) return;

        // Skip Google-internal and ad redirect URLs.
        if (href.includes('/aclk?') || href.includes('googleadservices.com') ||
            href.includes('googlesyndication.com')) return;
        try {
            const u = new URL(href);
            if (u.hostname.includes('google.') || u.hostname.endsWith('goo.gl') ||
                u.hostname.endsWith('g.page')) return;
        } catch(e) { return; }

        // Skip links inside known ad or non-organic containers.
        if (a.closest('[data-text-ad]') || a.closest('.ads-ad') ||
            a.closest('[aria-label="Ads"]') || a.closest('.commercial-unit-desktop-top')) return;

        // Skip "People also ask" and similar expandable boxes.
        if (a.closest('[data-q]') || a.closest('.related-question-pair') ||
            a.closest('[jsname="yEVEwb"]')) return;

        // Must have an associated h3 heading — the hallmark of an organic result.
        const h3 = a.querySelector('h3')
            || a.closest('[data-hveid], .g, [jscontroller]')?.querySelector('h3');
        if (!h3) return;

        seen.add(href);
        const parent = a.closest('[data-hveid]') || a.closest('.g') || a.parentElement;
        const snippetEl = parent
            ? (parent.querySelector('[data-sncf="1"]') ||
               parent.querySelector('.VwiC3b') ||
               parent.querySelector('[style*="-webkit-line-clamp"]'))
            : null;
        results.push({
            link: href,
            title: h3.innerText.trim(),
            snippet: snippetEl ? snippetEl.innerText.trim() : '',
        });
    });
    return results;
}"""


# ---------------------------------------------------------------------------
# Google scraper
# ---------------------------------------------------------------------------

def _get_proxy() -> dict | None:
    """Read proxy URL from Streamlit secrets, e.g. PROXY_URL = 'http://user:pass@host:port'."""
    try:
        raw = st.secrets.get("PROXY_URL", "")
        if raw:
            return {"server": raw}
    except Exception:
        pass
    return None


import os

# Persist cookies/profile between searches so a solved CAPTCHA stays valid.
_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".browser_rank_profile")

_CONTEXT_KWARGS = lambda hl, gl: dict(
    viewport={"width": 1280, "height": 900},
    locale=f"{hl}-{gl.upper()}",
    extra_http_headers={"Accept-Language": f"{hl}-{gl.upper()},{hl};q=0.9,en;q=0.8"},
)

_CONSENT_SELECTORS = [
    "button#L2AGLb", "button#W0wltc", "button[jsname='higCR']",
    "button[jsname='b3VHJd']", "button[jsname='tWT92d']",
    "form[action*='consent'] button", "div[role='none'] button",
]


def _dismiss_consent(page) -> None:
    for sel in _CONSENT_SELECTORS:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_load_state("load", timeout=8_000)
                return
        except Exception:
            pass


def _extract_page_results(page) -> list[dict]:
    try:
        page.wait_for_selector("h3", timeout=10_000)
    except Exception:
        return []
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(1000)
    return page.evaluate(_EXTRACT_JS)


def scrape_google(
    query: str,
    gl: str,
    hl: str,
    location: str,
    google_domain: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    target_domain: str | None = None,
    match_mode: str | None = None,
) -> dict:
    proxy = _get_proxy()
    first_url = build_google_page_url(query, gl, hl, location, google_domain, start=0)

    with sync_playwright() as p:
        if proxy:
            browser = p.chromium.launch(
                headless=True, proxy=proxy,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(**_CONTEXT_KWARGS(hl, gl))
        else:
            try:
                context = p.chromium.launch_persistent_context(
                    _PROFILE_DIR, headless=False, channel="chrome", **_CONTEXT_KWARGS(hl, gl)
                )
            except Exception:
                context = p.chromium.launch_persistent_context(
                    _PROFILE_DIR, headless=False,
                    args=["--no-sandbox"], **_CONTEXT_KWARGS(hl, gl)
                )

        page = context.new_page()

        try:
            page.goto(first_url, wait_until="load", timeout=30_000)
        except Exception as e:
            context.close()
            raise RuntimeError(f"Failed to load Google: {e}") from e

        _dismiss_consent(page)

        page_url_after = page.url
        page_title = page.title()

        if is_google_captcha_page(page_url_after):
            st.warning(
                "Google is showing a CAPTCHA. Please solve it in the Chrome window, then the app will continue automatically."
            )
            if not _wait_for_captcha_resolution(page, timeout_seconds=600):
                context.close()
                raise RuntimeError("Google CAPTCHA was not solved in time. Please retry after completing the verification.")
            page_url_after = page.url
            page_title = page.title()

        all_raw: list[dict] = []
        for page_num in range(max_pages):
            if page_num > 0:
                next_url = build_google_page_url(
                    query, gl, hl, location, google_domain, start=page_num * 10
                )
                try:
                    page.goto(next_url, wait_until="load", timeout=20_000)
                except Exception:
                    break

            page_raw = _extract_page_results(page)
            if not page_raw:
                break
            all_raw.extend(page_raw)
            if target_domain and match_mode and _page_contains_target_match(page_raw, target_domain, match_mode):
                break
            if not should_continue_scraping(page_raw, page_num, max_pages):
                break

        fetched_at = int(time.time())
        page.close()
        context.close()

    organic = [
        {"position": i + 1, "link": r["link"], "title": r["title"], "snippet": r["snippet"]}
        for i, r in enumerate(all_raw)
    ]
    return {
        "organic": organic,
        "_fetched_at": fetched_at,
        "_search_url": first_url,
        "_page_title": page_title,
        "_page_url_after": page_url_after,
        "_got_h3": bool(all_raw),
    }


# ---------------------------------------------------------------------------
# Domain / URL helpers  (identical logic to the original app)
# ---------------------------------------------------------------------------

def normalize_domain(raw_value: str) -> str:
    value = (raw_value or "").strip().lower()
    if not value:
        return ""
    if value.startswith("[") and "](" in value and value.endswith(")"):
        try:
            value = value.split("](", 1)[1][:-1]
        except Exception:
            pass
    value = value.strip("[]() ")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    domain = (parsed.netloc or parsed.path.split("/")[0]).strip().lower()
    return domain.replace("www.", "").strip("/")


def normalize_url_for_match(raw_value: str) -> str:
    value = (raw_value or "").strip().lower()
    if not value:
        return ""
    if value.startswith("[") and "](" in value and value.endswith(")"):
        try:
            value = value.split("](", 1)[1][:-1]
        except Exception:
            pass
    value = value.strip("[]() ")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    domain = (parsed.netloc or "").lower().replace("www.", "")
    path = (parsed.path or "").rstrip("/")
    return f"{domain}{path}"


def is_google_map_or_utility(link: str, clean_domain: str) -> bool:
    lower_link = (link or "").lower()
    path = urlparse(lower_link).path
    return (
        "google." in clean_domain
        or "maps.app.goo.gl" in clean_domain
        or "g.page" in clean_domain
        or "/maps" in path
        or "/place/" in path
        or "/search" in path
    )


def domains_match(target: str, found: str) -> bool:
    return target == found or found.endswith(f".{target}")


def exact_url_match(target_url: str, found_url: str) -> bool:
    return normalize_url_for_match(target_url) == normalize_url_for_match(found_url)


def is_homepage_url(raw_url: str) -> bool:
    normalized = normalize_url_for_match(raw_url)
    if not normalized:
        return False
    return "/" not in normalized




def main() -> None:
    st.set_page_config(page_title="SEO Rank Checker", page_icon="📈")
    st.title("🔍 Google Rank Tracker")
    st.write("Find the exact organic position of any website on Google — scraped directly from Google.")

    if not PLAYWRIGHT_AVAILABLE:
        st.error(
            "Playwright is not installed. Run: `pip install playwright` then `playwright install chromium`"
        )
        st.stop()

    ok, install_log = _ensure_chromium()
    if not ok:
        st.error("Failed to install Chromium.")
        st.code(install_log, language="text")
        st.stop()

    with st.sidebar:
        st.header("⚙️ Target Market")
        country = st.selectbox("Google Country (gl)", ["il", "us", "uk", "ca", "au", "de", "fr"], index=0)
        language = st.selectbox("Google Language (hl)", ["he", "en", "ar", "fr", "de", "es"], index=0)

        default_google_domain_by_gl = {
            "il": "google.co.il", "us": "google.com", "uk": "google.co.uk",
            "ca": "google.ca", "au": "google.com.au", "de": "google.de", "fr": "google.fr",
        }
        google_domain_options = [
            "google.com", "google.co.il", "google.co.uk",
            "google.ca", "google.com.au", "google.de", "google.fr",
        ]
        google_domain = st.selectbox(
            "Google Domain",
            google_domain_options,
            index=google_domain_options.index(default_google_domain_by_gl.get(country, "google.com")),
            help="Match the Google host used in your browser.",
        )

        location_options = get_city_options(country)
        if geonamescache is None:
            st.caption("Install geonamescache to enable full city list.")
        selected_location = st.selectbox(
            "Location (city)",
            location_options,
            index=0,
            help="Choose a city to match browser results more closely.",
        )
        location = "" if selected_location == "No location" else selected_location

        strict_homepage_mode = st.checkbox(
            "Strict homepage mode",
            value=False,
            help="When enabled, only counts a match if the homepage/root URL (/) ranks. Inner pages are ignored.",
        )

        st.subheader("📍 Local Pack Exclusion")
        use_manual_local_exclusion = st.checkbox("Set map/business count manually", value=False)
        manual_local_count = st.number_input(
            "Non-sponsored map/business results above organic",
            min_value=0, max_value=20, value=0, step=1,
            help="Subtract map-pack entries that appear above the organic list.",
        )

        page_count = st.selectbox(
            "Pages to scan",
            list(range(1, 11)),
            index=DEFAULT_MAX_PAGES - 1,
            help="How many Google result pages to inspect before stopping. Default is 5.",
        )

    keyword = st.text_input("Enter Keyword", placeholder="e.g., digital agency")
    target_domain = st.text_input("Enter Target Domain", placeholder="e.g., limedigital.co.il")
    match_mode = st.radio(
        "Match Mode",
        ["Domain (any page on domain)", "Exact URL (homepage/page only)"],
        horizontal=True,
    )
    normalized_target_domain = normalize_domain(target_domain)

    if st.button("Check Ranking", type="primary"):
        if not keyword or not target_domain:
            st.warning("Please fill in both the Keyword and Target Domain fields.")
        elif not normalized_target_domain:
            st.warning("Please enter a valid target domain or URL.")
        else:
            with st.spinner("Opening browser and fetching Google results…"):
                try:
                    data = scrape_google(
                        keyword.strip(),
                        country,
                        language,
                        location,
                        google_domain,
                        max_pages=page_count,
                        target_domain=target_domain,
                        match_mode=match_mode,
                    )
                except Exception as e:
                    st.error(f"An error occurred: {e}")
                    st.stop()

            fetched_at_epoch = data.get("_fetched_at")
            fetched_at_text = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(fetched_at_epoch))
                if isinstance(fetched_at_epoch, int) else "unknown"
            )
            search_url = data.get("_search_url", "")
            organic_results = data.get("organic", [])

            if not organic_results:
                page_url_after = data.get("_page_url_after", "")
                if "google.com/sorry" in page_url_after:
                    st.error("🚫 Google blocked the request (CAPTCHA / datacenter IP detected).")
                    st.warning(
                        "Streamlit Cloud's IP is flagged as a bot by Google. "
                        "To fix this, add a **residential proxy URL** to your app's Secrets:\n\n"
                        "```\nPROXY_URL = \"http://user:pass@proxy-host:port\"\n```\n\n"
                        "Any residential proxy service (e.g. Bright Data, Oxylabs, IPRoyal) works. "
                        "Alternatively, run the app **locally** — your home IP won't be blocked."
                    )
                else:
                    st.error("No organic results were scraped.")
                    st.caption(f"Page title: {data.get('_page_title', '?')}")
                    st.caption(f"Final URL: {page_url_after}")
                    st.caption(f"Found any h3 elements: {data.get('_got_h3', False)}")
                if search_url:
                    st.caption(f"Search URL: {search_url}")
                st.stop()

            st.caption(f"Scraped {len(organic_results)} organic results — {fetched_at_text}")
            if search_url:
                st.caption(f"Search URL: {search_url}")

            local_business_count = int(manual_local_count) if use_manual_local_exclusion else 0

            found = False
            visual_rank = 0
            first_domain_match = None
            first_domain_match_rank = None
            first_domain_match_api_pos = None
            homepage_domain_match = None
            homepage_domain_match_rank = None
            homepage_domain_match_api_pos = None
            same_domain_result = None
            selected = None
            selected_rank = None
            selected_api_pos = None

            for result in organic_results:
                result_url = result.get("link", "")
                clean_domain = normalize_domain(result_url)
                if not clean_domain:
                    continue

                api_position = result.get("position", visual_rank + 1)

                if is_google_map_or_utility(result_url, clean_domain):
                    continue

                visual_rank += 1

                is_match = domains_match(normalized_target_domain, clean_domain)
                if same_domain_result is None and is_match:
                    same_domain_result = result

                if match_mode == "Domain (any page on domain)" and is_match:
                    if first_domain_match is None:
                        first_domain_match = result
                        first_domain_match_rank = visual_rank
                        first_domain_match_api_pos = api_position
                    if homepage_domain_match is None and is_homepage_url(result_url):
                        homepage_domain_match = result
                        homepage_domain_match_rank = visual_rank
                        homepage_domain_match_api_pos = api_position
                    continue

                if match_mode == "Exact URL (homepage/page only)":
                    is_match = exact_url_match(target_domain, result_url)

                if is_match:
                    raw_organic_rank = api_position
                    adjusted_rank = max(1, raw_organic_rank - local_business_count)
                    organic_page_number = ((adjusted_rank - 1) // 10) + 1
                    organic_page_position = ((adjusted_rank - 1) % 10) + 1
                    matched_url = result.get("link", "")
                    matched_is_homepage = is_homepage_url(matched_url)

                    st.balloons()
                    st.success(f"🎯 **Match Found at Adjusted Organic Position {adjusted_rank}!**")
                    st.caption(f"Raw organic position: {raw_organic_rank}")
                    st.caption(f"Page {organic_page_number}, place {organic_page_position} on that page")
                    if use_manual_local_exclusion:
                        st.caption(f"Local businesses subtracted (manual): {local_business_count}")
                    st.caption(
                        "Matched URL type: "
                        f"{'homepage/root' if matched_is_homepage else 'inner page'}"
                    )
                    st.caption(
                        f"Context: gl={country}, hl={language}, domain={google_domain}, "
                        f"location={location.strip() or 'not set'}"
                    )
                    st.info(
                        f"**Title:** {result.get('title')}\n\n"
                        f"**URL:** [{result.get('link')}]({result.get('link')})"
                    )
                    found = True
                    break

            if not found and match_mode == "Domain (any page on domain)" and first_domain_match is not None:
                if strict_homepage_mode and homepage_domain_match is None:
                    selected = selected_rank = selected_api_pos = None
                else:
                    selected = homepage_domain_match or first_domain_match
                    selected_rank = homepage_domain_match_rank or first_domain_match_rank
                    selected_api_pos = homepage_domain_match_api_pos or first_domain_match_api_pos

            if not found and match_mode == "Domain (any page on domain)" and selected is not None:
                adjusted_rank = max(1, selected_api_pos - local_business_count)
                organic_page_number = ((adjusted_rank - 1) // 10) + 1
                organic_page_position = ((adjusted_rank - 1) % 10) + 1

                st.balloons()
                st.success(f"🎯 **Match Found at Adjusted Organic Position {adjusted_rank}!**")
                st.caption(f"Raw organic position: {selected_api_pos}")
                st.caption(f"Page {organic_page_number}, place {organic_page_position} on that page")
                if use_manual_local_exclusion:
                    st.caption(f"Local businesses subtracted (manual): {local_business_count}")
                st.caption(
                    f"Context: gl={country}, hl={language}, domain={google_domain}, "
                    f"location={location.strip() or 'not set'}"
                )
                st.info(
                    f"**Title:** {selected.get('title')}\n\n"
                    f"**URL:** [{selected.get('link')}]({selected.get('link')})"
                )
                if homepage_domain_match is None:
                    st.caption("Homepage URL not found; showing first matching page on the domain.")
                else:
                    st.caption("Homepage URL found and preferred.")
                found = True

            if not found:
                if strict_homepage_mode and same_domain_result is not None:
                    st.warning(f"Homepage '{target_domain}' not found in organic results for '{keyword}'.")
                    st.info(
                        f"Domain was found but not the homepage. "
                        f"Closest match: {same_domain_result.get('link')} "
                        f"(position {same_domain_result.get('position')})."
                    )
                elif match_mode == "Exact URL (homepage/page only)" and same_domain_result is not None:
                    st.warning(f"Exact URL '{target_domain}' not found in organic results for '{keyword}'.")
                    st.info(
                        f"Domain was found but not the exact URL. "
                        f"Closest match: {same_domain_result.get('link')} "
                        f"(position {same_domain_result.get('position')})."
                    )
                else:
                    st.error(f"❌ '{target_domain}' was not found in the organic results for '{keyword}'.")


if __name__ == "__main__":
    main()
