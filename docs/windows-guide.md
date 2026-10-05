# Cài đặt và vận hành trên Windows

Máy đã kiểm tra ngày 05/10/2026: Intel i5-13420H (8 nhân / 12 luồng), RAM 16 GB, NVIDIA RTX 2050 4 GB, Windows 11, Python 3.12.10 và FFmpeg 9.0.1.

## Mở ứng dụng

Nhấp đúp **Douyin2TikTok AI Studio.lnk** (logo Studio) hoặc **start.bat**, chọn video MP4, chọn giọng Hoài My hoặc Nam Minh, rồi bấm **Bắt đầu dịch & phát realtime**. Chờ xử lý xong trước khi bấm xuất MP4. Thành phẩm nằm trong `workspace/outputs`. Nếu chuyển thư mục, chạy `create_shortcut.ps1` để tạo lại lối mở; `setup.ps1` cũng tạo lối mở này sau cài đặt.

Môi trường `venv`, cấu hình `.env` và model Whisper Small đã được cài ở máy này. Sau khi khởi động lại Windows không cần cài lại. Nếu chuyển sang máy khác hoặc bị thiếu thư viện, chạy **setup.bat**. Script dùng Python 3.12, tái sử dụng package hệ thống phù hợp, chỉ bổ sung package thiếu vào venv và không sửa Python toàn cục. FFmpeg và FFprobe phải có trong PATH. Dung lượng cài mới khoảng 1–2 GB.

## Cấu hình đang dùng

- Nhận tiếng Trung: Faster-Whisper **Small**, CPU **int8**, **6 luồng**; model ở `workspace/models/faster-whisper-small`.
- Giọng Việt: Edge-TTS Hoài My / Nam Minh, cần Internet.
- Dịch trên máy này: **Gemini 2.5 Flash**, key lưu trong `.env` local. **OpenRouter Free** vẫn có sẵn bằng key OpenCode. Không tải LLM dịch về RAM. Có thể đổi nhà cung cấp trong Cài đặt.
- Giảm giọng gốc: DSP trên CPU, không tải bộ tách giọng nặng. Hiệu quả tùy video; có thể còn giọng gốc hoặc ảnh hưởng nhạc nền.
- Xuất MP4: H.264 + AAC, phụ đề ASS, tùy chọn làm mờ sub gốc; giữ kích thước và tỉ lệ video nguồn.
- Không nạp model lúc mở giao diện. Lần xử lý video đầu tiên cần thời gian nạp model; tốc độ thực tế phụ thuộc video và tải máy, không đảm bảo realtime.

Cấu hình dựa trên tổng RAM 16 GB, không dựa trên RAM trống tại thời điểm cài đặt. RTX 2050 được nhận diện, nhưng pipeline mặc định chạy CPU. Chỉ đổi `DEVICE=cuda` sau khi kiểm tra driver và các thư viện CUDA/CuDNN tương thích. Models phân biệt **đã tải đủ tệp** và **thiếu runtime**; có checkpoint không đồng nghĩa model đã chạy được. VieNeu được cố định backend ONNX/CPU, 4 luồng, không tự chuyển sang Torch khi cài thêm thư viện.

`venv\Scripts\python.exe -B download_models.py` hiển thị kế hoạch tải SenseVoice Int8, VieNeu v3 Turbo cùng MOSS codec, BS-RoFormer và ba checkpoint ProPainter/RAFT/Flow. Thêm `--download` để tải khoảng 1,60 GiB; script tái sử dụng cache, kiểm tra dung lượng/checksum được upstream cung cấp và lưu manifest tại `workspace/models/download_manifest.json`. Lệnh này không đổi engine mặc định. RoFormer cần `audio_separator`/Torch; ProPainter chưa có phần suy luận tích hợp trong app, nên che phụ đề hiện vẫn dùng blur.

Nếu tải bị gián đoạn, chạy lại cùng lệnh để tiếp tục từ tệp `.download` đang có. Bộ tải kiểm tra chính xác từng khoảng byte máy chủ trả về, tải tối đa bốn đoạn 1 MiB đồng thời rồi ghi theo thứ tự; chỉ đổi tên thành model hoàn chỉnh sau khi kiểm tra dung lượng/checksum. Máy chủ không hỗ trợ tải tiếp sẽ báo lỗi và giữ phần đã tải, không tự xóa hoặc tải đè. VieNeu/MOSS dùng cơ chế cache của Hugging Face.

