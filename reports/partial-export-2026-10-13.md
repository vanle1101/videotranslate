# Xuất video khi còn câu lỗi và giữ kết quả qua lịch sử

## Hành vi đã sửa

- Studio cho xuất bản hiện có khi tác vụ đã dừng xử lý dù còn câu thiếu lời, lỗi TTS hoặc kiểm tra bản dịch chưa hoàn tất. Khi có phần thiếu, hộp xuất liệt kê câu bị ảnh hưởng.
- API xuất thủ công nhận `allow_incomplete: true` (boolean nghiêm ngặt). Xuất tự động vẫn yêu cầu các điều kiện hoàn tất cũ. Không xuất trong lúc dịch/sửa/dừng còn đang xử lý, hoặc từ preview chưa xử lý toàn bộ nguồn.
- Bản sao dữ liệu dùng để render đánh dấu `omit_voice` cho câu chưa sẵn sàng/thiếu lời/thiếu file giọng. Không đổi trạng thái câu, không xác nhận im lặng giả, không dùng lại audio cũ của câu FAILED.
- Câu thiếu giọng vẫn giữ lời Việt làm phụ đề theo thời gian nguồn. Giọng Việt ở khoảng đó được bỏ, nền được giữ theo chế độ tách giọng đã chọn. Lỗi TTS thường chưa sinh `subtitle_cues`, nên render chủ động tạo cue từ lời đã lưu.
- Cache âm thanh phân biệt bản đầy đủ và bản bỏ giọng. Kiểm tra độ dài audio, khớp timeline, video/audio và giải mã toàn bộ MP4 vẫn được thực hiện.
- Đánh dấu `output_incomplete` và `output_missing_segment_ids` được lưu bền vững, đưa vào báo cáo kết quả và lịch sử. MP4 hợp lệ vẫn tải được sau khi khởi động lại dù dự án đang FAILED/PREPARED/STOPPED. Không đổi thành đã dịch/đã kiểm tra xong.
- Sửa lời hoặc đổi cấu hình làm hết hiệu lực kết quả như trước; file mất/hỏng/cũ không được cung cấp như kết quả mới.

## Nguyên nhân chặn tải trước đây

Nút xuất, API và bộ khôi phục đều gắn khả năng tải với việc tất cả câu READY/PLAYED và kiểm tra bản dịch hoàn tất. Khi còn một câu FAILED, người dùng không thể tải phần đã hoàn thành. Bản thử ban đầu còn lọc bỏ toàn bộ câu FAILED (mất phụ đề) và chặn xuất từ session đã STOPPED. Các điều kiện này đã được tách khỏi việc xuất thủ công bản hiện có.

## Kiểm thử

- 455 passed, 1 skipped, 19 subtests: API xuất/huỷ, final output gate, lưu/khôi phục session, lịch sử, audio cache, dub timing, portable pipeline và FFmpeg trên đường dẫn Windows có dấu.
- 268/268 UI tests.
- Bài thử MP4 thật 4 giây: phụ đề câu TTS lỗi hiện trong hình; đoạn đó không có giọng Việt; tải HTTP khớp SHA-256 của file; reload checkpoint giữ link và trạng thái FAILED.
- Ráp audio checkpoint thật `265796c3`: 195 câu, bỏ ID 38 và 148 (số câu trên giao diện 39 và 149); 193 câu khác không lỗi timing hoặc vượt slot; timeline đủ 320.040635 giây.

## Kiểm tra video thực tế

- Xuất thành công video gần nhất `265796c3`, không dịch lại: `workspace/outputs/douyin_translated_265796c3_hq.mp4`.
- H.264 1920×1080 + AAC, 320.000 giây (nguồn 320.040635), 379,782,765 byte. Exporter giải mã toàn bộ cả hình/tiếng trước khi công bố; tổng lượt xuất 1877.4 giây với RoFormer + x264 slow/CRF16.
- Tải đủ file qua HTTP và so SHA-256: `ea899025df779d3354dadbc85d49df246adc36e08bf2911ce202e20adfc8abed`, khớp file gốc.
- Thoát sạch app rồi khởi động lại lúc 00:51:57 ngày 13/10. Snapshot và lịch sử vẫn có link MP4, 195 câu, `output_incomplete=true`, IDs thiếu `[38,148]`; tải HTTP Range trả 206 đúng 1024 byte.
- Đối chiếu checkpoint trước/sau: không thay lời gốc, lời Việt, audio_path, needs_review hoặc verification của bất kỳ câu nào.
- Đã xem khung hình tại 210.8 giây: câu 149 lỗi TTS vẫn có phụ đề “Vậy tôi có đến cũng chẳng tìm thấy đâu.”. Ảnh kiểm tra: `workspace/audit/provider-fix-20261012/partial-sentence-149.png`.
- Báo cáo review vẫn giữ 91 câu unresolved; xuất bản hiện có không đồng nghĩa mọi câu đã được xác minh.
- Kiểm tra bổ sung: rollback khi lưu kiểu phụ đề thất bại giữ marker partial; mở lại nguồn không retry dịch trong lúc đang xuất; WebSocket reconnect gửi thông tin kết quả partial. Nhóm API/kết nối 59 passed, nhóm caption/API 66 passed.

Không commit hoặc push. Các thay đổi có sẵn của workspace được giữ nguyên.
