from nso_monitor.common.html import extract_links, is_generic_label, looks_like_spa


def test_looks_like_spa_detects_empty_app_shell():
    assert looks_like_spa('<html><body><div id="root"></div><script>x</script></body></html>') is True
    assert looks_like_spa("<html><body><p>" + "word " * 200 + "</p></body></html>") is False


def test_is_generic_label():
    assert is_generic_label("Download") is True
    assert is_generic_label("تحميل") is True  # تحميل
    assert is_generic_label("Labour Force Survey 2024") is False


def test_extract_links_prefers_row_text_over_generic_link_label():
    html = """
    <html><head><title>Social Statistics</title></head><body>
    <h1>Social Statistics</h1>
    <table>
      <tr><td>Household Health Survey 2023</td><td><a href="/files/health_2023.pdf">Download PDF</a></td></tr>
      <tr><td>Consumer Price Index 2024</td><td><a href="/files/cpi_2024.pdf">Download</a></td></tr>
    </table>
    </body></html>"""
    title, links = extract_links(html, "https://example.org/social/index.html")
    assert title == "Social Statistics"
    files = {l["url"]: l for l in links if l["kind"] == "file"}
    health = files["https://example.org/files/health_2023.pdf"]
    assert health["title"] == "Household Health Survey 2023"   # not "Download PDF"
    assert health["heading"] == "Social Statistics"
    assert health["format"] == "PDF"


def test_extract_links_resolves_relative_urls_and_skips_junk():
    html = '<a href="page.html">Page</a><a href="mailto:x@y.com">Mail</a><a href="#top">Top</a>'
    _, links = extract_links(html, "https://example.org/dir/")
    urls = [l["url"] for l in links]
    assert "https://example.org/dir/page.html" in urls
    assert not any("mailto" in u for u in urls)
    assert len(links) == 1