Lượt tải ngày 05/10/2026 đã hoàn tất toàn bộ danh sách trên và tạo manifest. Từng tệp được đối chiếu kích thước; tệp có checksum upstream được đối chiếu checksum, còn các release GitHub cũ không cung cấp digest thì ghi SHA-256 tính tại máy vào manifest để kiểm tra về sau. Bộ kiểm thử tài sản model/tải tiếp đạt 10 bài. Trạng thái đã tải không thay thế các yêu cầu runtime RoFormer và giới hạn tích hợp ProPainter nêu trên.

Ngày 05/10/2026, SenseVoice đã nhận dạng mẫu tiếng Trung 5,59 giây (0,29 giây suy luận); VieNeu đã tạo câu tiếng Việt bằng preset Trúc Ly, âm thanh 48 kHz dài 2,32 giây (1,98 giây tổng hợp, 14,47 giây tính cả nạp model). VieNeu mở được từ cache khi tắt mạng Hugging Face, có 25 preset. Đây là kiểm engine bằng mẫu ngắn; chưa phải benchmark video dài hoặc luồng UI chọn giọng VieNeu. Hai giọng Hoài My/Nam Minh ở Studio thuộc Edge-TTS, không dùng ID đó làm preset VieNeu.

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

Nếu mở nhiều phiên Chrome, đặt `MUSE_CHROME_PORT` trong `.env` bằng cổng hiện trên trang `chrome://inspect/#remote-debugging` của phiên muốn dùng, ví dụ `9222`, rồi khởi động lại tool. Giá trị `0` dùng cổng Chrome tự công bố. Cổng cụ thể chọn máy chủ kết nối của Chrome; không tự chọn hồ sơ theo tên hay màu cửa sổ. Bạn vẫn cần chấp nhận hộp thoại kết nối trong đúng phiên Chrome. Khi trang Muse tải lỗi, tool giữ tab vừa mở để lần bấm **Kết nối Muse** tiếp theo thử lại trên cùng tab.

Chế độ **Chrome riêng** lưu phiên ở `workspace/muse-profile` và đóng trình duyệt riêng khi thoát. Kết nối nội bộ chỉ nghe ở localhost, có mã xác thực riêng mỗi lần chạy và chỉ hỗ trợ đăng nhập/dịch. Runtime và hồ sơ đăng nhập không được đẩy GitHub. Driver phụ thuộc giao diện Muse nên có thể cần cập nhật khi trang thay đổi.

## Kiểm tra và xử lý lỗi

Bản Qt trên máy không giải mã H.264/AAC trực tiếp. Khi cần, ứng dụng tự tạo bản xem trước WebM VP8/Opus (tối đa 640 px, dưới 90 MB, một worker / hai luồng encoder) và dọn khi thoát. Video nguồn và MP4 xuất vẫn giữ nguyên. Nhạc nền trong Studio dùng Ogg Opus; MP4 cuối dùng AAC. Hoài My và Nam Minh là hai giọng tiếng Việt Microsoft Edge-TTS qua Internet, không phải model giọng cài tại máy.

Cài đặt buffer, giảm giọng và ducking đã được nối xuống pipeline; giọng đọc tiếp tục sau pause, timeline giữ con trỏ, Stop xóa trạng thái âm thanh đã hủy. Xuất MP4 ráp giọng tuần tự để tránh giới hạn dòng lệnh Windows. FFmpeg đang xuất có thể hủy; tác vụ AI trong thread kết thúc lượt đang chạy rồi giải phóng model. Chỉ chuyển video hoàn chỉnh vào outputs khi render thành công.

Chạy `venv\Scripts\python.exe -B check_runtime.py` để kiểm tra cài đặt ngoại tuyến. Nếu lỗi khởi động, xem tab Diagnostics hoặc `workspace/logs`. Link Douyin/TikTok có thể bị yêu cầu đăng nhập/cookie theo nền tảng; có thể tải video hợp lệ về máy rồi chọn file MP4.

Ô link tự trích URL từ nội dung Chia sẻ; các domain video được hỗ trợ có thể bỏ `https://`. Dán link mới bỏ lựa chọn file cũ. Bấm Bắt đầu tạo tác vụ ngay, rồi lần lượt hiện kết nối, tải, chuẩn bị, nhận giọng, dịch và tạo giọng. Phần trăm tải dựa trên số byte khi máy chủ cung cấp tổng dung lượng; phần trăm xử lý dựa trên số câu hoàn tất. Các bước chưa đo được hiện “Chưa có %”. Dừng hoạt động cả khi còn đang kết nối; Tạm dừng chỉ khả dụng sau khi chuẩn bị xong. Playlist bị từ chối trước khi tải các video.

