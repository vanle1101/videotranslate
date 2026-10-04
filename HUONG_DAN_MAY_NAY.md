# Chạy trên Windows, RAM 16 GB

Máy đã kiểm tra ngày 04/10/2026: Intel i5-13420H (8 nhân / 12 luồng), RAM 16 GB, NVIDIA RTX 2050 4 GB, Windows 11, Python 3.12.10 và FFmpeg 9.0.1.

## Mở ứng dụng

Nhấp đúp **start.bat**, chọn video MP4, chọn giọng Hoài My hoặc Nam Minh, rồi bấm **Bắt đầu dịch & phát realtime**. Chờ xử lý xong trước khi bấm xuất MP4. Thành phẩm nằm trong `workspace/outputs`.

Môi trường `venv`, cấu hình `.env` và model Whisper Small đã được cài ở máy này. Sau khi khởi động lại Windows không cần cài lại. Nếu chuyển sang máy khác hoặc bị thiếu thư viện, chạy **setup.bat**. Script dùng Python 3.12, tái sử dụng package hệ thống phù hợp, chỉ bổ sung package thiếu vào venv và không sửa Python toàn cục. FFmpeg và FFprobe phải có trong PATH. Dung lượng cài mới khoảng 1–2 GB.

## Cấu hình đang dùng

- Nhận tiếng Trung: Faster-Whisper **Small**, CPU **int8**, **6 luồng**; model ở `workspace/models/faster-whisper-small`.
- Giọng Việt: Edge-TTS Hoài My / Nam Minh, cần Internet.
- Dịch: **OpenRouter Free**, model **inclusionai/ling-3.0-flash-sante:free**, dùng key đã lưu trong OpenCode trên máy. Không tải LLM dịch về RAM. Có thể chuyển OpenCode Zen, Gemini / DeepSeek hoặc Google / MyMemory ở tab Cài đặt.
- Giảm giọng gốc: DSP trên CPU, không tải bộ tách giọng nặng. Hiệu quả tùy video; có thể còn giọng gốc hoặc ảnh hưởng nhạc nền.
- Xuất MP4: H.264 + AAC, phụ đề ASS, tùy chọn làm mờ sub gốc; giữ kích thước và tỉ lệ video nguồn.
- Không nạp model lúc mở giao diện. Lần xử lý video đầu tiên cần thời gian nạp model; tốc độ thực tế phụ thuộc video và tải máy, không đảm bảo realtime.

Cấu hình dựa trên tổng RAM 16 GB, không dựa trên RAM trống tại thời điểm cài đặt. RTX 2050 được nhận diện, nhưng AI đang chạy CPU để hoạt động với driver hiện có mà không cài CUDA/PyTorch. Chỉ đổi `DEVICE=cuda` sau khi kiểm tra driver và các thư viện CUDA/CuDNN tương thích. VieNeu, SenseVoice, RoFormer và ProPainter là tùy chọn chưa cài; giao diện Models hiển thị tình trạng tệp thực tế.

Gemini model có thể nhập bằng tên trong Cài đặt. Mặc định tùy chọn là `gemini-3.8-flash`, đối chiếu [lịch vòng đời model của Google](https://ai.google.dev/gemini-api/docs/deprecations); chưa kiểm thử API trả phí vì máy chưa cấu hình key cho dự án.

## Dùng key miễn phí có sẵn trong OpenCode

Mặc định ứng dụng gọi **OpenRouter Free** bằng kết nối `OpenRouter-Free` có sẵn trong OpenCode. Đây là dịch vụ OpenRouter, không phải OpenCode Zen. Chỉ chấp nhận model có hậu tố `:free`; không tự đổi provider hoặc model trả phí khi lỗi. Lần thử API trên máy trả bản dịch thành công và báo chi phí 0. Hạn mức và khả dụng phụ thuộc dịch vụ: [tài liệu bản miễn phí](https://openrouter.ai/docs/guides/routing/model-variants/free).

Key được tự đọc từ `%USERPROFILE%\.local\share\opencode\auth.json` (hoặc đường dẫn `XDG_DATA_HOME` nếu có). Không cần chép key vào code hay nhập lại. Có thể đặt `OPENROUTER_API_KEY` trong `.env` local để ghi đè. Key không xuất hiện trong API Cài đặt, giao diện hoặc GitHub.

Trong **Cài đặt**, chọn **OpenRouter Free**, lưu model rồi bấm **Kiểm tra OpenRouter Free**. Nếu chuyển máy, kết nối OpenRouter trong OpenCode hoặc đặt key vào `.env` local. Phần nhận giọng nói vẫn dùng Whisper Small trên CPU, còn tạo giọng Việt dùng Edge-TTS qua mạng.

Tùy chọn **OpenCode Zen Free** dùng [CLI chính thức](https://opencode.ai/docs/cli/#run), key `opencode` hoặc `OPENCODE_API_KEY`, session riêng không có quyền chạy công cụ, không chia sẻ và tự dọn file tạm. Khi kiểm tra ngày 04/10/2026, Zen trả **403 FreeTierError** ngay cả qua CLI gốc 1.18.30; vấn đề tương tự đã được báo ở [upstream](https://github.com/anomalyco/opencode/issues/49756). Vì vậy Zen không được đặt làm mặc định. Không giả lập header hoặc lách chặn dịch vụ.

## Kiểm tra và xử lý lỗi

Chạy `venv\Scripts\python.exe -B check_runtime.py` để kiểm tra cài đặt ngoại tuyến. Nếu lỗi khởi động, xem tab Diagnostics hoặc `workspace/logs`. Link Douyin/TikTok có thể bị yêu cầu đăng nhập/cookie theo nền tảng; có thể tải video hợp lệ về máy rồi chọn file MP4.

Bản PyAV được giới hạn dưới 19 vì Faster-Whisper 1.2.1 vẫn gọi API đã bị xóa ở PyAV 19: [upstream issue](https://github.com/SYSTRAN/faster-whisper/issues/1492).

Kiểm thử hồi quy không cần mạng/model:

```powershell
venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests/test_local_runtime.py tests/test_windows_media.py tests/test_portable_pipeline.py tests/test_local_api.py tests/test_opencode_client.py tests/test_opencode_translation.py tests/test_opencode_api.py tests/test_openrouter_client.py tests/test_openrouter_api.py
```

Hai script kiểm tra riêng: `tests/smoke_desktop.py` kiểm tra QtWebEngine và thao tác chọn file; `tests/smoke_local_video.py` tạo câu nói tiếng Trung tổng hợp, nhận giọng thật, dịch, đọc tiếng Việt và xuất video. Script video cần Internet và model đã tải, tự dọn media thử.

Kết quả tại máy này: 102 bài hồi quy đạt; thử desktop và các nút kiểm tra kết nối đạt; video tổng hợp 6,864 giây qua 3 đoạn thoại, dịch thật bằng OpenRouter Free, đọc tiếng Việt và xuất MP4 thành công. Dịch theo lô cũng đã kiểm tra với API thật. Bản dịch AI vẫn cần xem lại trước khi xuất bản. Chưa kiểm chứng trên video dài hoặc tài khoản tải video riêng của người dùng. `pip check` còn báo xung đột của các công cụ toàn cục có sẵn (aider-chat, patchright, selenium); các package toàn cục đó không được chỉnh sửa bởi lần cài này.

`.env`, video, model và venv không được đẩy lên GitHub. Các bài audit cũ của dự án có yêu cầu model/mẫu video riêng và không thuộc bộ kiểm thử hồi quy trên.
