"""安装索引仅用合成元数据与空EXE夹具；不扫描用户游戏，不运行任何程序。"""

import json

import pytest

from nova.gpu.catalog import GameCatalog, _parse_vdf


def quoted(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture-not-an-executable")
    return path


def steam_game(library, *, identity="101", title="Practice Adventure", directory="Practice"):
    steamapps = library / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    root = steamapps / "common" / directory
    root.mkdir(parents=True, exist_ok=True)
    manifest = steamapps / f"appmanifest_{identity}.acf"
    manifest.write_text(
        '"AppState" { "appid" '
        + quoted(identity)
        + ' "name" '
        + quoted(title)
        + ' "installdir" '
        + quoted(directory)
        + " }",
        encoding="utf-8",
    )
    return root, manifest


def catalog(**overrides):
    options = {"registry_roots": [], "steam_paths": [], "epic_manifest_dirs": []}
    return GameCatalog(**(options | overrides))


def test_gameconfig_exact_existing_path_and_safe_summary(tmp_path):
    game = executable(tmp_path / "Installed" / "PracticeClient.exe")
    missing = tmp_path / "missing.exe"
    index = catalog(registry_roots=[str(game), str(missing), r"\\host\private\game.exe"])
    summary = index.refresh()
    assert summary["counts"] == {"gameconfig": 1, "steam": 0, "epic": 0}
    assert summary["exact_entries"] == 1
    assert str(tmp_path) not in json.dumps(summary)
    assert index.match(game) == {
        "source": "gameconfig",
        "id": "windows",
        "title": "PracticeClient",
        "exact": True,
        "category": "game",
    }
    assert index.match(missing) is None


def test_steam_nested_executable_is_only_root_evidence(tmp_path):
    root, _ = steam_game(tmp_path / "Steam")
    client = executable(root / "bin" / "win64" / "NestedClient.exe")
    executable(root / "uninstall.exe")
    index = catalog(steam_paths=[tmp_path / "Steam"])
    summary = index.refresh()
    match = index.match(client)
    assert summary["counts"]["steam"] == 1
    assert summary["exact_entries"] == 0  # 不递归把所有EXE当作游戏。
    assert match["source"] == "steam"
    assert match["id"] == "101"
    assert match["exact"] is False
    assert match["category"] == "game"
    assert index.match(root / "uninstall.exe")["category"] == "tool"


def test_steam_installation_root_boundary_not_prefix(tmp_path):
    root, _ = steam_game(tmp_path / "Steam")
    neighbor = executable(root.with_name(root.name + "-other") / "client.exe")
    index = catalog(steam_paths=[tmp_path / "Steam"])
    index.refresh()
    assert index.match(neighbor) is None


@pytest.mark.parametrize("legacy", [False, True])
def test_libraryfolders_discovers_other_library_without_exe_scan(tmp_path, legacy):
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    library = tmp_path / "Second Library"
    root, _ = steam_game(library)
    client = executable(root / "deep" / "Practice.exe")
    value = quoted(library) if legacy else '{ "path" ' + quoted(library) + " }"
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders" { "0" ' + value + " }", encoding="utf-8"
    )
    index = catalog(steam_paths=[steam])
    assert index.refresh()["counts"]["steam"] == 1
    assert index.match(client)["source"] == "steam"


def test_epic_launch_executable_exact_and_does_not_index_helpers(tmp_path):
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    install = tmp_path / "EpicPractice"
    game = executable(install / "Binaries" / "Win64" / "Practice.exe")
    helper = executable(install / "Another.exe")
    (manifests / "practice.item").write_text(
        json.dumps(
            {
                "InstallLocation": str(install),
                "LaunchExecutable": r"Binaries\Win64\Practice.exe",
                "DisplayName": "Epic Practice",
                "AppName": "fixture-id",
                "LaunchCommand": "SYNTHETIC_PRIVATE_LAUNCH_ARGUMENT_NOT_READ",
            }
        ),
        encoding="utf-8",
    )
    index = catalog(epic_manifest_dirs=[manifests])
    assert index.refresh()["counts"]["epic"] == 1
    assert index.match(game)["exact"] is True
    assert index.match(helper) is None
    assert "PRIVATE" not in json.dumps(index.match(game))


@pytest.mark.parametrize(
    "launch", [r"..\other.exe", r"\outside.exe", "../other.exe", "C:/other.exe"]
)
def test_epic_rejects_traversal_or_external_launch_path(tmp_path, launch):
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    executable(tmp_path / "other.exe")
    install = tmp_path / "game"
    install.mkdir()
    (manifests / "invalid.item").write_text(
        json.dumps(
            {
                "InstallLocation": str(install),
                "LaunchExecutable": launch,
            }
        ),
        encoding="utf-8",
    )
    result = catalog(epic_manifest_dirs=[manifests]).refresh()
    assert result["counts"]["epic"] == 0
    assert result["errors"] == [{"source": "epic", "code": "GAME_MANIFEST_INVALID"}]
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("directory", ["../outside", r"..\outside", "C:/outside", "/outside"])
def test_steam_rejects_traversal_directory(tmp_path, directory):
    root, manifest = steam_game(tmp_path / "Steam")
    manifest.write_text(
        '"AppState" { "appid" "101" "installdir" ' + quoted(directory) + " }", encoding="utf-8"
    )
    index = catalog(steam_paths=[tmp_path / "Steam"])
    summary = index.refresh()
    assert summary["counts"]["steam"] == 0
    assert index.match(executable(root / "safe.exe")) is None


