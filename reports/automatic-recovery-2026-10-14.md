# Phục hồi tác vụ 2b5ef22b — 14/10/2026

## Nguyên nhân tái diễn

- Nguồn luôn dừng tại 287.48 giây: Whisper trả một token `-` có start=end=13.24 trong đoạn nhận diện. Bộ tách thoại biến token thành một câu dài 0 giây; kiểm tra giới hạn nguồn từ chối cả đoạn. Bấm Tiếp tục lặp lại cùng dữ liệu.
- Câu hiển thị 42/43 có thời lượng nguồn 0.14/0.04 giây, không đủ tạo giọng. Câu 82 chứa cả câu phụ đề Trung trong khoảng 0.36 giây và câu kế tiếp có nguy cơ lặp ý hỏi. Đây không phải lỗi mạng.
- Kiểm tra hai cửa sổ nguồn cho thấy audio là tiếng Hàn, trong khi pipeline ép ASR tiếng Trung. Whisper tự phát hiện `ko` với xác suất 0.99598 và 0.97853; SenseVoice độc lập cũng trả tiếng Hàn. Video có phụ đề Trung. Tự thử lại TTS không sửa được sai nguồn/ranh câu này.

## Thay đổi đã triển khai

- `core/dialogue_segments.py`: không tách theo token có thời lượng 0; giữ nguyên cả câu cha và thời gian đã đo. Chạy lại chính WAV lỗi cho 11 câu hợp lệ.
- `core/streaming/pipeline.py`: tối đa 3 lượt phục hồi ngoài retry của provider, chờ 5/15/30 giây cho lỗi tạm thời có kiểu rõ ràng ở nhận diện, dịch đoạn, kiểm tra bản dịch và Edge-TTS. Giữ phần đã hoàn thành và chỉnh sửa mới của người dùng. Các câu khác vẫn được xử lý.
- Không đưa lỗi thời lượng, sai mốc nguồn, xác thực, chứng chỉ TLS hoặc hết bộ nhớ vào vòng lặp mạng.
- Đếm ngược tự thử lại, số lần thử và lịch sử trạng thái; tạm dừng/dừng vẫn có hiệu lực. Các lượt chờ đồng thời có chủ sở hữu riêng để không xóa mất trạng thái của nhau.
- Worker Whisper phát lỗi `cuda_out_of_memory` có cấu trúc, hủy kết quả GPU chưa hoàn tất, đóng tiến trình rồi tự chạy lại cùng model/options bằng CPU int8 đúng một lần. Không đổi cài đặt chung hay giảm model.
- `static/app.js`: hiển thị rõ đếm ngược đến lần thử lại, phân biệt với thời gian hoàn tất video; không tiếp tục đếm khi mất kết nối, tạm dừng hoặc tác vụ đã kết thúc.

## Kiểm tra

- 186 kiểm thử pipeline/phục hồi/biên tập và 47 kiểm thử tiến độ/lịch sử qua trước chỉnh sửa cuối về lượt chờ đồng thời.
- 16 kiểm thử phục hồi tự động qua sau chỉnh sửa cuối, gồm hai thứ tự hủy lượt chờ đồng thời.
- 276 kiểm thử tương tác giao diện qua.
- 123 kiểm thử pipeline/phục hồi/lịch sử qua sau cập nhật thông báo nguồn bị chặn; 50 kiểm thử worker/cancellation/source identity qua sau bổ sung fallback CPU.
- App đã đóng sạch lúc 09:44:59 và khởi động bản sửa lúc 09:47:27. Đã gọi tiếp tục tác vụ thật một lần.
- Lượt này vượt mốc lỗi 287.48 đến 319.48 giây, sau đó gặp lỗi CUDA hết bộ nhớ ở bước sinh token. Đã sửa đường này, khởi động bản mới lúc 09:58:15 và tiếp tục đúng mốc 319.48 giây.
- Sau khởi động lại, tác vụ đã chuẩn bị nguồn đến đủ 395.782676/395.782676 giây. Lúc kiểm tra 10:03 vẫn RUNNING, có yêu cầu OpenCode thực và phản hồi mới. 64 câu có bản dịch; chưa tuyên bố hoàn tất dịch hay hoàn tất chất lượng. Snapshot `workspace/audit/auto_retry_20261014/live_after_fixes.json`.
- Log 10:04:03 timeout request `6ae8aaef71ea460989c1b10b5b417ee9`, 10:04:04 tự chạy attempt 2. Không gọi Tiếp tục lần nữa. Lúc 10:10 tác vụ vẫn RUNNING.
- Phát hiện nhãn công đoạn song song còn giữ từ bước nhạc nền dù AI có yêu cầu mới. Đã bổ sung callback tiến độ thực cho đường song song; 107 kiểm thử preview/phục hồi qua. Sửa nhãn này nằm trên đĩa, chưa nạp vào tiến trình đang chạy để tránh ngắt tác vụ.
- Sao lưu trước chạy: `workspace/audit/auto_retry_20261014/2b5ef22b_before_trial.json`.

