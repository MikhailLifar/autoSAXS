from __future__ import annotations


def main() -> int:
    from guisaxs_skills.liveview.app import run_liveview_app

    run_liveview_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