@pytest.mark.parametrize(
    "title",
    [
        "Wallpaper Engine",
        "Steamworks Common Redistributables",
        "Practice Dedicated Server",
        "Practice Tools",
    ],
)
def test_steam_non_game_metadata_is_tool_even_if_registry_has_same_exe(tmp_path, title):
    root, _ = steam_game(tmp_path / "Steam", title=title)
    game = executable(root / "engine.exe")
    index = catalog(steam_paths=[tmp_path / "Steam"], registry_roots=[game])
    summary = index.refresh()
    assert summary["tool_entries"] >= 1
    assert index.match(game)["category"] == "tool"


def test_steam_helper_directories_are_only_negative_evidence(tmp_path):
    root, _ = steam_game(tmp_path / "Steam")
    helper = executable(root / "_CommonRedist" / "runtime.exe")
    index = catalog(steam_paths=[tmp_path / "Steam"])
    index.refresh()
    assert index.match(helper)["category"] == "tool"
    assert index.match(executable(root / "game.exe"))["category"] == "game"


@pytest.mark.parametrize(
    "content",
    [
        "garbage",
        '"AppState" {',
        '"AppState" "bad"',
        '"AppState" { "appid" "999" "installdir" "Practice" }',
    ],
)
def test_malformed_or_wrong_identity_manifest_not_partially_accepted(tmp_path, content):
    root, manifest = steam_game(tmp_path / "Steam")
    manifest.write_text(content, encoding="utf-8")
    index = catalog(steam_paths=[tmp_path / "Steam"])
    result = index.refresh()
    assert result["counts"]["steam"] == 0
    assert result["errors"]
    assert index.match(executable(root / "client.exe")) is None


def test_manifest_byte_limit_is_enforced(tmp_path):
    _, manifest = steam_game(tmp_path / "Steam")
    manifest.write_bytes(b" " * (1024 * 1024 + 1))
    summary = catalog(steam_paths=[tmp_path / "Steam"]).refresh()
    assert summary["counts"]["steam"] == 0
    assert {"source": "steam", "code": "GAME_MANIFEST_INVALID"} in summary["errors"]


def test_refresh_drops_uninstalled_or_removed_metadata(tmp_path):
    root, manifest = steam_game(tmp_path / "Steam")
    client = executable(root / "client.exe")
    index = catalog(steam_paths=[tmp_path / "Steam"])
    index.refresh()
    assert index.match(client)
    manifest.unlink()
    assert index.refresh()["counts"]["steam"] == 0
    assert index.match(client) is None


def test_vdf_comments_unicode_and_escaped_path():
    parsed = _parse_vdf(
        '// comment\n"AppState" {"name" "练习游戏" "path" "C:\\\\Games\\\\Practice"}'
    )
    assert parsed["appstate"]["name"] == "练习游戏"
    assert parsed["appstate"]["path"] == r"C:\Games\Practice"


@pytest.mark.parametrize("value", ["}", '"x"', '"x" { "a" "b"', '"x" { "a" }', '"x" "b" trailing'])
def test_vdf_invalid_shape_rejected(value):
    with pytest.raises(ValueError):
        _parse_vdf(value)


def test_vdf_depth_is_bounded():
    with pytest.raises(ValueError):
        _parse_vdf('"x" {' * 20 + '"a" "b"' + "}" * 20)


def test_summary_property_is_path_free_and_does_not_share_mutable_state(tmp_path):
    root, _ = steam_game(tmp_path / "Steam")
    index = catalog(steam_paths=[tmp_path / "Steam"])
    assert index.summary["counts"] == {}
    index.refresh()
    summary = index.summary
    summary["counts"]["steam"] = 999
    summary["errors"].append({"source": "fake", "code": "fake"})
    assert index.summary["counts"]["steam"] == 1
    assert index.summary["errors"] == []
    assert str(root) not in json.dumps(index.summary)


def test_manifest_count_limit_preserves_bounded_partial_index(tmp_path, monkeypatch):
    monkeypatch.setattr("nova.gpu.catalog._MAX_FILES", 1)
    steam_game(tmp_path / "Steam", identity="101", directory="One")
    steam_game(tmp_path / "Steam", identity="102", directory="Two")
    summary = catalog(steam_paths=[tmp_path / "Steam"]).refresh()
    assert summary["counts"]["steam"] == 1
    assert {"source": "steam", "code": "MANIFEST_LIMIT"} in summary["errors"]


def test_initial_steam_roots_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr("nova.gpu.catalog._MAX_ROOTS", 1)
    steam_game(tmp_path / "SteamOne")
    steam_game(tmp_path / "SteamTwo")
    summary = catalog(steam_paths=[tmp_path / "SteamOne", tmp_path / "SteamTwo"]).refresh()
    assert summary["counts"]["steam"] == 1
    assert {"source": "steam", "code": "LIBRARY_LIMIT"} in summary["errors"]


def test_epic_source_directories_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr("nova.gpu.catalog._MAX_ROOTS", 1)
    first = tmp_path / "First"
    second = tmp_path / "Second"
    first.mkdir()
    second.mkdir()
    summary = catalog(epic_manifest_dirs=[first, second]).refresh()
    assert {"source": "epic", "code": "DIRECTORY_LIMIT"} in summary["errors"]