## Giới hạn còn tồn tại

Chưa triển khai hỗ trợ đầy đủ audio Hàn + phụ đề Trung. Cần nhận diện ngôn ngữ thực, căn thời gian thoại theo audio đúng ngôn ngữ và đối chiếu phụ đề Trung; không thể xác nhận đúng nguồn bằng cách ép hai ASR cùng nhận tiếng Trung. Không đánh dấu các câu chưa kiểm chứng là hoàn tất, không thay bằng im lặng hay kéo mốc tùy ý.

Bằng chứng: `workspace/audit/pacing_source_findings_20261014.md`, `workspace/audit/pacing_source_probe_20261014.json`, `workspace/audit/pacing_language_probe_20261014.json`, `workspace/audit/asr-boundary-287480-fixed.json`.

## Khắc phục lỗi Edge-TTS 403 và ngôn ngữ nguồn (12:13 ngày 14/10)

- 50/53 câu FAILED bị HTTP403 tại WebSocket Edge-TTS, không phải bản dịch sai hay thiếu luồng. Thử cùng câu/giọng Hoài My: SDK7.2.8 lỗi403, SDK7.3.1 thành công. Đã nâng venv và requirements lên edge-tts>=7.3.1,<8; giữ nguyên giọng.
- Đóng app sạch12:12:45, khởi động12:13:38, tiếp tục tác vụ2b5ef22b đã lưu. Số câu có lời và WAV tăng63→111/146. Đây là tiến độ lời/giọng, không phải tỷ lệ đã xác minh ngữ nghĩa.
- 30 câu trống lời Việt vẫn còn; các câu thời lượng quá ngắn vẫn cần sửa nguồn. Không xoá hoặc đánh dấu im lặng để ép hoàn tất.
- Thêm nhận diện ngôn ngữ tự động ở từng khoảng nguồn có giới hạn; phát source_language_mismatch trước khi công bố dữ liệu nhận nhầm tiếng Trung. Lưu chẩn đoán theo định danh nguồn/runtime để tránh thử lại sai ngôn ngữ vô ích. BGM đã lưu vẫn tái sử dụng.
- Kiểm tra production worker trên đoạn202.41–211.77s: tiếng Hàn95.96%, không công bố câu sai. Chưa triển khai chuyển đổi toàn bộ dự án cũ sang lời Hàn + phụ đề Trung.
- 251 kiểm thử nguồn/worker/preview/review/BGM qua; sau tích hợp thông báo rõ nguyên nhân và tiến độ lúc retry:62 kiểm thử recovery/progress/Edge-TTS qua.
- Bản sửa ngôn ngữ nằm trên đĩa; không khởi động lại giữa lượt phục hồi đang hoạt động. Bản Edge-TTS mới đã chạy thực trong app.
- Sao lưu:workspace/audit/2b5ef22b_before_edge_upgrade_20261014.json. Bằng chứng:workspace/audit/edge_tts_protocol_20261014.md, workspace/audit/source_language_guard_probe_20261014.json.
