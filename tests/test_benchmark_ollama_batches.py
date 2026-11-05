import json

from scripts.benchmark_ollama_batches import _load_rows, _summarize_requests


def _write(path, rows):
    path.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")


def test_checkpoint_directory_rows_are_sorted_and_given_stable_ids(tmp_path):
    _write(tmp_path / "000000000100.json", [
        {"id": 0, "start": 1.0, "end": 2.0, "text_zh": "后来"}])
    _write(tmp_path / "000000000000.json", [
        {"id": 0, "start": 0.0, "end": 1.0, "text_zh": "先前"}])
    _write(tmp_path / "000000000000_language.json", [
        {"id": 0, "start": 0.0, "end": 1.0, "text_zh": "不纳入"}])

    all_rows, selected = _load_rows(tmp_path, limit=2, offset=0)

    assert [row["text_zh"] for row in all_rows] == ["先前", "后来"]
    assert [row["id"] for row in selected] == [0, 1]


def test_request_summary_reports_model_token_rates():
    summary = _summarize_requests([{
        "prompt_eval_count": 400, "prompt_eval_duration": 2_000_000_000,
        "eval_count": 20, "eval_duration": 1_000_000_000,
        "load_duration": 500_000_000, "total_duration": 4_000_000_000,
        "wall_elapsed_seconds": 4.1, "success": True,
    }])

    assert summary["request_count"] == 1
    assert summary["successful_requests"] == 1
    assert summary["prompt_tokens_per_second"] == 200.0
    assert summary["output_tokens_per_second"] == 20.0
    assert summary["model_load_seconds"] == .5
