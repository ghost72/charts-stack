"""
Discovery CLI for charts-energy (Variant A).

Run inside the container:
    python -m app.discover               # list confirmed channels (default)
    python -m app.discover --list
    python -m app.discover --probe <slug>  # check whether a slug is a real channel

--list prints a built-in table of confirmed slugs (display | slug | genre) from
the feasibility report. --probe opens the stream host with Icy-MetaData:1 via
httpx (headers-only GET, normal TLS) and reports real/not-real
(HTTP 200 + icy-name header => real). The CLI performs no state/playlist writes.
"""

import argparse
import sys

import httpx

from . import config
from .i18n import t

# Confirmed channels from .agents/tasks/energy-api-feasibility.md section 3
# (each proven live by reading ICY metadata). (display, slug, genre).
# KEEP IN SYNC with charts-dashboard/app/collector.py :: _ENERGY_CHANNELS
# (non-regional subset); a dashboard drift test enforces this.
CONFIRMED_CHANNELS: list[tuple[str, str, str]] = [
    ("Energy Made In Switzerland", "energymadeinswitzerland", "Swiss/Mundart"),
    ("Energy Pop", "energypop", "Pop"),
    ("Energy Hits", "energyhits", "Hits"),
    ("Energy Dance", "energydance", "Dance"),
    ("Energy Rock", "energyrock", "Rock"),
    ("Energy Urban", "energyurban", "Rap/Hip-Hop"),
    ("Energy 80s", "energy80s", "80s"),
    ("Energy 90s", "energy90s", "90s"),
    ("Energy 00s", "energy00s", "00s"),
    ("Energy 10s", "energy10s", "10s"),
    ("Energy 20s", "energy20s", "20s"),
    ("Energy Workout", "energyworkout", "Workout"),
    ("Energy Lounge", "energylounge", "Lounge"),
    ("Energy Love", "energylove", "Love/Ballads"),
    ("Energy Latin", "energylatin", "Latin"),
    ("Energy At Work", "energyatwork", "At Work"),
    ("Energy Me Time", "energymetime", "Me Time"),
    ("Energy Xmas", "energyxmas", "Christmas"),
    ("Energy Zurich", "energyzuerich", "Regional"),
    ("Energy Bern", "energybern", "Regional"),
    ("Energy Basel", "energybasel", "Regional"),
    ("Energy Luzern", "energyluzern", "Regional"),
]


def list_channels() -> None:
    """Print the built-in table of confirmed channels."""
    print(t("energy.discover.list_header"))
    print(t("energy.discover.columns"))
    for display, slug, genre in CONFIRMED_CHANNELS:
        print(f"{display} | {slug} | {genre}")


def probe(slug: str) -> int:
    """
    Probe a guessed slug: HTTP 200 + an icy-name header => real channel.
    Returns a process exit code (0 real, non-zero otherwise).
    """
    url = config.ENERGY_STREAM_HOST_TMPL.format(slug=slug)
    try:
        with httpx.Client(timeout=config.ENERGY_READ_TIMEOUT) as client:
            resp = client.get(url, headers={
                "Icy-MetaData": "1",
                "User-Agent": config.ENERGY_USER_AGENT,
            })
    except httpx.HTTPError as e:
        print(t("energy.discover.probe_unreachable", slug=slug, error=e))
        return 1

    icy_name = resp.headers.get("icy-name")
    if resp.status_code == 200 and icy_name:
        print(t("energy.discover.probe_ok", slug=slug, name=icy_name))
        return 0
    print(t("energy.discover.probe_fail", slug=slug, status=resp.status_code))
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.discover")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true",
                       help="list confirmed channels (default)")
    group.add_argument("--probe", metavar="SLUG",
                       help="probe whether SLUG is a real channel")
    args = parser.parse_args(argv)

    if args.probe:
        return probe(args.probe)
    list_channels()
    return 0


if __name__ == "__main__":
    sys.exit(main())