Có thể dán nguyên đoạn chia sẻ Douyin vào ô **Video nguồn**, gồm tiêu đề tiếng Trung, hashtag, mã chia sẻ và nhiều dòng. Ngay khi dán, giao diện tự thay đoạn chia sẻ bằng URL sạch và hiện thông báo đã nhận link. Nội dung tự gõ vẫn giữ nguyên để sửa. Link có định dạng Markdown sao chép từ chat như `[**link**](link)` cũng được nhận; dấu gạch dưới trong link không bị đổi. Dán nội dung chỉ nhận diện link; bấm **Bắt đầu** mới tải và dịch.

### Sửa transcript cạnh video

Ở cửa sổ rộng từ 1200 px, Studio có ba cột: **nguồn video và tác vụ** bên trái, **video và âm thanh** ở giữa, **Transcript và Giọng lồng tiếng** thành hai khung riêng bên phải. Từ 960 px, transcript vẫn nằm cạnh video, còn nguồn và tác vụ chuyển xuống dưới. Danh sách câu và giọng cuộn riêng. Hình ảnh giữ tỷ lệ nguồn, không bị kéo giãn.

Thanh phát có nút phát/tạm dừng, âm lượng tổng, tắt tiếng và toàn màn hình. Âm lượng tổng áp dụng cả giọng Việt và nhạc nền; hai thanh bên dưới điều chỉnh tỷ lệ trộn từng phần. **Thư viện** liệt kê tối đa 100 video nguồn mới nhất đã lưu trong `workspace/inputs`, bỏ qua file tạm và link thư mục; bấm video chỉ chọn để xem trước, bấm **Bắt đầu** mới chạy dịch.

Khi tải, phần trăm, dung lượng đã nhận/tổng dung lượng, tốc độ và thời gian còn lại (ước tính) hiện ngay trong khung video. Nếu không có tổng dung lượng thì hiện **Chưa có %**. Studio đối chiếu trạng thái với máy chủ mỗi hai giây kể cả khi tab Tác vụ đóng; tác vụ đã dừng sẽ tắt vòng xoay. Khi mất kết nối hoặc không tìm thấy tác vụ, giao diện báo rõ và không tự nhận là đã tải xong. Phản hồi cũ không ghi đè tiến độ mới hơn.

1. Bấm **mốc thời gian** hoặc **câu gốc** để tua đến đầu câu. Câu đang phát được đánh dấu trong transcript.
2. Khi câu đã **Sẵn sàng**, bấm vào chữ cần sửa trong **bản dịch tiếng Việt**. Video tạm dừng, ô sửa mở tại vị trí chữ đã bấm; có thể sửa từng chữ hoặc cả câu.
3. Bấm **Lưu và tạo lại giọng** hoặc nhấn **Ctrl+Enter**. Studio tạo lại giọng đọc, căn vào thời lượng câu cũ rồi cập nhật phụ đề, phát lại và dữ liệu xuất video. Chỉ xác nhận lưu sau khi máy chủ xử lý thành công.
4. Bấm **Hủy sửa** hoặc nhấn **Esc** để bỏ bản nháp. Khi lỗi lưu, bản nháp vẫn được giữ để thử lại. Cập nhật realtime của câu khác không ghi đè nội dung đang sửa.

**Xuất Video Hoàn Chỉnh** bị khóa khi còn bản sửa chưa lưu hoặc đang tạo lại giọng. Lưu hoặc hủy bản sửa trước khi xuất. Mốc tua và căn giọng hiện theo **câu**, chưa có thời gian chính xác cho từng từ; vị trí chữ được bấm dùng để đặt con trỏ soạn thảo.

### Chọn và nghe thử giọng

Danh sách **Giọng lồng tiếng** luôn hiện theo từng dòng và nhóm theo nguồn, ghi rõ cần Internet hay chạy tại máy. Dùng ô tìm kiếm theo tên, vùng miền hoặc nguồn; có thể gõ không dấu. Bấm **Nghe thử** ở bất kỳ dòng nào để nghe cùng một câu mẫu, rồi bấm **Chọn** ở giọng muốn dùng. Nghe thử không thay đổi giọng đang chọn; nút **Dừng mẫu** dừng mẫu đang phát. Ứng dụng nhớ lựa chọn của bạn. Chọn giọng hoặc bắt đầu video sẽ dừng mẫu đang phát; mẫu hết hạn có thể tạo lại. Mẫu nghe thử chỉ được giữ tạm, tối đa tám mẫu trong bộ nhớ và hết hạn sau năm phút.

