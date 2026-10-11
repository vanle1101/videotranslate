"""Source grouping and status projection use evidence, not media filenames."""
from copy import deepcopy

import pytest

from core.streaming.video_history import group_video_history, local_identity, source_identity


POST = "7692745161054506290"


def project(task_id, **fields):
    return {"task_id": task_id, "title": "source.mp4", "status": "STOPPED",
            "updated_at": 1, "can_open": True, "saved": True, **fields}


def test_douyin_url_variants_and_url_less_local_project_form_one_video():
    rows = [
        project("first", source_url=f"https://www.douyin.com/jingxuan?modal_id={POST}&foo=1", video_path=r"D:\Videos\source.mp4"),
        project("second", source_url=f"https://www.douyin.com/video/{POST}?from=web", video_path=r"D:\Videos\different-download.mp4"),
        project("local", video_path="d:/VIDEOS/source.mp4", updated_at=3),
    ]
    original = deepcopy(rows)
    videos = group_video_history(rows)
    assert len(videos) == 1 and videos[0]["version_count"] == 3
    assert videos[0]["session_id"] == "local"
    assert rows == original
    # Input order and operation IDs do not alter the canonical video ID.
    assert group_video_history(list(reversed(rows)))[0]["video_id"] == videos[0]["video_id"]


def test_same_filename_different_directories_are_separate_videos():
    videos = group_video_history([
        project("a", video_path=r"C:\One\source.mp4"),
        project("b", video_path=r"D:\Two\source.mp4"),
    ])
    assert len(videos) == 2
    assert len({row["video_id"] for row in videos}) == 2


def test_distinct_douyin_posts_and_short_links_never_merge_by_host_or_title():
    rows = [project("a", source_url=f"https://www.douyin.com/video/{POST}"),
            project("b", source_url="https://www.douyin.com/video/7663686335764516115"),
            project("c", source_url="https://v.douyin.com/one/"),
            project("d", source_url="https://v.douyin.com/two/")]
    assert len(group_video_history(rows)) == 4


def test_iesdouyin_share_url_uses_the_same_known_post_identity():
    rows = [project("desktop", source_url=f"https://www.douyin.com/video/{POST}"),
            project("share", source_url=f"https://www.iesdouyin.com/share/video/{POST}/")]
    assert len(group_video_history(rows)) == 1


def test_reused_local_path_does_not_bridge_distinct_known_douyin_posts():
    rows = [project("first", video_path=r"D:\source.mp4", source_url=f"https://www.douyin.com/video/{POST}"),
            project("second", video_path=r"D:\source.mp4", source_url="https://www.douyin.com/video/7663686335764516115"),
            project("unknown", video_path=r"D:\source.mp4"),
            project("first-new-path", video_path=r"D:\another.mp4", source_url=f"https://www.iesdouyin.com/share/video/{POST}/")]
    videos = group_video_history(rows)
    assert len(videos) == 3
    assert sorted(row["version_count"] for row in videos) == [1, 1, 2]
    assert len({row["video_id"] for row in videos}) == 3


def test_unrecognized_hosts_cannot_claim_douyin_identity():
    assert source_identity(f"https://evil.test/video/{POST}") != source_identity(f"https://douyin.com/video/{POST}")
    assert source_identity(f"https://douyin.com.evil.test/video/{POST}") != source_identity(f"https://douyin.com/video/{POST}")


def test_arbitrary_url_queries_are_preserved():
    assert source_identity("https://example.com/video?id=a") != source_identity("https://example.com/video?id=b")


@pytest.mark.parametrize("value", [None, "", "not a url", "https://[bad", 23, "file://C:/video.mp4"])
def test_invalid_urls_do_not_create_cross_project_aliases(value):
    assert source_identity(value) is None
    assert len(group_video_history([project("a", source_url=value), project("b", source_url=value)])) == 2


