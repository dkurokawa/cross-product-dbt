from cross_product_platform import __version__
from cross_product_platform.cli import build_parser


def test_version_and_parser() -> None:
    assert __version__
    assert build_parser().prog == "platform"
