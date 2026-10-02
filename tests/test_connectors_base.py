import io
import json

from openpyxl import Workbook

from nso_monitor.agent2.connectors.base import coverage_of, parse_table_file


def test_parse_csv_utf8():
    content = "name,year,value\nHealth,2020,5\nHealth,2021,7\n".encode("utf-8-sig")
    sheets = parse_table_file(content, "CSV", "https://x.org/f.csv")
    assert len(sheets) == 1
    assert sheets[0]["rows"] == [["name", "year", "value"], ["Health", "2020", "5"], ["Health", "2021", "7"]]


def test_parse_csv_arabic_windows1256_encoding():
    text = "الاسم,القيمة\nالقاهرة,10\n"
    content = text.encode("cp1256")
    sheets = parse_table_file(content, "CSV", "https://x.org/f.csv")
    assert sheets[0]["rows"][0][0] == "الاسم"  # الاسم, decoded correctly, not mangled


def test_parse_json_list_of_dicts_becomes_a_table():
    # "note" is missing on the first row and not trailing (year comes after it in the union header),
    # so it must show up as a real gap rather than being silently trimmed like a trailing empty cell.
    data = [{"year": 2020, "note": None, "value": 5}, {"year": 2021, "note": "revised", "value": 7}]
    sheets = parse_table_file(json.dumps(data).encode(), "JSON", "https://x.org/f.json")
    rows = sheets[0]["rows"]
    assert rows[0] == ["year", "note", "value"]  # header is the union of keys, in first-seen order
    assert rows[1] == [2020, "", 5]   # missing value -> "" (uniform with CSV/Excel), not silently dropped
    assert rows[2] == [2021, "revised", 7]


def test_parse_xlsx_multiple_sheets():
    wb = Workbook()
    wb.active.title = "Data"
    wb.active.append(["year", "value"])
    wb.active.append([2020, 5])
    wb.create_sheet("Notes").append(["a note"])
    buf = io.BytesIO()
    wb.save(buf)
    sheets = parse_table_file(buf.getvalue(), "XLSX", "https://x.org/f.xlsx")
    names = {s["sheet"]: s["rows"] for s in sheets}
    assert names["Data"] == [["year", "value"], [2020, 5]]
    assert names["Notes"] == [["a note"]]


def test_parse_table_file_trims_trailing_empty_cells_and_rows():
    content = "a,b,\n,,\n1,2,\n".encode()
    sheets = parse_table_file(content, "CSV", "https://x.org/f.csv")
    assert sheets[0]["rows"] == [["a", "b"], ["1", "2"]]  # the all-empty middle row is dropped


def test_coverage_of():
    assert coverage_of([]) == ""
    assert coverage_of(["2021", "2020", "2019"]) == "2019-2021"
    assert coverage_of(["2020"]) == "2020"