def test_path_normalization_keeps_full_identity_without_filesystem_access(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("history grouping must not access the filesystem")
    monkeypatch.setattr("pathlib.Path.stat", forbidden)
    assert local_identity(r"D:\Work\clips\..\source.mp4") == local_identity("d:/work/source.mp4")
    assert local_identity(r"D:\One\source.mp4") != local_identity(r"D:\Two\source.mp4")


def test_active_run_beats_recent_saved_variant_and_retains_exact_progress():
    videos = group_video_history([
        project("edited", video_path=r"D:\source.mp4", updated_at=200, status="COMPLETED", progress_pct=100),
        project("running", video_path=r"D:\source.mp4", updated_at=100, status="RUNNING", progress_pct=22.9,
                stage="Đang đối chiếu lời nguồn", can_stop=True),
    ])
    assert videos[0]["session_id"] == "running"
    assert videos[0]["status"] == "RUNNING" and videos[0]["progress_pct"] == 22.9
    assert videos[0]["stage"] == "Đang đối chiếu lời nguồn" and videos[0]["can_stop"]


def test_newest_failed_attempt_is_not_hidden_by_old_success():
    videos = group_video_history([
        project("good", video_path=r"D:\source.mp4", updated_at=1, status="COMPLETED", progress_pct=100),
        project("failed", video_path=r"D:\source.mp4", updated_at=3, status="FAILED", progress_pct=None, stage="Muse timeout"),
    ])
    assert videos[0]["session_id"] == "failed" and videos[0]["status"] == "FAILED"
    assert videos[0]["progress_pct"] is None and videos[0]["stage"] == "Muse timeout"


def test_newer_missing_source_failure_is_not_hidden_by_old_editable_project():
    videos = group_video_history([
        project("editable", video_path=r"D:\source.mp4", updated_at=3),
        project("missing", video_path=r"D:\source.mp4", updated_at=4, can_open=False, status="FAILED", stage="Thiếu video gốc"),
    ])
    assert videos[0]["session_id"] == "missing"
    assert videos[0]["status"] == "FAILED" and not videos[0]["can_open"]
    assert videos[0]["stage"] == "Thiếu video gốc"
    assert videos[0]["version_count"] == 2
    assert videos[0]["versions"][0]["task_id"] == "missing"


def test_export_joins_owner_without_source_and_stop_uses_export_id():
    videos = group_video_history([
        project("owner", video_path=r"D:\source.mp4", source_video_url="/api/inputs/source.mp4", status="COMPLETED"),
        {"task_id": "export_owner", "parent_session_id": "owner", "is_export": True,
         "status": "RUNNING", "updated_at": 5, "progress_pct": None, "stage": "Đang kiểm định", "can_stop": True},
    ])
    row = videos[0]
    assert len(videos) == 1 and row["version_count"] == 1
    assert row["task_id"] == "export_owner" and row["session_id"] == "owner"
    assert row["source_video_url"] == "/api/inputs/source.mp4" and row["can_open"]
    assert row["progress_pct"] is None and row["can_stop"]


@pytest.mark.parametrize("terminal", ["FAILED", "CANCELLED", "STOPPED", "TIMED_OUT"])
def test_recent_terminal_export_is_visible_then_newer_edit_restores_project_state(terminal):
    owner = project("owner", video_path=r"D:\source.mp4", status="COMPLETED", updated_at=1)
    export = {"task_id": "export_owner", "parent_session_id": "owner", "is_export": True,
              "status": terminal, "updated_at": 5, "stage": "Xuất video đã kết thúc"}
    row = group_video_history([owner, export])[0]
    assert row["status"] == terminal and row["stage"] == "Xuất video đã kết thúc" and row["can_open"]
    assert row["session_id"] == "owner"
    owner["updated_at"] = 7
    assert group_video_history([owner, export])[0]["task_id"] == "owner"


def test_newer_active_session_does_not_reuse_progress_from_an_older_export():
    rows = [project("old", video_path=r"D:\source.mp4", updated_at=1),
        {"task_id": "export_old", "parent_session_id": "old", "is_export": True, "status": "RUNNING", "updated_at": 3, "progress_pct": 80},
        project("new", video_path=r"D:\source.mp4", status="RUNNING", updated_at=7, progress_pct=4)]
    assert group_video_history(rows)[0]["task_id"] == "new"
    assert group_video_history(rows)[0]["progress_pct"] == 4


def test_active_export_wins_over_later_checkpoint_of_its_same_running_owner():
    rows = [project("owner", video_path=r"D:\source.mp4", status="RUNNING", updated_at=10, progress_pct=22.9),
        {"task_id": "export_owner", "parent_session_id": "owner", "is_export": True,
         "status": "RUNNING", "updated_at": 3, "progress_pct": 47.5}]
    assert group_video_history(rows)[0]["task_id"] == "export_owner"
    assert group_video_history(rows)[0]["progress_pct"] == 47.5
