from nso_monitor.common.text import clean_text, content_years, coverage, extract_years, file_format, guess_frequency, period_label


def test_clean_text():
    assert clean_text("<p>Hello  <b>world</b></p>\n\n") == "Hello world"
    assert clean_text("A &amp; B") == "A & B"
    assert clean_text(None) == ""
    truncated = clean_text("one two three four five", max_len=12)
    assert truncated.endswith("...") and len(truncated) <= 15


def test_file_format():
    assert file_format("https://x.org/file.PDF") == "PDF"
    assert file_format("https://x.org/data.xlsx?token=1") == "XLSX"
    assert file_format("https://x.org/page") == ""
    assert file_format(None) == ""


def test_extract_years_plain_range_and_arabic_digits():
    assert extract_years("Census 2017 results") == [2017]
    assert extract_years("Yearbook 2019/20") == [2019, 2020]
    assert extract_years("Report 2015-2018 and update 2021") == [2015, 2018, 2021]
    assert extract_years("Arabic year ٢٠٢٤") == [2024]
    assert extract_years("no year here") == []


def test_guess_frequency():
    assert guess_frequency("Quarterly Labour Force Survey") == "Quarterly"
    assert guess_frequency("النشرة السنوية") == "Annual"
    assert guess_frequency("nothing here") == ""


def test_period_label():
    assert period_label(2024) == "2024"
    assert period_label(2024, quarter=3) == "2024-Q3"
    assert period_label(2024, month=7) == "2024-07"
    assert period_label(0) == "" and period_label(None) == ""


def test_coverage():
    assert coverage([]) == ""
    assert coverage([2020]) == "2020"
    assert coverage([2015, 2018, 2021]) == "2015-2021"


def test_content_years_prefers_title_over_wordpress_upload_path():
    # Real bug this guards: a file titled "...for year 2007" hosted at a WordPress upload path
    # /uploads/2023/06/... must report 2007, not a false "2007-2023" contaminated by the upload date.
    title = "الإحصاءات الحيوية لسنة 2007"
    url = "https://bsc.ly/wp-content/uploads/2023/06/vital-statistics.pdf"
    assert content_years(title, "", url) == [2007]


def test_content_years_falls_back_to_url_when_title_has_no_year():
    assert content_years("Vital statistics bulletin", "", "https://x.org/files/report_2019.pdf") == [2019]


def test_content_years_empty_when_nothing_has_a_year():
    assert content_years("Vital statistics bulletin", "", "https://x.org/files/report.pdf") == []
