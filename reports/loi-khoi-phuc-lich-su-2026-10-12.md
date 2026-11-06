# Lỗi mở lịch sử thấy 0 câu dù bản dịch gần hoàn tất vẫn còn

Ngày kiểm tra: 12/10/2026, giờ Việt Nam. Chỉ đọc log, checkpoint và code; không sửa ứng dụng hoặc chạy lại dịch.

## Kết luận

Có hai vấn đề liên quan: lần chạy sau tạo task mới thay vì tiếp tục task cũ; lịch sử gom các task cùng video rồi nút mở chính chọn task mới nhất, kể cả task rỗng. Vì vậy mở lịch sử vẫn có thể vào bản 0 câu. Hướng dẫn chỉ nói “mở lịch sử” là chưa đủ.

Ảnh người dùng gửi hiển thị Transcript 0 câu và trạng thái giữ phần đã dịch. Ảnh không hiển thị task ID nên không thể xác nhận ID trực tiếp từ ảnh. Tuy nhiên, dữ liệu lưu và phép chạy lại hàm gom lịch sử đều khớp cơ chế dưới đây.

## Dữ liệu thực tế

| Task | Lưu lần cuối | Chế độ | Số câu | Trạng thái |
|---|---|---|---:|---|
| 265796c3 | 12/10/2026 14:39:15 | full | 195 | STOPPED |
| 07b06069 | 12/10/2026 16:29:09 | preview | 0 | STOPPED |

Hai task cùng URL Douyin modal_id=7692745161054506290 và cùng video_da54f3c875074d2388dc44ac2b9f6ebc.mp4. Bản cũ còn 195 natural_vi, 128 PLAYED + 64 READY + 3 WAITING. Ba segment chưa xong: 130, 148, 163. Kiểm tra trước đó xác nhận cả 191 audio_path được tham chiếu vẫn tồn tại.

pipeline.log:12566 trở đi ghi job 265796c3 tới câu 195 và 97,9%, rồi review. pipeline.log:12643–12647 ghi job mới 07b06069 dùng lại video nguồn nhưng chạy nhận diện đoạn 0–32 giây.

## Nguyên nhân lịch sử mở bản rỗng

- core/streaming/video_history.py:121–140: gom cùng nguồn, chọn representative bằng updated_at mới nhất khi không có tác vụ active; không xét số câu đã lưu để chọn đích mở bản dịch.
- Cùng file:145–150: versions vẫn giữ bản cũ, nhưng session_id của dòng chính là owner của representative.
- static/app.js:4373 và 4400–4401: nút mở chính mở session_id này.
- static/app.js:4385–4395: bản cũ nằm trong mục thu gọn “Các bản đã lưu”, có nút “Mở bản này”.

Đã chạy group_video_history() với metadata đọc từ bốn manifest của video này: selected_session_id=07b06069; phiên 265796c3 vẫn đứng trong versions. Đây là kiểm chứng logic bằng dữ liệu lưu, không phải gọi API ứng dụng đang chạy.

## Vì sao tạo task mới

- static/app.js:191 khởi tạo currentTaskId=null; nút Bắt đầu dịch gửi translation_mode=preview trong start-url (khoảng 2749–2760).
- main.py:941–949 chỉ dò active_streaming_sessions, yêu cầu mode trùng và bỏ qua is_stopped; không dò manifest cũ trên đĩa trong luồng này.
- main.py:961 trở đi tạo task ID mới. Job cũ full và job mới preview cũng không khớp điều kiện tái sử dụng.
- Restore theo task ID có sẵn qua editable_session/restore_saved_session; không nên suy ra mọi thao tác mở lịch sử sẽ chọn đúng bản cũ.

## Lỗi làm bản cũ chưa hoàn tất

- errors.log:1515: segment 130, EdgeTTSRequestError, không có âm thanh sau ba lần thử.
- errors.log:1585: segment 148, SpeechBudgetError rồi PacingReviewRejected; bản rút gọn chưa qua kiểm tra văn nói.
- errors.log:1758: segment 163, EdgeTTSRequestError.
- Checkpoint review_summary: completed, checked=195, verified=74, corrected=30, unresolved=91. Không được coi 195 câu đã có bản dịch là kết quả đã hoàn toàn được xác minh.

## Đề nghị sửa trên máy chính

1. Tách trạng thái lần chạy mới nhất khỏi đích “Mở bản dịch đã lưu”; nếu lần mới nhất rỗng nhưng bản cũ có nội dung, phải làm bản có nội dung dễ thấy và mở được trực tiếp. Vẫn hiển thị lỗi của lần mới nhất.
2. Các bản đã lưu cần hiện task ID hoặc thời gian + số câu đã dịch; không chỉ ghi “Bản dịch toàn bộ”.
3. Khi cùng video đã có công việc dở dang, cung cấp lựa chọn rõ “Tiếp tục bản đã lưu” và “Tạo bản mới”; không để mặc định preview che bản full cũ.
4. Khi mở bản rỗng, tránh thông báo chung khiến người dùng tưởng chính phiên này đã có bản dịch được giữ.
5. Test hồi quy: full 195 câu rồi tạo preview 0 câu, restart và mở lịch sử; phải tìm/mở lại được full 195 câu ngay, giữ nguyên ID và audio hợp lệ. Resume phần đã chuẩn bị không được chạy ASR lại từ giây 0; xử lý các segment còn thiếu theo checkpoint và giữ các yêu cầu review hiện có.

## Cách truy cập bản cũ theo UI hiện tại

Lịch sử video → mở “Các bản đã lưu” của video → tìm “Bản dịch toàn bộ” lưu 12/10/2026 14:39 → “Mở bản này”. Task cần mở: 265796c3. Nút mở chính của dòng video hiện chọn 07b06069 theo dữ liệu kiểm tra. Chưa thao tác UI thực tế để xác nhận bản cũ đã hiện lên trên màn hình người dùng.