- **Microsoft Edge**: Hoài My và Nam Minh, cần Internet. Đây là hai giọng tiếng Việt thực tế trong danh sách dịch vụ, không phải toàn bộ giọng đa ngôn ngữ của Edge.
- **[VieNeu-TTS v3 Turbo](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo)**: 25 preset từ SDK 3.8.3 đã cài, gồm Trúc Ly, Mai Anh, Hải Đăng và các giọng vùng miền/phong cách khác. Tên và mô tả lấy từ preset của nhà phát triển; alias không được tính thành giọng mới. Runtime CPU ONNX dùng tối đa bốn luồng và chia sẻ một model giữa nghe thử và dịch video. Model/preset Apache-2.0.
- **[Piper Cake](piper-voices.md)**: năm giọng từ model tiếng Việt của Cake by VPBank, chạy trên CPU. Xem hướng dẫn riêng để cài tùy chọn và nguồn/giấy phép.

Các đường nhập link, chọn file trên máy và tải file lên đều dùng đúng giọng đã chọn. Giọng thiếu model/runtime sẽ bị vô hiệu hóa hoặc báo lỗi rõ ràng; ứng dụng không tự đổi sang giọng khác. Chất lượng và cách phát âm khác nhau theo giọng, nên nghe thử và kiểm tra bản dịch trước khi xuất.

### Tải video Douyin công khai

