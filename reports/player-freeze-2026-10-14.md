# Sửa trình phát kẹt 0:23 — 2026-10-14

Nguyên nhân: cửa sổ WebM đầu24s; tại23s frontend pause rồi mới chuyển mã120s tiếp. FAILED polling che loading. Seek tiếp theo huỷ encoder đang chuẩn bị cùng vùng video.

Đã sửa static/app.js:
- Chuẩn bị trước cửa sổ kế khi phát, giữ player chạy trong lúc chuẩn bị.
- Cửa sổ lạnh36s thay120s, chồng12s để giảm chờ tua.
- Tua cùng phạm vi đang chuẩn bị chỉ cập nhật đích phát; không huỷ/tạo lại encoder.
- FAILED giữ loading; STOPPED/CANCELLED polling không huỷ lần tua mới. Đổi nguồn/dừng thực vẫn huỷ.
- Kiểm soát generation/revision, phản hồi muộn không ghi đè nguồn/đích mới.

282 kiểm thử UI đạt, gồm prefetch không pause, dùng lại khi qua ranh, seek liên tiếp tới đích cuối, pause/stop/poll.
App đóng12:49:35, khởi động12:49:49 port65486. API phục vụ JS mới đã kiểm tra.
Probe video thật2b5ef22b đoạn12–48s READY preview0bab6f3673a70c8eea793226; không thay bản dịch/manifest.
Công cụ native screenshot FrameArrived timeout nên chưa xác nhận thị giác phát mượt trên chính cửa sổ Qt. Thử mở remote debugging bị automatic approval policy chặn; đã dùng kiểm thử offline và API preview thật thay thế.
