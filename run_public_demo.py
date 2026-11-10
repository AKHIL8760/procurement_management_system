#!/usr/bin/env python3
"""Start the Railway demo in read-only mode on its public interface."""

import os

from app import main


def run() -> int:
    os.environ["READ_ONLY_DEMO"] = "1"
    os.environ["HOST"] = "0.0.0.0"
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
