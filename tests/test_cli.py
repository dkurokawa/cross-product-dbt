from pathlib import Path

import pytest

from cross_product_platform.cli import main
from cross_product_platform.identity import SALT_ENV
from cross_product_platform.ingest import LakeNotEmptyError, run_ingest

from .conftest import small_config

ARGS = ["--persons", "60", "--months", "2", "--start", "2026-03-01", "--gap-day", "2026-03-05"]


def test_ingest_command_lands_and_refuses_to_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "lake"
    assert main(["ingest", "--root", str(root), *ARGS]) == 0
    out = capsys.readouterr().out
    assert "landed A:" in out and "quarantined E/accounts" in out
    assert main(["ingest", "--root", str(root), *ARGS]) == 1
    assert "--overwrite" in capsys.readouterr().err
    assert main(["ingest", "--root", str(root), "--overwrite", "--no-bad-files", *ARGS]) == 0
    assert "quarantined" not in capsys.readouterr().out.replace("0 quarantined", "")


def test_ingest_without_a_salt_fails_before_any_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(SALT_ENV, raising=False)
    assert main(["ingest", "--root", str(tmp_path / "lake"), *ARGS]) == 1
    assert SALT_ENV in capsys.readouterr().err
    assert not (tmp_path / "lake").exists()


def test_no_generate_lands_an_existing_incoming_tree(tmp_path: Path) -> None:
    from cross_product_platform.generators.run import generate_all

    generate_all(small_config(n_persons=80, months=2), tmp_path / "_incoming")
    result = run_ingest(small_config(), tmp_path, generate=False)
    assert result.report.files_landed["A"] > 0


def test_overwrite_removes_only_managed_directories(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("mine", encoding="utf-8")
    cfg = small_config(n_persons=60, months=2)
    run_ingest(cfg, tmp_path)
    with pytest.raises(LakeNotEmptyError):
        run_ingest(cfg, tmp_path)
    run_ingest(cfg, tmp_path, overwrite=True)
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "mine"
