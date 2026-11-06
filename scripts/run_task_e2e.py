"""Retired helper: use the supported Studio review and export flow."""

if __package__ in (None, ""):
    from safe_legacy_helpers import refuse_unsafe_helper
else:
    from .safe_legacy_helpers import refuse_unsafe_helper


def main():
    return refuse_unsafe_helper("run_task_e2e")


if __name__ == "__main__":
    raise SystemExit(main())


