"""
Filename parser tests. Run:  python3 scripts/test_filename_parser.py
Pass-through zero-Telegram test of the parse_filename heuristics.
"""

import sys
from pathlib import Path

# Make main importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load tmdb.py directly so the test doesn't need pyrogram/motor/etc.
import importlib.util
spec = importlib.util.spec_from_file_location(
    "tmdb_standalone", str(Path(__file__).resolve().parents[1] / "main" / "utils" / "tmdb.py"))
_tmdb = importlib.util.module_from_spec(spec)
sys.modules["tmdb_standalone"] = _tmdb  # needed for @dataclass introspection
spec.loader.exec_module(_tmdb)  # type: ignore
parse_filename = _tmdb.parse_filename


CASES = [
    # (filename,                                        expected_title,          expected_year,  is_tv)
    ("The.Matrix.1999.1080p.BluRay.x264.mkv",           "The Matrix",            1999,           False),
    ("Inception (2010) 1080p BluRay.mp4",               "Inception",             2010,           False),
    ("Oppenheimer.2023.2160p.WEB-DL.DDP5.1.mkv",        "Oppenheimer",           2023,           False),
    ("The.Batman.2022.1080p.WEBRip.x265-EVO.mkv",       "The Batman",            2022,           False),
    ("Breaking.Bad.S01E01.Pilot.1080p.BluRay.mkv",      "Breaking Bad",          None,           True),
    ("Game.of.Thrones.1x01.Winter.Is.Coming.mkv",       "Game of Thrones",       None,           True),
    ("Severance Season 1 2160p WEB-DL.mkv",             "Severance",             None,           True),
    ("Dune.Part.Two.2024.1080p.BluRay.mkv",             "Dune Part Two",         2024,           False),
    ("The.Last.of.Us.S02E03.2160p.WEB-DL.mkv",          "The Last of Us",        None,           True),
    ("Parasite.2019.4K.HDR.mkv",                        "Parasite",              2019,           False),
    ("Interstellar.2014.IMAX.1080p.BluRay.x264-RARBG",  "Interstellar",          2014,           False),
    ("The Witcher S01 1080p NF WEB-DL.mkv",             "The Witcher",           None,           True),
]

fails = 0
for fname, want_title, want_year, want_tv in CASES:
    p = parse_filename(fname)
    ok = True
    if p.title != want_title: ok = False
    if want_year is not None and p.year != want_year: ok = False
    if p.is_tv != want_tv: ok = False
    status = "PASS" if ok else "FAIL"
    if not ok: fails += 1
    print(f"{status}  {fname!r:70s} → {p.title!r} yr={p.year} tv={p.is_tv}")

print("---")
print(f"{len(CASES)-fails} passed, {fails} failed")
sys.exit(0 if fails == 0 else 1)
