from triconvey_agent.backend.update_manager import is_version_newer, select_installer_asset


def test_is_version_newer_understands_basic_semver():
    assert is_version_newer("0.2.0", "0.1.9") is True
    assert is_version_newer("1.0.0", "1.0.0") is False
    assert is_version_newer("1.0.0-beta.1", "1.0.0") is False


def test_select_installer_asset_prefers_setup_executable():
    assets = [
        {
            "name": "TriConveyAgent-portable.exe",
            "browser_download_url": "https://example.com/portable.exe",
        },
        {
            "name": "TriConveyAgent-Setup-0.2.0.exe",
            "browser_download_url": "https://example.com/setup.exe",
        },
    ]

    chosen = select_installer_asset(assets)

    assert chosen is not None
    assert chosen["name"] == "TriConveyAgent-Setup-0.2.0.exe"
