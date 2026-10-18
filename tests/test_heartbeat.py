from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts.build_heartbeat import build, parse_calendar, render_heartbeat, SVG_NS


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/heartbeat-template.svg"


def response(entries, total=None):
    return {"data": {"user": {"contributionsCollection": {"contributionCalendar": {
        "totalContributions": (
            sum(entry["contributionCount"] for entry in entries) if total is None else total
        ),
        "weeks": [{"contributionDays": entries}],
    }}}}}


def entry(day, count):
    return {"date": day, "contributionCount": count}


class HeartbeatTests(unittest.TestCase):
    def test_complete_calendar_preserves_zero_and_leap_day(self):
        entries = [
            entry("2024-02-28", 3), entry("2024-02-29", 0), entry("2024-03-01", 5)
        ]
        days = parse_calendar(response(entries), date(2024, 2, 28), date(2024, 3, 1))
        self.assertEqual(sum(days.values()), 8)
        self.assertEqual(days[date(2024, 2, 29)], 0)

    def test_incomplete_duplicate_and_out_of_range_days_are_rejected(self):
        examples = [
            [entry("2026-01-01", 1)],
            [entry("2026-01-01", 1), entry("2026-01-01", 2)],
            [entry("2026-01-01", 1), entry("2026-01-03", 2)],
        ]
        for entries in examples:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                parse_calendar(response(entries), date(2026, 1, 1), date(2026, 1, 2))

    def test_invalid_counts_and_totals_are_rejected(self):
        for count in [-1, True, "3", 1.5]:
            with self.subTest(count=count), self.assertRaises(ValueError):
                parse_calendar(
                    response([entry("2026-01-01", count)], total=3),
                    date(2026, 1, 1), date(2026, 1, 1),
                )
        with self.assertRaises(ValueError):
            parse_calendar(
                response([entry("2026-01-01", 3)], total=4),
                date(2026, 1, 1), date(2026, 1, 1),
            )

    def test_graphql_errors_and_malformed_calendars_are_rejected(self):
        examples = [
            None, {}, {"errors": [{"message": "rate limited"}]},
            {"data": {"user": None}},
            {"data": {"user": {"contributionsCollection": {
                "contributionCalendar": {"totalContributions": 0, "weeks": [None]}
            }}}},
        ]
        for payload in examples:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_calendar(payload, date(2026, 1, 1), date(2026, 1, 1))

    def test_week_crossing_new_year_keeps_all_contributions_and_theme(self):
        first = date(2025, 12, 28)
        days = {first + timedelta(days=i): i for i in range(9)}
        root = ET.fromstring(render_heartbeat(days, TEMPLATE.read_text()))
        line = root.find(f".//*[@id='activity-line']")
        # Dec 28..Jan 3 is one week, Jan 4..5 is the second; not three yearly bins.
        self.assertEqual(len(line.get("points").split()), 2)
        self.assertEqual(root.find(".//*[@id='activity-total']").text, "36 contributions")
        self.assertIn("2026-01-05", root.get("aria-label"))
        original = ET.parse(TEMPLATE).getroot()
        self.assertEqual(
            root.find(f"{{{SVG_NS}}}style").text,
            original.find(f"{{{SVG_NS}}}style").text,
        )
        self.assertEqual(line.get("stroke"), "#b9eee8")
        self.assertEqual(root.find(".//*[@id='activity-area']").get("fill"), "#0e7490")

    def test_zero_activity_and_single_day_produce_valid_svg(self):
        for length in [1, 14]:
            days = {date(2026, 1, 1) + timedelta(days=i): 0 for i in range(length)}
            svg = render_heartbeat(days, TEMPLATE.read_text())
            root = ET.fromstring(svg)
            self.assertNotIn("nan", svg)
            self.assertIn("0 public contributions", root.get("aria-label"))
            points = root.find(".//*[@id='activity-line']").get("points").split()
            self.assertTrue(all(point.endswith(",240.0") for point in points))

    def test_failed_fetch_leaves_previous_svg_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "heartbeat.svg"
            output.write_text("last successful image")
            with patch("scripts.build_heartbeat.fetch_calendar", side_effect=ValueError):
                with self.assertRaises(ValueError):
                    build("owner", "test-token", 2026, date(2026, 1, 1), TEMPLATE, output)
            self.assertEqual(output.read_text(), "last successful image")
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_build_requests_only_elapsed_dates_and_is_idempotent(self):
        def calendar(username, token, start, end):
            return {start + timedelta(days=i): 1 for i in range((end - start).days + 1)}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "heartbeat.svg"
            with patch("scripts.build_heartbeat.fetch_calendar", side_effect=calendar) as fetch:
                self.assertTrue(
                    build("owner", "test-token", 2025, date(2026, 1, 2), TEMPLATE, output)
                )
                self.assertEqual(fetch.call_args.args[-1], date(2026, 1, 2))
                before = output.read_bytes()
                self.assertFalse(
                    build("owner", "test-token", 2025, date(2026, 1, 2), TEMPLATE, output)
                )
                self.assertEqual(output.read_bytes(), before)

    def test_profile_assets_and_local_html_links_exist(self):
        class References(HTMLParser):
            def __init__(self):
                super().__init__()
                self.references = []

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                key = {"img": "src", "a": "href"}.get(tag)
                if key and key in values:
                    self.references.append(values[key])

        for path in (ROOT / "assets").glob("*.svg"):
            with self.subTest(asset=path.name):
                ET.parse(path)
        for path in ROOT.glob("*.md"):
            parser = References()
            parser.feed(path.read_text())
            for reference in parser.references:
                if reference.startswith(("https://", "http://", "mailto:", "#")):
                    continue
                with self.subTest(page=path.name, reference=reference):
                    self.assertTrue((path.parent / reference.split("#")[0]).is_file())


if __name__ == "__main__":
    unittest.main()
