# Sửa xuất video tách nền lặp lại — 2026-10-13

## Nguyên nhân

Preview lưu nền DSP theo interval, nhưng HQ export chỉ cache bản mix cuối.
Thay đổi giọng/revision làm mất hiệu lực mix và chạy lại toàn bộ separation.
Máy đang chọn RoFormer; nền DSP preview không tương đương nền RoFormer.
Lượt task `265796c3` hôm nay riêng separation mất 19 phút 42 giây theo stderr.

## Thay đổi

- Cache riêng nền nguồn, độc lập với lời dịch, giọng đọc và phụ đề.
- Kiểm tra nguồn, engine/model/configuration, hash và giải mã toàn bộ audio trước khi dùng lại.
- Xuất DSP/realtime dùng lại đủ interval preview có provenance hợp lệ; kiểm tra biên thời gian từng packet Opus.
- Cache mix phân biệt nền ghép interval với nền xử lý toàn nguồn.
- Cache nền được lưu sau khi tách thành công, kể cả nếu render sau đó thất bại.
- Cache thiếu/hỏng hoặc cấu hình thay đổi sẽ xử lý lại; không lấy DSP thay RoFormer.
- Modal xuất phản ánh engine đang chọn và việc dùng lại nền.

## Kiểm chứng

- 198 test liên quan đạt: cache nền/mix, interval preview, partial export và timing.
- Kiểm tra thật video 320.040635 giây: `HQExporter._background` dùng lại RoFormer trong **8.11 giây**, không trích `raw_audio.wav`, không gọi `BSRoFormerSeparator.separate`.
- Đã giữ nền RoFormer từ export đang chạy bằng bản sao độc lập, đối chiếu PCM nguồn/raw và lưu provenance ở `workspace/cache/background_recovery_265796c3/provenance.json`.
- Lượt xuất cũ hoàn thành, API trả `COMPLETED`. MP4: `workspace/outputs/douyin_translated_265796c3_hq.mp4`, 379,498,169 byte.

8.11 giây chỉ là thời gian lấy nền, không phải thời gian render toàn video.
Nền preview cũ thiếu provenance sẽ không được nhận bừa là cache hợp lệ.
Cache có giới hạn dung lượng; bản đã bị dọn hoặc cấu hình nguồn/model đổi cần tách lại.
