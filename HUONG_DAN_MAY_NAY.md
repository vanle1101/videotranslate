# Chạy trên Windows, RAM 16 GB

Máy đã kiểm tra ngày 04/10/2026: Intel i5-13420H (8 nhân / 12 luồng), RAM 16 GB, NVIDIA RTX 2050 4 GB, Windows 11, Python 3.12.10 và FFmpeg 9.0.1.

## Mở ứng dụng

Nhấp đúp **start.bat**, chọn video MP4, chọn giọng Hoài My hoặc Nam Minh, rồi bấm **Bắt đầu dịch & phát realtime**. Chờ xử lý xong trước khi bấm xuất MP4. Thành phẩm nằm trong `workspace/outputs`.

Môi trường `venv`, cấu hình `.env` và model Whisper Small đã được cài ở máy này. Sau khi khởi động lại Windows không cần cài lại. Nếu chuyển sang máy khác hoặc bị thiếu thư viện, chạy **setup.bat**. Script dùng Python 3.12, tái sử dụng package hệ thống phù hợp, chỉ bổ sung package thiếu vào venv và không sửa Python toàn cục. FFmpeg và FFprobe phải có trong PATH. Dung lượng cài mới khoảng 1–2 GB.

## Cấu hình đang dùng

- Nhận tiếng Trung: Faster-Whisper **Small**, CPU **int8**, **6 luồng**; model ở `workspace/models/faster-whisper-small`.
- Giọng Việt: Edge-TTS Hoài My / Nam Minh, cần Internet.
- Dịch trên máy này: **Gemini 2.5 Flash**, key lưu trong `.env` local. **OpenRouter Free** vẫn có sẵn bằng key OpenCode. Không tải LLM dịch về RAM. Có thể đổi nhà cung cấp trong Cài đặt.
- Giảm giọng gốc: DSP trên CPU, không tải bộ tách giọng nặng. Hiệu quả tùy video; có thể còn giọng gốc hoặc ảnh hưởng nhạc nền.
- Xuất MP4: H.264 + AAC, phụ đề ASS, tùy chọn làm mờ sub gốc; giữ kích thước và tỉ lệ video nguồn.
- Không nạp model lúc mở giao diện. Lần xử lý video đầu tiên cần thời gian nạp model; tốc độ thực tế phụ thuộc video và tải máy, không đảm bảo realtime.

Cấu hình dựa trên tổng RAM 16 GB, không dựa trên RAM trống tại thời điểm cài đặt. RTX 2050 được nhận diện, nhưng AI đang chạy CPU để hoạt động với driver hiện có mà không cài CUDA/PyTorch. Chỉ đổi `DEVICE=cuda` sau khi kiểm tra driver và các thư viện CUDA/CuDNN tương thích. VieNeu, SenseVoice, RoFormer và ProPainter là tùy chọn chưa cài; giao diện Models hiển thị tình trạng tệp thực tế.

Gemini được đưa lên đầu Cài đặt, model hiện chọn là `gemini-2.5-flash`. Khi thử ngày 04/10/2026, model này dịch thành công. Các bản 3.5–3.8 gặp quá tải; 3.6 có lúc trả lời ngắn được nhưng hai lần thử pipeline video trả lỗi 503. Bản 3.1 Pro preview báo quota miễn phí bằng 0. Đây là lựa chọn hoạt động tại thời điểm kiểm tra, không phải cam kết chất lượng hoặc hạn mức. Có thể nhập model khác, lưu rồi bấm kiểm tra kết nối. Lỗi API hoặc JSON chưa hoàn chỉnh sẽ được báo rõ, không âm thầm đổi dịch vụ. Key không gửi về giao diện hay đưa lên GitHub.

## Dùng key miễn phí có sẵn trong OpenCode