Dán nguyên nội dung chia sẻ vào **Video nguồn**, chờ hiện URL sạch rồi bấm **Bắt đầu**. Studio ưu tiên lấy video công khai qua đường tải miễn phí tích hợp, không cần API key, đăng nhập hoặc mở Chrome. Cách tải này dựa trên API công khai được mô tả trong [tài liệu Douyin của media-parser](https://github.com/ucmao/media-parser/blob/main/docs/parsers/douyin.md); đây không phải cam kết dịch vụ chính thức của Douyin. Khả năng tải phụ thuộc quyền truy cập video, mạng và thay đổi phía Douyin.

Studio ưu tiên tệp nguồn gốc khi Douyin cung cấp, tải nguyên byte và không tự nén hoặc hạ chất lượng. Khi không có địa chỉ nguồn gốc, bộ tải chọn bản phát có độ phân giải/bitrate cao nhất được cung cấp. Tiến độ lấy tổng dung lượng từ máy chủ media; dung lượng nguồn gốc có thể lớn hơn nhiều bản phát nén. Bộ tải kiểm tra dung lượng ổ đĩa, tính toàn vẹn và thời lượng sau khi tải; bấm Dừng để hủy và dọn file đang tải dở.

Nếu đường tải công khai không lấy được video, Studio tự thử bộ tải dự phòng `yt-dlp`, dùng cookie Douyin đã nhập nếu có. Ứng dụng không tự mở Chrome hoặc lấy phiên đăng nhập trình duyệt. Video riêng tư, đã xóa hoặc bị giới hạn có thể vẫn không tải được; khi đã có tệp video hợp lệ, có thể chọn MP4 trên máy để tiếp tục.

Lượt kiểm tra ngày 05/10/2026 dùng đúng link `_lAiSDH0bK8`: đã lấy mẫu **15 giây, 1080p, khoảng 28,5 MB** từ nguồn chất lượng gốc, giữ nguyên luồng hình/tiếng bằng remux. Chưa tải toàn bộ tệp nguồn khoảng **19,94 GB**. Sau một lượt Gemini trả 503, lần thử lại hoàn tất nhận diện, dịch và đọc cả 2 câu. Sửa một câu qua API đã tạo âm thanh mới, tăng revision và giữ nguyên mốc đầu/cuối; xuất MP4 H264/AAC **1920×1080, 15,017 giây, khoảng 11,3 MB** thành công. Bản tải nguồn giữ nguyên chất lượng; bản xuất lồng tiếng phải render lại để ghép âm thanh và phụ đề. Kết quả này chỉ xác nhận đoạn mẫu, chưa kiểm chứng xử lý toàn bộ video dài.

Giới hạn hiện tại: Qt trên máy này cần bản xem trước WebM để phát H264/AAC. Bản xem trước có trần 90 MB nên video gần 3 giờ này chưa phát trọn vẹn trong Studio; tải nguồn thành công không đồng nghĩa preview video dài đã được hỗ trợ. Vùng che phụ đề Trung hiện cố định theo tỷ lệ khung hình, cần kiểm tra có che đúng vị trí chữ của từng video trước khi xuất.

### Cookie Douyin khi cần đăng nhập

Đăng nhập trong Chrome không tự chia sẻ phiên với Studio. Khi kết nối mạng đã hoạt động nhưng bộ tải báo 403 hoặc cần cookie mới, mở **Cài đặt → Tải video Douyin → Nâng cao: cookie cho video cần đăng nhập** và nhập tệp cookie định dạng **Netscape `.txt`** (tối đa 1 MiB). Nếu đang dùng tiện ích xuất cookie trong Chrome, chỉ xuất trang Douyin đang đăng nhập; chọn tệp trực tiếp trong Studio, không gửi nội dung cookie vào chat. Sau khi nhập, quay lại Studio và thử tải link. Nhập tệp thành công chỉ xác nhận đã lưu cookie hợp lệ về định dạng/thời hạn, không xác nhận Douyin cho phép tải hoặc tài khoản đã xác thực.

Ứng dụng chỉ giữ cookie của `douyin.com`, `iesdouyin.com` và các tên miền con tương ứng, bỏ cookie hết hạn và của trang khác. Bản sao cục bộ nằm trong `workspace/private`, không được đưa lên Git, không trả giá trị cookie qua API/log hoặc đường phục vụ video. Bộ tải chỉ dùng bản sao này cho link Douyin; thao tác **Xóa cookie đã nhập** chỉ xóa bản sao của Studio, không đăng xuất Chrome hay xóa tệp bạn đã xuất. Nếu phiên hết hạn, xuất và nhập lại. Không tự đọc hồ sơ Chrome hoặc thay đổi quyền trình duyệt.

Ở lượt thử trước khi bổ sung đường tải công khai ngày 05/10/2026, sau khi bật VPN: link ngắn chuyển hướng sau 1,9 giây; trang video đầy đủ trả HTTP 200 sau 1,1 giây. Bộ tải cũ không có cookie nhận HTTP 403; chưa xác nhận tải thật bằng phiên đã nhập. VPN đã giải quyết timeout của lượt thử đó nhưng không đảm bảo tải được mọi video.

Nếu kết nối nguồn không tiến triển trong 60 giây, app kết thúc tác vụ và giữ thông báo lỗi ở Studio/Tasks. Ở lần thử bộ tải cũ ngày 05/10/2026, link Douyin đã được nhận và tạo task sau 31 ms nhưng kết nối hết thời gian chờ. Kết quả kiểm tra đường tải mới được ghi ở mục **Tải video Douyin công khai** phía trên. Diagnostics giữ xuống dòng, phân biệt lỗi/cảnh báo và có nút sao chép log nguyên văn qua clipboard của Windows.

Lượt tải HTTP từ máy chủ cục bộ qua bản desktop `pythonw` đã tạo task sau 32 ms, tải xong, tách âm thanh và hoàn tất video im lặng 1 giây sau 8,53 giây; video/máy chủ thử đã được dọn. Kiểm tra clipboard thật qua QtWebChannel cũng đạt. Kết quả này xác nhận đường tải và nối pipeline, không thay cho kiểm tra truy cập Douyin qua Internet.

Bản PyAV được giới hạn dưới 19 vì Faster-Whisper 1.2.1 vẫn gọi API đã bị xóa ở PyAV 19: [upstream issue](https://github.com/SYSTRAN/faster-whisper/issues/1492).

Kiểm thử hồi quy không cần mạng/model:

```powershell
venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests/test_local_runtime.py tests/test_windows_media.py tests/test_portable_pipeline.py tests/test_local_api.py tests/test_opencode_client.py tests/test_opencode_translation.py tests/test_opencode_api.py tests/test_openrouter_client.py tests/test_openrouter_api.py tests/test_gemini_client.py tests/test_gemini_api.py tests/test_muse_service.py tests/test_muse_api.py tests/test_cloud_translation.py tests/test_edge_tts_retry.py tests/test_media_preview.py tests/test_downloader.py tests/test_url_progress.py tests/test_model_assets.py
```

Hai script kiểm tra riêng: `tests/smoke_desktop.py` kiểm tra phát hình/âm thanh thật trong QtWebEngine, pause/seek/resume, năm tab ở 1024×640 và transcript cạnh video ở 1920×1080/1024×640. Kiểm tra transcript dùng phản hồi máy chủ giả lập để xác nhận vị trí con trỏ khi bấm chữ, lưu bản sửa và đồng bộ phụ đề trong DOM thật; không xác nhận chất lượng dịch hoặc tổng hợp giọng của dịch vụ. `tests/smoke_local_video.py` tạo câu nói tiếng Trung tổng hợp, nhận giọng thật, dịch, đọc tiếng Việt và xuất video. Script video cần Internet và model đã tải, tự dọn media thử; dùng `--keep-output` nếu muốn giữ MP4 cuối.

Lượt chỉnh giao diện theo mẫu ngày 05/10/2026 đạt **65 bài Node**, **27 bài Python và 15 subtest** cho API cục bộ/desktop; một test tạo symlink thật được bỏ qua do quyền Windows, các kiểm tra loại link bằng mô phỏng vẫn đạt. Qt smoke đạt ở các chiều rộng 1920, 1672, 1366, 1280 và 1024 px, gồm dán link, 42,4% tiến độ, sửa transcript, nghe mẫu, phát/tua/tạm dừng, và vị trí lớp che trên vùng ảnh thực. Tiến độ dùng dữ liệu giả lập có kiểm soát, không tải lại video lớn. Fullscreen đã kiểm tra handler Qt và luồng JavaScript; chưa xác nhận thao tác vào fullscreen bằng chuột thật trong phiên người dùng.

Sau khi bổ sung transcript, `node --test tests/test_ui_interactions.mjs` đạt **50 bài**, gồm giữ bản nháp, xử lý lỗi lưu, chống lưu lặp, đổi audio revision, khóa xuất khi chưa lưu và dọn trạng thái tải khi dừng. Desktop smoke cũng đạt kiểm tra transcript/giọng mẫu/phát video nêu trên. Bộ Python liên quan đến resolver, worker tải, sửa transcript, tiến độ và cookie đạt **187 bài**. Các kết quả kiểm thử pipeline dịch thực tế trong đoạn tiếp theo là từ những lượt trước, không phải xác nhận toàn bộ video Douyin của lượt tải mới.

Kết nối Chrome đang dùng có bộ kiểm thử `node --test tests/test_chrome_connection.mjs tests/test_muse_chat_adapter.mjs`: kiểm tra giữ nguyên các tab khác, ngắt kết nối đúng lúc, xử lý đóng tool trong khi đang kết nối, mở đoạn chat phụ trên giao diện tiếng Việt và đợi nội dung trả lời xuất hiện. Cần người dùng bật quyền Chrome để kiểm tra Muse thật; các kiểm thử giả lập không xác nhận quyền tài khoản.

Lượt hồi quy mới trên máy này đạt 214 bài Python và 51 bài Node cho kết nối Chrome, Muse và UI. Các sửa tiếp theo về playlist, điều khiển, log và model cũng qua bộ kiểm tra tập trung 50 bài. Desktop smoke kiểm tra 42% tải, bước chưa có %, tác vụ tải xuất hiện sớm và log nhiều dòng/sao chép đều đạt. Desktop phát được video qua bản xem trước WebM, pause/seek/resume và phát BGM Ogg thành công. Video tổng hợp 6,864 giây qua 3 đoạn thoại đã chạy thật với OpenRouter Free ở lần trước và Gemini 2.5 Flash ở lần này, đọc tiếng Việt và xuất MP4 thành công. Lượt kiểm tra lại sau sửa tải/progress và cài model cũng đạt với 3 câu, MP4 169.874 byte; đã tự dọn file mẫu. Gemini 2.5 Flash đã kiểm tra dịch một đoạn và theo lô bằng API thật, giữ nguyên ID/thời gian/metadata; dịch vụ có lúc trả 503, lần thử lại đã thành công. Edge-TTS thử lại tối đa 3 lần nếu dịch vụ kết thúc mà không trả âm thanh. Bản dịch AI vẫn cần xem lại trước khi xuất bản. Chưa kiểm chứng trên video dài hoặc tài khoản tải video riêng của người dùng. `pip check` còn báo xung đột của các công cụ toàn cục có sẵn (aider-chat, patchright, selenium); các package toàn cục đó không được chỉnh sửa bởi lần cài này.

`.env`, video, model và venv không được đẩy lên GitHub. Các bài audit cũ của dự án có yêu cầu model/mẫu video riêng và không thuộc bộ kiểm thử hồi quy trên.
