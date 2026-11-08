"""Which test files one Windows CI job runs (.github/workflows/tests.yml).

Usage: python .github/split_tests.py <job> <jobs>. Prints the job's test
modules. Files are spread so each job gets about the same time: a file
weighs its seconds on the Windows runner (measured 2026-11-09, run
42536951238), and a file not listed weighs 0.3 s per test. The real mpv
tests have their own job, so they are left out.
"""

import pathlib
import sys

SECONDS = {
    "test_page": 32,
    "test_skips": 25,
    "test_mapping": 25,
    "test_speed": 25,
    "test_settings": 21,
    "test_upscale": 21,
    "test_playback_lifecycle": 21,
    "test_reconcile": 14,
    "test_migrations": 14,
    "test_outbox": 12,
    "test_phoneplay": 11,
    "test_entry": 11,
    "test_playback": 10,
    "test_bulk": 10,
    "test_library_root": 9,
    "test_setup": 9,
    "test_web": 9,
    "test_config": 8,
    "test_match_tab": 6,
    "test_franchise": 6,
    "test_review_fixes": 6,
    "test_search_typos": 6,
    "test_timestamps": 6,
    "test_lengths": 5,
    "test_episode_art": 5,
    "test_art_refresh": 5,
    "test_release_map": 4,
    "test_extra_folders": 4,
    "test_episode_numbers": 3,
    "test_prepare": 3,
    "test_remote": 3,
    "test_history": 3,
    "test_spine": 2,
    "test_fonts": 2,
    "test_errors": 2,
    "test_regression_28": 1,
    "test_tracks": 1,
    "test_playlists": 1,
    "test_system": 1,
    "test_update": 1,
    "test_about": 1,
    "test_remote_hosts": 1,
}


def weight(path):
    if path.stem in SECONDS:
        return SECONDS[path.stem]
    return 0.3 * path.read_text("utf-8").count("def test_")


def split(jobs):
    files = [f for f in sorted(pathlib.Path("tests").glob("test_*.py")) if f.stem != "test_real_tools"]
    out = [[0.0, []] for _ in range(jobs)]
    for f in sorted(files, key=lambda f: (-weight(f), f.stem)):
        job = min(out, key=lambda j: j[0])
        job[0] += weight(f)
        job[1].append("tests." + f.stem)
    return out


if __name__ == "__main__":
    print(" ".join(split(int(sys.argv[2]))[int(sys.argv[1])][1]))
