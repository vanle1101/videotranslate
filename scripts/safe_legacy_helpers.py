"""Fail closed for retired, one-off scripts which bypassed Studio's gates.

Keep this module independent of configuration, providers, session restoration
and media tools. Merely importing an old entry point must never change a user's
project, synthesize speech or remove a previous output.
"""
import sys


_REASONS = {
    "complete_and_export": "Bản cũ có thể ép tốc độ giọng, tự đánh dấu READY và xuất bản dịch chưa được xác minh.",
    "finalize_all_segments": "Bản cũ có thể bỏ chi tiết trong lời dịch, xóa lời gốc có thoại và giữ verification của bản dịch trước.",
    "synthesize_remaining": "Bản cũ gọi trực tiếp worker với giới hạn tốc độ khác ứng dụng, không phục hồi qua checkpoint và review được hỗ trợ.",
    "run_export": "Bản cũ có thể xóa video kết quả trước khi xuất lại và bỏ qua kiểm tra phiên của ứng dụng.",
    "run_task_e2e": "Bản cũ tải lại video, tạo lại mã dự án cố định và có thể xuất riêng các câu READY hoặc nhận video cũ làm kết quả thành công.",
    "test_seg_24": "Bản cũ tự chạy khi import, phục hồi dự án thật, đổi giới hạn tốc độ và gọi trực tiếp worker của câu 24.",
    "test_restore": "Bản cũ tự phục hồi một dự án thật ngay khi import; hãy mở đúng dự án trong Lịch sử của ứng dụng.",
}


def refuse_unsafe_helper(name):
    """Give an actionable explanation without reading or mutating a project."""
    reason = _REASONS.get(name, "Script này không còn là đường xử lý được hỗ trợ.")
    print(
        f"LEGACY_HELPER_DISABLED: {name}.py\n{reason}\n"
        "Mở dự án đã lưu trong Lịch sử của ứng dụng. Chờ tác vụ hiện tại kết thúc; "
        "dùng 'AI kiểm tra lại' để đối chiếu nguồn và sửa các câu còn thiếu hoặc chưa chắc. "
        "Sau khi giải quyết các câu bị chặn, dùng 'Xuất video MP4' trong ứng dụng. "
        "Các thao tác này giữ checkpoint và các bước kiểm tra nghĩa, giọng đọc và output.\n"
        "Script đã dừng; dữ liệu dự án và video hiện có được giữ nguyên.",
        file=sys.stderr,
    )
    return 2
