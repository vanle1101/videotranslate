# Sửa lỗi OpenCode / Edge-TTS — 12/10/2026

Đã áp dụng vào máy này và khởi động lại app lúc 22:44. Backend mới: `http://127.0.0.1:64883`. Git HEAD: `b7ec5bf17b94de352e88c894d773167a809e173b`; giữ các thay đổi local có sẵn, chưa commit/push.

## Nguyên nhân xác nhận

- Run `265796c3`: OpenCode trả JSON lỗi cú pháp, chèn chữ ngoài chuỗi/đối tượng, có phản hồi trùng tên trường. Sáu mẫu trong `workspace/cache/ai_diagnostics` xác nhận đầu ra provider hỏng. Parser từ chối đúng; không được cắt nối rồi coi bản dịch đã kiểm định.
- OpenCode có lúc vượt deadline. Probe ngắn đầu tiên timeout 60 giây; probe tiếp theo với cấu hình reviewer trả JSON hợp lệ sau 26,8 giây. Đây là dịch vụ phản hồi không ổn định, không phải bằng chứng mọi yêu cầu dài sẽ thành công.
- Edge-TTS từng hết 3 lần thử mà không có âm thanh. `_finish_visual_chunk` giữ lỗi và tiếp tục câu sau nhưng thiếu lượt tự thử lại cuối cùng, dù thông báo hứa sẽ thử lại.

## Thay đổi

1. `core/translation_review.py`: hướng dẫn sửa JSON yêu cầu mỗi trường xuất hiện một lần, bỏ suy luận/văn bản chen ngoài JSON, tạo lại toàn bộ đối tượng từ nguồn ban đầu. Giữ parser nghiêm ngặt, số lần thử hữu hạn, checkpoint và kiểm định ngữ nghĩa.
2. `core/engines/translation/opencode_client.py`: phân loại timeout bằng `provider_timeout` và lỗi kiểm định bằng `provider_invalid_response`; timeout có cờ retryable. Lỗi cấu trúc không tự lặp cùng yêu cầu ở lớp transport; caller sửa prompt trong số lượt có giới hạn.
3. `core/streaming/pipeline.py`: ghi nhận lỗi Edge-TTS tạm thời ở luồng dịch theo đoạn; sau các câu độc lập, thử lại riêng giọng đọc đúng một lượt. Không dịch lại những câu đã có bản dịch. Nếu vẫn lỗi, giữ trạng thái FAILED để người dùng tiếp tục sau. Không thử lại tự động với lỗi TLS/quyền truy cập, câu đã bị sửa/xóa/thay giọng, hoặc khi đã dừng. Một lượt tạo giọng khác sẽ hủy lượt tự thử đang chờ, tránh nhân số lần thử.
4. Thêm hồi quy JSON hỏng, phân loại lỗi, retry sau các câu khác, giới hạn retry, Stop, sửa thủ công, thay giọng và ASR/dịch cập nhật lời trước khi TTS thất bại.

## Kiểm chứng

- Nhóm provider/JSON/reviewer/runtime: **388 passed** — `workspace/audit/provider-fix-20261012/provider-tests.log`.
- Nhóm pipeline/history/edit/pacing: **484 passed** — `workspace/audit/provider-fix-20261012/pipeline-tests.log`.
- Bộ test retry mới sau khi thêm hai ca bảo vệ đường dịch không dùng hình ảnh: **13 passed** (11 ca đã nằm trong nhóm 484 ở trên).
- `git diff --check` qua.
- Thử Edge-TTS thật với lời đã lưu của segment ID 130 và 163: cả hai WAV hợp lệ, lần lượt 1,872 giây và 2,136 giây. ID 130 phục hồi nhờ chế độ SentenceBoundary dự phòng đã có trong bản cập nhật. File thử nằm ở `workspace/audit/provider-fix-20261012/edge-130.wav` và `edge-163.wav`; không thay âm thanh dự án bằng file thử.
- App thoát sạch qua Ctrl+Q, khởi chạy lại bằng Python của venv, backend khỏe. Đối chiếu API trước/sau: **195 câu, nội dung lời, tham chiếu âm thanh và revision giữ nguyên; `.env` không đổi**. Kết quả: `workspace/audit/provider-fix-20261012/after-restart.json`.

## Trạng thái dự án và giới hạn

Run cũ vẫn được lưu ở chế độ full, nguồn đã xử lý đủ 320,040635 giây, trạng thái STOPPED và có thể tiếp tục. Chưa chạy lại toàn bộ dự án. Còn 3 câu thiếu giọng (ID 130, 148, 163), câu 39 (ID 38) chưa có lời Việt có căn cứ và 91 câu còn cờ cần kiểm tra. Sửa cơ chế phục hồi không tự xác nhận những câu này hoặc biến dự án thành hoàn tất.

Lỗi dịch vụ mạng/JSON có thể tái diễn; app sẽ thử hữu hạn và giữ kết quả đã lưu. Không xóa log cũ để che lỗi.

## Chuyển sang máy chính

Patch riêng của lượt sửa này: `workspace/audit/provider-fix-20261012/provider-fixes.patch` (3 file core, 3 file test). Patch dựa trên index đã có thay đổi local sau khi cập nhật Git, không phải bản diff thuần với HEAD. Trên máy chính cần đối chiếu mã và chạy `git apply --check` trước; nếu khác nền thì chuyển các thay đổi theo từng hunk, không ghi đè toàn bộ file. Không kèm `.env`, token hay dữ liệu dự án trong patch.
