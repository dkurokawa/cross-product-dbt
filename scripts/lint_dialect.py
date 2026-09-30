"""Thin wrapper so pre-commit / CI can run the dialect lint without installing the script."""

import sys

from cross_product_platform.cli import main

sys.exit(main(["lint-dialect", *sys.argv[1:]]))
