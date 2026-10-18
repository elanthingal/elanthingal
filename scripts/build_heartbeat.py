#!/usr/bin/env python3
"""Build the profile's weekly contribution heartbeat using GitHub's API."""

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays { date contributionCount }
        }
      }
    }
  }
}
"""


def parse_calendar(payload, start, end):
    """Accept only a complete, consistent calendar for the requested interval."""
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned an invalid response")
    if payload.get("errors"):
        raise ValueError("GitHub rejected the contribution query")
    try:
        calendar = payload["data"]["user"]["contributionsCollection"][
            "contributionCalendar"
        ]
        total = calendar["totalContributions"]
        weeks = calendar["weeks"]
    except (KeyError, TypeError):
        raise ValueError("GitHub returned no contribution calendar") from None
    if type(total) is not int or total < 0 or not isinstance(weeks, list):
        raise ValueError("GitHub returned an invalid contribution calendar")

    days = {}
    for week in weeks:
        if not isinstance(week, dict) or not isinstance(
            week.get("contributionDays"), list
        ):
            raise ValueError("GitHub returned an invalid calendar week")
        for entry in week["contributionDays"]:
            try:
                day = date.fromisoformat(entry["date"])
                count = entry["contributionCount"]
            except (KeyError, TypeError, ValueError):
                raise ValueError("GitHub returned an invalid contribution day") from None
            if type(count) is not int or count < 0:
                raise ValueError("GitHub returned an invalid contribution count")
            if not start <= day <= end:
                raise ValueError("GitHub returned a date outside the requested interval")
            if day in days:
                raise ValueError(f"GitHub returned a duplicate date: {day}")
            days[day] = count

    expected = (end - start).days + 1
    if len(days) != expected:
        raise ValueError(
            f"Incomplete contribution calendar: expected {expected} days, got {len(days)}"
        )
    if sum(days.values()) != total:
        raise ValueError("GitHub's contribution total disagrees with its daily counts")
    return days


def fetch_calendar(username, token, start, end):
    variables = {
        "login": username,
        "from": f"{start.isoformat()}T00:00:00Z",
        "to": f"{end.isoformat()}T23:59:59Z",
    }
    request = Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": variables}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-contribution-heartbeat",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read(2_000_001)
    except HTTPError as error:
        raise ValueError(f"GitHub contribution request failed: HTTP {error.code}") from None
    except (URLError, TimeoutError):
        raise ValueError("GitHub contribution request failed; try again later") from None
    if len(body) > 2_000_000:
        raise ValueError("GitHub contribution response exceeded the size limit")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("GitHub returned invalid JSON") from None
    return parse_calendar(payload, start, end)


def collect_days(username, token, start_year, through):
    days = {}
    for year in range(start_year, through.year + 1):
        start = date(year, 1, 1)
        end = min(date(year, 12, 31), through)
        days.update(fetch_calendar(username, token, start, end))
    return days


def render_heartbeat(days, template):
    """Preserve the template's fonts and theme; replace only chart data."""
    if not days:
        raise ValueError("Cannot render an empty contribution calendar")
    first, last = min(days), max(days)
    if len(days) != (last - first).days + 1:
        raise ValueError("Cannot render an incomplete contribution calendar")
    if any(type(count) is not int or count < 0 for count in days.values()):
        raise ValueError("Cannot render invalid contribution counts")

    root = ET.fromstring(template)
    elements = {element.get("id"): element for element in root.iter()}
    required = {
        "activity-heading", "activity-total", "activity-description",
        "activity-area", "activity-line", "activity-years", "activity-caption",
    }
    if not required <= elements.keys():
        raise ValueError("Heartbeat template is missing its chart elements")
    total = sum(days.values())
    description = (
        f"GitHub contribution heartbeat: {total:,} public contributions from "
        f"{first.isoformat()} through {last.isoformat()}, grouped by week"
    )
    root.set("aria-label", description)
    elements["activity-description"].text = description
    elements["activity-heading"].text = (
        f"COMMIT HEARTBEAT / {first.year} \u2014 PRESENT"
    )
    elements["activity-total"].text = f"{total:,} contributions"
    elements["activity-caption"].text = (
        f"Through {last.isoformat()} UTC \u00b7 public GitHub contributions"
    )

    weeks = defaultdict(int)
    for day, count in sorted(days.items()):
        sunday = day - timedelta(days=(day.weekday() + 1) % 7)
        weeks[sunday] += count
    first_week, last_week = min(weeks), max(weeks)
    span = max((last_week - first_week).days, 7)
    peak = max(weeks.values()) or 1

    def x_position(day):
        return 20 + 1160 * (day - first_week).days / span

    points = [
        f"{x_position(week):.1f},{240 - 160 * count / peak:.1f}"
        for week, count in sorted(weeks.items())
    ]
    elements["activity-line"].set("points", " ".join(points))
    elements["activity-area"].set(
        "points",
        f"{x_position(first_week):.1f},240.0 "
        + " ".join(points)
        + f" {x_position(last_week):.1f},240.0",
    )

    axis = elements["activity-years"]
    axis.clear()
    axis.set("id", "activity-years")
    for year in range(first.year, last.year + 1):
        x = min(1180, max(20, x_position(date(year, 1, 1))))
        ET.SubElement(axis, f"{{{SVG_NS}}}line", {
            "x1": f"{x:.1f}", "y1": "240", "x2": f"{x:.1f}", "y2": "248",
            "stroke": "rgba(185,238,232,.35)",
        })
        label = ET.SubElement(axis, f"{{{SVG_NS}}}text", {
            "x": f"{x:.1f}", "y": "268", "text-anchor": "middle",
            "class": "m", "font-size": "11", "fill": "rgba(243,240,233,.55)",
        })
        label.text = str(year)
    return ET.tostring(root, encoding="unicode") + "\n"


def build(username, token, start_year, through, template_path, output_path):
    # Read and validate all remote data before touching the last successful SVG.
    days = collect_days(username, token, start_year, through)
    rendered = render_heartbeat(days, template_path.read_text(encoding="utf-8"))
    if output_path.exists() and output_path.read_text(encoding="utf-8") == rendered:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output_path.parent,
            prefix=f".{output_path.name}.", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(rendered)
        temporary.chmod(0o644)
        temporary.replace(output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--through", type=date.fromisoformat)
    parser.add_argument(
        "--template", type=Path,
        default=Path(__file__).with_name("heartbeat-template.svg"),
    )
    parser.add_argument("--output", type=Path, default=Path("assets/heartbeat.svg"))
    args = parser.parse_args()
    today = datetime.now(timezone.utc).date()
    through = args.through or today
    if not 1 <= args.start_year <= through.year or through > today:
        parser.error("Choose a valid start year and an end date no later than today")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("GITHUB_TOKEN is required (Actions supplies it automatically)")
    try:
        changed = build(
            args.username, token, args.start_year, through, args.template, args.output
        )
    except (ValueError, OSError, ET.ParseError) as error:
        print(f"Heartbeat build failed: {error}", file=sys.stderr)
        return 1
    print("Updated heartbeat SVG" if changed else "Heartbeat SVG is unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