Máy mới chưa cấu hình chọn **OpenRouter Free** bằng kết nối `OpenRouter-Free` có sẵn trong OpenCode. Đây là dịch vụ OpenRouter, không phải OpenCode Zen. Chỉ chấp nhận model có hậu tố `:free`; không tự đổi provider hoặc model trả phí khi lỗi. Lần thử API trên máy trả bản dịch thành công và báo chi phí 0. Hạn mức và khả dụng phụ thuộc dịch vụ: [tài liệu bản miễn phí](https://openrouter.ai/docs/guides/routing/model-variants/free).

Key được tự đọc từ `%USERPROFILE%\.local\share\opencode\auth.json` (hoặc đường dẫn `XDG_DATA_HOME` nếu có). Không cần chép key vào code hay nhập lại. Có thể đặt `OPENROUTER_API_KEY` trong `.env` local để ghi đè. Key không xuất hiện trong API Cài đặt, giao diện hoặc GitHub.

Trong **Cài đặt**, chọn **OpenRouter Free**, lưu model rồi bấm **Kiểm tra OpenRouter Free**. Nếu chuyển máy, kết nối OpenRouter trong OpenCode hoặc đặt key vào `.env` local. Phần nhận giọng nói vẫn dùng Whisper Small trên CPU, còn tạo giọng Việt dùng Edge-TTS qua mạng.

Tùy chọn **OpenCode Zen Free** dùng [CLI chính thức](https://opencode.ai/docs/cli/#run), key `opencode` hoặc `OPENCODE_API_KEY`, session riêng không có quyền chạy công cụ, không chia sẻ và tự dọn file tạm. Khi kiểm tra ngày 04/10/2026, Zen trả **403 FreeTierError** ngay cả qua CLI gốc 1.18.30; vấn đề tương tự đã được báo ở [upstream](https://github.com/anomalyco/opencode/issues/49756). Vì vậy Zen không được đặt làm mặc định. Không giả lập header hoặc lách chặn dịch vụ.

## Muse qua tài khoản của bạn (thử nghiệm)

Tích hợp dựa trên driver MIT của [Muse-Chat-MCP](https://github.com/duclm1x1/Muse-Chat-MCP), cố định commit `6c00e8cf1bf2fb6718740e0c20d3578fb4c47286`. Đây là thao tác giao diện web qua Chrome, không phải API chính thức và không tự cấp token. Khả năng truy cập, model và hạn mức do tài khoản Muse/Meta quyết định; chưa có căn cứ xác nhận quảng cáo “1 tỷ token”.

1. Chạy `setup_muse.bat` một lần nếu chuyển máy. Cần Node.js 20+ và Chrome; chỉ thêm khoảng 13 MB Playwright, không tải trình duyệt mới. Máy này đã cài phần kết nối.
2. Trong **Cài đặt → Muse (thử nghiệm)**, chọn **Chrome đang dùng** nếu đã đăng nhập Muse ở Chrome cá nhân, rồi lưu. Trên Chrome hiện đại, mở `chrome://inspect/#remote-debugging` và bật **Allow remote debugging for this browser instance**. Bấm **Kết nối Muse** trong tool, rồi chấp nhận hộp thoại kết nối của Chrome. Không cần đăng nhập lại hoặc khởi động lại trình duyệt. Đây là [cơ chế kết nối Chrome được Playwright hỗ trợ](https://playwright.dev/mcp/configuration/browser-extension).
3. Bấm **Kiểm tra đăng nhập** và **Thử dịch Muse**. Khi thành công, chọn nhà cung cấp **Muse** rồi lưu để dịch video qua tài khoản đó. Việc đăng nhập thành công ở Chrome cá nhân không đồng nghĩa hồ sơ Chrome riêng của tool đã đăng nhập.
4. Có thể chọn **Chrome riêng của tool** nếu muốn tách phiên; đăng nhập một lần trong cửa sổ do tool mở. Chế độ này không cần bật kết nối trình duyệt cá nhân.

Chế độ **Chrome đang dùng** mở một tab Muse mới bằng phiên đã đăng nhập, không sao chép cookie, key hoặc hồ sơ Chrome. Quyền remote debugging cho ứng dụng local khả năng điều khiển trình duyệt; phần tích hợp này chỉ thao tác tab Muse do nó tạo. Đóng tool sẽ đóng tab đó và ngắt kết nối, giữ Chrome và các tab khác. Có thể tắt remote debugging sau khi dùng. Tool không tự bật quyền này và không âm thầm chuyển sang Chrome khác khi kết nối lỗi.

Chế độ **Chrome riêng** lưu phiên ở `workspace/muse-profile` và đóng trình duyệt riêng khi thoát. Kết nối nội bộ chỉ nghe ở localhost, có mã xác thực riêng mỗi lần chạy và chỉ hỗ trợ đăng nhập/dịch. Runtime và hồ sơ đăng nhập không được đẩy GitHub. Driver phụ thuộc giao diện Muse nên có thể cần cập nhật khi trang thay đổi.

## Kiểm tra và xử lý lỗi

Chạy `venv\Scripts\python.exe -B check_runtime.py` để kiểm tra cài đặt ngoại tuyến. Nếu lỗi khởi động, xem tab Diagnostics hoặc `workspace/logs`. Link Douyin/TikTok có thể bị yêu cầu đăng nhập/cookie theo nền tảng; có thể tải video hợp lệ về máy rồi chọn file MP4.

Bản PyAV được giới hạn dưới 19 vì Faster-Whisper 1.2.1 vẫn gọi API đã bị xóa ở PyAV 19: [upstream issue](https://github.com/SYSTRAN/faster-whisper/issues/1492).

Kiểm thử hồi quy không cần mạng/model:

```powershell
venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests/test_local_runtime.py tests/test_windows_media.py tests/test_portable_pipeline.py tests/test_local_api.py tests/test_opencode_client.py tests/test_opencode_translation.py tests/test_opencode_api.py tests/test_openrouter_client.py tests/test_openrouter_api.py tests/test_gemini_client.py tests/test_gemini_api.py tests/test_muse_service.py tests/test_muse_api.py tests/test_cloud_translation.py tests/test_edge_tts_retry.py
```

Hai script kiểm tra riêng: `tests/smoke_desktop.py` kiểm tra QtWebEngine và thao tác chọn file; `tests/smoke_local_video.py` tạo câu nói tiếng Trung tổng hợp, nhận giọng thật, dịch, đọc tiếng Việt và xuất video. Script video cần Internet và model đã tải, tự dọn media thử.

Kết nối Chrome đang dùng có bộ kiểm thử `node --test tests/test_chrome_connection.mjs`: kiểm tra giữ nguyên các tab khác, ngắt kết nối đúng lúc và xử lý đóng tool trong khi đang kết nối. Cần người dùng bật quyền Chrome để kiểm tra Muse thật; các kiểm thử giả lập không xác nhận quyền tài khoản.

Kết quả tại máy này: 148 bài hồi quy và 94 subtests đạt; thử desktop và các nút kiểm tra kết nối đạt; video tổng hợp 6,864 giây qua 3 đoạn thoại, đã chạy thật với OpenRouter Free ở lần trước và Gemini 2.5 Flash ở lần này, đọc tiếng Việt và xuất MP4 thành công. Gemini 2.5 Flash đã kiểm tra dịch một đoạn và theo lô bằng API thật, giữ nguyên ID/thời gian/metadata. Edge-TTS thử lại tối đa 3 lần nếu dịch vụ kết thúc mà không trả âm thanh. Bản dịch AI vẫn cần xem lại trước khi xuất bản. Chưa kiểm chứng trên video dài hoặc tài khoản tải video riêng của người dùng. `pip check` còn báo xung đột của các công cụ toàn cục có sẵn (aider-chat, patchright, selenium); các package toàn cục đó không được chỉnh sửa bởi lần cài này.

`.env`, video, model và venv không được đẩy lên GitHub. Các bài audit cũ của dự án có yêu cầu model/mẫu video riêng và không thuộc bộ kiểm thử hồi quy trên.
