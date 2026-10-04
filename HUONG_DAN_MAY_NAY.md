# Chạy trên Windows, RAM 16 GB

Máy đã kiểm tra ngày 04/10/2026: Intel i5-13420H (8 nhân / 12 luồng), RAM 16 GB, NVIDIA RTX 2050 4 GB, Windows 11, Python 3.12.10 và FFmpeg 9.0.1.

## Mở ứng dụng

Nhấp đúp **start.bat**, chọn video MP4, chọn giọng Hoài My hoặc Nam Minh, rồi bấm **Bắt đầu dịch & phát realtime**. Chờ xử lý xong trước khi bấm xuất MP4. Thành phẩm nằm trong `workspace/outputs`.

Môi trường `venv`, cấu hình `.env` và model Whisper Small đã được cài ở máy này. Sau khi khởi động lại Windows không cần cài lại. Nếu chuyển sang máy khác hoặc bị thiếu thư viện, chạy **setup.bat**. Script dùng Python 3.12, tái sử dụng package hệ thống phù hợp, chỉ bổ sung package thiếu vào venv và không sửa Python toàn cục. FFmpeg và FFprobe phải có trong PATH. Dung lượng cài mới khoảng 1–2 GB.

## Cấu hình đang dùng

- Nhận tiếng Trung: Faster-Whisper **Small**, CPU **int8**, **6 luồng**; model ở `workspace/models/faster-whisper-small`.
- Giọng Việt: Edge-TTS Hoài My / Nam Minh, cần Internet.
- Dịch: chế độ miễn phí qua dịch vụ mạng; không cần API key, có thể bị giới hạn hoặc gián đoạn theo dịch vụ. Có thể chuyển Gemini / DeepSeek ở tab Cài đặt và nhập key riêng.
- Giảm giọng gốc: DSP trên CPU, không tải bộ tách giọng nặng. Hiệu quả tùy video; có thể còn giọng gốc hoặc ảnh hưởng nhạc nền.
- Xuất MP4: H.264 + AAC, phụ đề ASS, tùy chọn làm mờ sub gốc; giữ kích thước và tỉ lệ video nguồn.
- Không nạp model lúc mở giao diện. Lần xử lý video đầu tiên cần thời gian nạp model; tốc độ thực tế phụ thuộc video và tải máy, không đảm bảo realtime.

Cấu hình dựa trên tổng RAM 16 GB, không dựa trên RAM trống tại thời điểm cài đặt. RTX 2050 được nhận diện, nhưng AI đang chạy CPU để hoạt động với driver hiện có mà không cài CUDA/PyTorch. Chỉ đổi `DEVICE=cuda` sau khi kiểm tra driver và các thư viện CUDA/CuDNN tương thích. VieNeu, SenseVoice, RoFormer và ProPainter là tùy chọn chưa cài; giao diện Models hiển thị tình trạng tệp thực tế.

Gemini model có thể nhập bằng tên trong Cài đặt. Mặc định tùy chọn là `gemini-3.8-flash`, đối chiếu [lịch vòng đời model của Google](https://ai.google.dev/gemini-api/docs/deprecations); chưa kiểm thử API trả phí vì máy chưa cấu hình key cho dự án.

## Kiểm tra và xử lý lỗi

Chạy `venv\Scripts\python.exe -B check_runtime.py` để kiểm tra cài đặt ngoại tuyến. Nếu lỗi khởi động, xem tab Diagnostics hoặc `workspace/logs`. Link Douyin/TikTok có thể bị yêu cầu đăng nhập/cookie theo nền tảng; có thể tải video hợp lệ về máy rồi chọn file MP4.

Bản PyAV được giới hạn dưới 19 vì Faster-Whisper 1.2.1 vẫn gọi API đã bị xóa ở PyAV 19: [upstream issue](https://github.com/SYSTRAN/faster-whisper/issues/1492).

Kiểm thử hồi quy không cần mạng/model:

```powershell
venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests/test_local_runtime.py tests/test_windows_media.py tests/test_portable_pipeline.py tests/test_local_api.py
```

Hai script kiểm tra riêng: `tests/smoke_desktop.py` kiểm tra QtWebEngine và thao tác chọn file; `tests/smoke_local_video.py` tạo câu nói tiếng Trung tổng hợp, nhận giọng thật, dịch, đọc tiếng Việt và xuất video. Script video cần Internet và model đã tải, tự dọn media thử.

Kết quả tại máy này: 39 bài hồi quy đạt; thử desktop đạt; video tổng hợp 6,864 giây qua 3 đoạn thoại, dịch và xuất MP4 thành công. Chưa kiểm chứng trên video dài hoặc tài khoản tải video riêng của người dùng. `pip check` còn báo xung đột của các công cụ toàn cục có sẵn (aider-chat, patchright, selenium); các package toàn cục đó không được chỉnh sửa bởi lần cài này.

`.env`, video, model và venv không được đẩy lên GitHub. Các bài audit cũ của dự án có yêu cầu model/mẫu video riêng và không thuộc bộ kiểm thử hồi quy trên.
