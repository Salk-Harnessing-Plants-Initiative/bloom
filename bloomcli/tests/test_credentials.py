"""Task 2.1 — profile-based dotenv credentials (mirrors packages/bloom-fs)."""

import pytest

from bloomcli.credentials import (
    Credentials,
    available_profiles,
    filename_for_profile,
    load_credentials,
    resolve_profile_path,
    save_credentials,
)

CREDS = Credentials(
    api_url="https://bloom.salk.edu/api",
    anon_key="anon-xyz",
    email="user@salk.edu",
    password="hunter2",
)


def test_filename_for_profile():
    assert filename_for_profile("prod") == "credentials.txt"
    assert filename_for_profile("staging") == "credentials.staging.txt"


def test_save_then_load_default_prod_profile(tmp_path):
    path = save_credentials(CREDS, config_dir=tmp_path)
    assert path.name == "credentials.txt"
    assert load_credentials("prod", config_dir=tmp_path) == CREDS


def test_save_then_load_named_profile(tmp_path):
    path = save_credentials(CREDS, profile="staging", config_dir=tmp_path)
    assert path.name == "credentials.staging.txt"
    assert load_credentials("staging", config_dir=tmp_path) == CREDS


def test_saved_file_is_dotenv_with_four_keys(tmp_path):
    path = save_credentials(CREDS, config_dir=tmp_path)
    text = path.read_text()
    for line in (
        "BLOOM_EMAIL=user@salk.edu",
        "BLOOM_PASSWORD=hunter2",
        "BLOOM_API_URL=https://bloom.salk.edu/api",
        "BLOOM_ANON_KEY=anon-xyz",
    ):
        assert line in text


def test_available_profiles_maps_filenames(tmp_path):
    (tmp_path / "credentials.txt").write_text("BLOOM_EMAIL=a\n")
    (tmp_path / "credentials.staging.txt").write_text("BLOOM_EMAIL=b\n")
    profiles = available_profiles(tmp_path)
    assert set(profiles) == {"prod", "staging"}


def test_conflicting_prod_files_raise(tmp_path):
    (tmp_path / "credentials.txt").write_text("BLOOM_EMAIL=a\n")
    (tmp_path / "credentials.prod.txt").write_text("BLOOM_EMAIL=b\n")
    with pytest.raises(ValueError, match="profile 'prod'"):
        available_profiles(tmp_path)


def test_missing_profile_raises_with_login_hint(tmp_path):
    with pytest.raises(FileNotFoundError, match="bloomcli login"):
        resolve_profile_path("prod", config_dir=tmp_path)


def test_file_missing_required_key_raises(tmp_path):
    (tmp_path / "credentials.txt").write_text("BLOOM_EMAIL=a\nBLOOM_PASSWORD=b\n")  # no url/anon
    with pytest.raises(ValueError, match="missing required keys"):
        load_credentials("prod", config_dir=tmp_path)
