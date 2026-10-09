# Cài đặt và vận hành trên Windows

Cấu hình ghi nhận ngày 05/10/2026: Intel i5-13420H (8 nhân / 12 luồng), RAM 16 GB, NVIDIA RTX 2050 4 GB, Windows 11, Python 3.12.10 và FFmpeg 9.0.1. Đây là cấu hình của lượt kiểm tra đó, không phải kết quả đo lại phần cứng mỗi lần mở ứng dụng.

## Mở ứng dụng

Nhấp đúp **Douyin2TikTok AI Studio.lnk** (logo Studio) hoặc **start.bat**, chọn video MP4, chọn giọng Hoài My hoặc Nam Minh, rồi bấm **Bắt đầu dịch**. Chờ xử lý xong trước khi bấm **Xuất video MP4**. Thành phẩm nằm trong `workspace/outputs`. Nếu chuyển thư mục, chạy `scripts/create_shortcut.ps1` để tạo lại lối mở; `scripts/setup.ps1` cũng tạo lối mở này sau cài đặt.

Bấm **X** chỉ ẩn cửa sổ xuống khay hệ thống; tác vụ tải, dịch, kiểm tra và xuất video tiếp tục chạy. Bấm biểu tượng Studio ở khay (có thể nằm trong nút **^**) hoặc mở lại shortcut để hiện cửa sổ. Chuột phải biểu tượng, chọn **Thoát hoàn toàn** để tắt; nếu còn tác vụ, Studio hỏi trước khi dừng. Khi máy không có khay hệ thống khả dụng, X sẽ đóng ứng dụng như bình thường.

Môi trường `venv`, cấu hình `.env` và model Whisper Small đã được cài ở máy này. Sau khi khởi động lại Windows không cần cài lại. Nếu chuyển sang máy khác hoặc bị thiếu thư viện, chạy **setup.bat**. Script dùng Python 3.12, tái sử dụng package hệ thống phù hợp, chỉ bổ sung package thiếu vào venv và không sửa Python toàn cục. FFmpeg và FFprobe phải có trong PATH. Dung lượng cài mới khoảng 1–2 GB.

Launcher chạy `scripts/check_runtime.py` trước khi mở Studio. Kiểm tra này bao gồm thư viện OCR/OpenCV và các giới hạn phiên bản NumPy, SciPy, PyAV mà adapter hiện hỗ trợ; có thư viện import được chưa đủ để coi là tương thích. Nếu kiểm tra báo lỗi, xem đúng package và phiên bản ghi trong thông báo. Không cần cài lại toàn bộ môi trường chỉ vì một dependency lỗi. Kiểm tra này chạy offline, không gọi Muse và không xác nhận chất lượng dịch.

## Thư mục làm việc

Mã xử lý nằm trong `core`, giao diện trong `static` và `templates`, tài liệu trong `docs`, bộ kiểm thử trong `tests`. Các script cài đặt, tải model, kiểm tra runtime và tạo shortcut được gom vào `scripts`; `start.bat`, `setup.bat` và shortcut vẫn mở từ thư mục gốc.

- `workspace/inputs`: video nguồn.
- `workspace/outputs`: thành phẩm, phụ đề và báo cáo kiểm tra đi kèm.
- `workspace/temp`: dữ liệu thử và file trung gian; các script benchmark không ghi vào outputs.
- `workspace/cache`: dữ liệu xử lý của phiên; không dọn khi đang dịch, sửa thoại hoặc xuất video.
- `workspace/logs`: nhật ký chẩn đoán.
- `workspace/models`, `workspace/tools`, `venv`: model, công cụ và thư viện đang dùng.

Đợt dọn ngày 06/10/2026 gom cache thử, bản xem trước hết phiên, ảnh kiểm tra và bản xuất đã được thay thế vào `workspace/temp/to-delete-2026-10-06`. Đây là các file đã chuyển, không tạo bản sao; có thể xóa cả thư mục này khi không cần nữa. Bản đã rà `douyin-7676801479801388282-vi-reviewed.mp4`, báo cáo đi kèm và các video mẫu được giữ trong outputs. `.env`, hồ sơ đăng nhập và dữ liệu workspace không đưa lên Git.

## Cấu hình đang dùng

- Nhận tiếng Trung: Faster-Whisper **Small**, CPU **int8**, **6 luồng**; model ở `workspace/models/faster-whisper-small`.
- Giọng Việt: Edge-TTS Hoài My / Nam Minh, cần Internet.
- Dịch trên máy này: **OpenCode Zen · Muse Spark Free**, model `muse-spark-1.3-contributor-free`, dùng đăng nhập OpenCode đã có. Không cần Chrome hoặc Gemini. Tình trạng gọi dịch vụ được ghi bên dưới.
- Giảm giọng gốc: DSP trên CPU, không tải bộ tách giọng nặng. Hiệu quả tùy video; có thể còn giọng gốc hoặc ảnh hưởng nhạc nền.
- Xuất MP4: H.264 + AAC, phụ đề ASS, tùy chọn làm mờ sub gốc; giữ kích thước và tỉ lệ video nguồn.
- Không nạp model lúc mở giao diện. Lần xử lý video đầu tiên cần thời gian nạp model; tốc độ thực tế phụ thuộc video và tải máy, không đảm bảo realtime.

Cấu hình dựa trên tổng RAM 16 GB, không dựa trên RAM trống tại thời điểm cài đặt. RTX 2050 được nhận diện, nhưng pipeline mặc định chạy CPU. Chỉ đổi `DEVICE=cuda` sau khi kiểm tra driver và các thư viện CUDA/CuDNN tương thích. Models phân biệt **đã tải đủ tệp** và **thiếu runtime**; có checkpoint không đồng nghĩa model đã chạy được. VieNeu được cố định backend ONNX/CPU, 4 luồng, không tự chuyển sang Torch khi cài thêm thư viện.

`venv\Scripts\python.exe -B scripts/download_models.py` hiển thị kế hoạch tải SenseVoice Int8, VieNeu v3 Turbo cùng MOSS codec, BS-RoFormer và ba checkpoint ProPainter/RAFT/Flow. Thêm `--download` để tải khoảng 1,60 GiB; script tái sử dụng cache, kiểm tra dung lượng/checksum được upstream cung cấp và lưu manifest tại `workspace/models/download_manifest.json`. Lệnh này không đổi engine mặc định. RoFormer cần `audio_separator`/Torch; ProPainter chưa có phần suy luận tích hợp trong app, nên che phụ đề hiện vẫn dùng blur.

Nếu tải bị gián đoạn, chạy lại cùng lệnh để tiếp tục từ tệp `.download` đang có. Bộ tải kiểm tra chính xác từng khoảng byte máy chủ trả về, tải tối đa bốn đoạn 1 MiB đồng thời rồi ghi theo thứ tự; chỉ đổi tên thành model hoàn chỉnh sau khi kiểm tra dung lượng/checksum. Máy chủ không hỗ trợ tải tiếp sẽ báo lỗi và giữ phần đã tải, không tự xóa hoặc tải đè. VieNeu/MOSS dùng cơ chế cache của Hugging Face.

Phân biệt giọng nói dùng Sherpa-ONNX 1.13.8, Pyannote segmentation và WeSpeaker tiếng Trung; đây là xử lý audio, phần dịch vẫn dùng Muse. Chạy `venv\Scripts\python.exe -B scripts/download_diarization_models.py --download` một lần để tải khoảng 32 MiB. Script kiểm tra kích thước/SHA256 và giữ tệp đã có; không cần tải lại mỗi video. Các nhãn `voice-*` là dự đoán chưa hiệu chuẩn, không xác nhận giới tính, quan hệ hay xưng hô. SQLite trong cache từng dự án giữ nhãn và bằng chứng theo đoạn để Resume. `DIARIZATION_ENABLED=False` bỏ bước này; thiếu model/runtime sẽ giữ lời nguồn cùng cảnh báo, không tự xác nhận người nói. Giới hạn mặc định hai luồng CPU và 120 giây mỗi đoạn. Không xóa cache/checkpoint nếu còn muốn tiếp tục dự án.

Lượt tải ngày 05/10/2026 đã hoàn tất toàn bộ danh sách trên và tạo manifest. Từng tệp được đối chiếu kích thước; tệp có checksum upstream được đối chiếu checksum, còn các release GitHub cũ không cung cấp digest thì ghi SHA-256 tính tại máy vào manifest để kiểm tra về sau. Bộ kiểm thử tài sản model/tải tiếp đạt 10 bài. Trạng thái đã tải không thay thế các yêu cầu runtime RoFormer và giới hạn tích hợp ProPainter nêu trên.

Ngày 05/10/2026, SenseVoice đã nhận dạng mẫu tiếng Trung 5,59 giây (0,29 giây suy luận); VieNeu đã tạo câu tiếng Việt bằng preset Trúc Ly, âm thanh 48 kHz dài 2,32 giây (1,98 giây tổng hợp, 14,47 giây tính cả nạp model). VieNeu mở được từ cache khi tắt mạng Hugging Face, có 25 preset. Đây là kiểm engine bằng mẫu ngắn; chưa phải benchmark video dài hoặc luồng UI chọn giọng VieNeu. Hai giọng Hoài My/Nam Minh ở Studio thuộc Edge-TTS, không dùng ID đó làm preset VieNeu.

Gemini là lựa chọn tùy chọn. Model `gemini-2.5-flash` đã dịch được trong các lượt trước nhưng gặp giới hạn hạn mức. Nhà cung cấp mặc định hiện là OpenCode Muse Spark Free; tác vụ OpenCode không tự chuyển sang OpenRouter khi lỗi. Key không gửi về giao diện hay đưa lên GitHub.

## Muse Spark Free trong OpenCode

Chọn **OpenCode Zen · Muse Spark Free** và model `muse-spark-1.3-contributor-free`. Danh mục CLI ngày 06/10/2026 ghi tên **Muse Spark 1.3 Free**, trạng thái active, giá input/output bằng 0. Bản `muse-spark-1.3` không nằm trong danh sách miễn phí được Studio cho phép. Luồng ASR + OCR và luồng chỉ dịch tiếng nói đều dùng model đã chọn; UI ghi đúng OpenCode.

Studio dùng [CLI chính thức](https://opencode.ai/docs/cli/#run), agent `plan` có sẵn và key `opencode` đã lưu hoặc `OPENCODE_API_KEY`. Mỗi lượt dịch chạy trong phiên riêng, tách cấu hình và dữ liệu phiên khỏi OpenCode đang mở, có kiểm soát quyền công cụ và gắn một shell native tạm chỉ trả lỗi để chặn lệnh ngoài luồng dịch. Đây không phải sandbox hệ điều hành. Muse trong OpenCode độc lập với tùy chọn Muse qua Chrome bên dưới. Studio gửi văn bản ASR/OCR để dịch, không nhận đó là Muse đã trực tiếp xem video.

Kiểm tra thật ngày 06/10/2026 đã dịch thành công bằng Muse Spark 1.3 Free qua CLI 1.18.30 với agent `plan`. Luồng sửa nguồn ASR/OCR → dịch → kiểm tra nghĩa hoàn tất hai câu trong 51,16 giây: thả tim giúp tăng đề xuất; bình luận thu hút người xem tài khoản. Hai câu vẫn giữ trạng thái **Cần kiểm tra**, chưa tạo video mới từ lượt này. Các lần gọi trước bị **FreeTierError** với cấu hình cầu nối khác không chứng minh Muse hoặc phiên OpenCode của người dùng không hoạt động. Studio giữ nguyên provider khi lỗi, không tự đổi sang OpenRouter hoặc model trả phí; hạn mức và khả dụng vẫn do dịch vụ quyết định.

## OpenRouter Free (lựa chọn riêng)

Có thể chủ động chọn **OpenRouter Free** bằng kết nối `OpenRouter-Free` có sẵn trong OpenCode. Đây là dịch vụ OpenRouter, không phải OpenCode Zen. Chỉ chấp nhận model có hậu tố `:free`; không tự đổi provider hoặc model trả phí khi lỗi. Các lượt thử trước đã trả bản dịch và báo chi phí 0, sau đó gặp giới hạn. Hạn mức và khả dụng phụ thuộc dịch vụ: [tài liệu bản miễn phí](https://openrouter.ai/docs/guides/routing/model-variants/free).

Key được tự đọc từ `%USERPROFILE%\.local\share\opencode\auth.json` (hoặc đường dẫn `XDG_DATA_HOME` nếu có). Không cần chép key vào code hay nhập lại. Có thể đặt `OPENROUTER_API_KEY` trong `.env` local để ghi đè. Key không xuất hiện trong API Cài đặt, giao diện hoặc GitHub.

Trong **Cài đặt**, chọn **OpenRouter Free**, lưu model rồi bấm **Kiểm tra OpenRouter Free**. Nếu chuyển máy, kết nối OpenRouter trong OpenCode hoặc đặt key vào `.env` local. Phần nhận giọng nói vẫn dùng Whisper Small trên CPU, còn tạo giọng Việt dùng Edge-TTS qua mạng.

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

Chạy `venv\Scripts\python.exe -B scripts/check_runtime.py` để kiểm tra cài đặt ngoại tuyến. Nếu lỗi khởi động, xem tab Diagnostics hoặc `workspace/logs`. Link Douyin/TikTok có thể bị yêu cầu đăng nhập/cookie theo nền tảng; có thể tải video hợp lệ về máy rồi chọn file MP4.

Ô link tự trích URL từ nội dung Chia sẻ; các domain video được hỗ trợ có thể bỏ `https://`. Dán link mới bỏ lựa chọn file cũ. Bấm Bắt đầu tạo tác vụ ngay, rồi lần lượt hiện kết nối, tải, chuẩn bị, nhận giọng, dịch và tạo giọng. Phần trăm tải dựa trên số byte khi máy chủ cung cấp tổng dung lượng; phần trăm xử lý dựa trên số câu hoàn tất. Các bước chưa đo được hiện “Chưa có %”. Dừng hoạt động cả khi còn đang kết nối; Tạm dừng chỉ khả dụng sau khi chuẩn bị xong. Playlist bị từ chối trước khi tải các video.

Có thể dán nguyên đoạn chia sẻ Douyin vào ô **Video nguồn**, gồm tiêu đề tiếng Trung, hashtag, mã chia sẻ và nhiều dòng. Ngay khi dán, giao diện tự thay đoạn chia sẻ bằng URL sạch và hiện thông báo đã nhận link. Nội dung tự gõ vẫn giữ nguyên để sửa. Link có định dạng Markdown sao chép từ chat như `[**link**](link)` cũng được nhận; dấu gạch dưới trong link không bị đổi. Dán nội dung chỉ nhận diện link; bấm **Bắt đầu** mới tải và dịch.

### Dịch có đối chiếu hình và tiếng

Với Gemini, bật **AI đọc chữ và kiểm chứng lời nói** trước khi bắt đầu. Faster-Whisper nhận diện toàn bộ âm thanh để giữ mốc câu thật. RapidOCR chạy tại máy, lấy mẫu hình 3 lần/giây để đo chữ và vị trí; thời điểm đổi chữ có thể lệch khoảng một khoảng lấy mẫu. Gemini nhận video nén kèm âm thanh theo đoạn khoảng 24 giây (tối đa 45 giây), bản nhận giọng và OCR để sửa nhận dạng, dịch theo ngữ cảnh, rồi kiểm tra lần hai bằng video. Model chỉ dịch/phân loại các vùng OCR đã đo, không được tạo tọa độ/thời gian mới. Ngữ cảnh đoạn trước chuyển sang đoạn tiếp theo. Mỗi đoạn thường dùng hai lượt Gemini; có sử dụng hạn mức và mất thêm thời gian.

Với **OpenRouter Free**, tùy chọn này dùng Faster-Whisper và RapidOCR tại máy, sau đó gửi văn bản tới model `:free` đã cấu hình để sửa nguồn, dịch và kiểm tra nghĩa riêng. Không yêu cầu key Gemini, không nén/gửi video tới Gemini. Lời thoại có bằng chứng OCR rõ cùng thời điểm được xử lý; câu thiếu căn cứ hoặc còn mâu thuẫn vẫn cần nghe lại. Nhãn **OpenRouter · bản chép + OCR** phân biệt rõ với Gemini xem/nghe video. Model miễn phí vẫn phụ thuộc hạn mức và khả dụng của OpenRouter; ứng dụng không tự chuyển model trả phí. Khi tắt tùy chọn, ứng dụng dùng luồng dịch âm thanh như trước.

Nếu chọn Gemini và gặp lỗi hạn mức, Studio vẫn có thể dùng OpenRouter làm dự phòng. Trong trường hợp chuyển dịch vụ dự phòng này, toàn bộ bản dịch cần được duyệt trước khi sử dụng. Lỗi key, bộ lọc nội dung hoặc yêu cầu không hợp lệ không kích hoạt nhánh dự phòng. File nén gửi Gemini tối đa 14 MB mỗi đoạn được dọn sau khi xử lý; video nguồn giữ nguyên.

Câu thiếu căn cứ được ghi **Cần kiểm tra**, kèm lý do trong transcript. Toàn bộ câu từ nhánh OpenRouter dự phòng cũng cần rà: kiểm thử thực tế phát hiện model vẫn tự tin với câu sai nghĩa dù đã tự kiểm tra. Studio vẫn tạo và phát giọng nháp cho câu cần kiểm tra, kèm phụ đề theo tiếng đọc và nhãn **Bản nháp · Cần kiểm tra**. Bạn nghe trước, bấm bản dịch để sửa rồi **Lưu và tạo lại giọng**. Nếu bản nháp đã đúng, giữ nguyên chữ và bấm lưu để xác nhận. Chỉ phần xuất MP4 cuối cùng đợi các câu này được xác nhận. Câu chưa có nội dung tiếng Việt để đọc vẫn cho video chạy qua; có thể bấm **Nghe gốc** rồi nhập lời Việt. Chữ trên hình không đọc chắc, không xác định được vùng chữ hoặc được dịch qua nhánh OpenRouter dự phòng sẽ giữ nguyên, không tự che. Sửa lời thoại không duyệt thay bản dịch chữ trên hình; hiện chưa có trình duyệt OCR riêng.

Phụ đề hiển thị **trọn câu đang được đọc**, không chạy từng chữ và không gộp lời đáp của nhân vật tiếp theo lên trước. Faster-Whisper giữ các câu riêng, dùng mốc từ, dấu kết câu và khoảng nghỉ để tách lời thoại; không còn chia video thành khối tám giây rồi ghép nhiều câu lại. Chưa có mô hình nhận diện người nói riêng nên cảnh nói chồng tiếng hoặc không có khoảng nghỉ vẫn cần rà transcript.

**Tự đặt vị trí sub** mặc định bật. Nếu OCR xác định được phụ đề thoại Trung, Studio giữ chữ Trung và đặt lời Việt chữ đen trên ô vàng nhỏ sát dưới vùng đó; sát mép dưới không đủ chỗ thì đặt phía trên. Nếu không có vùng phụ đề chắc chắn, lời Việt nằm trong ô trắng nhỏ phía dưới. Tiêu đề và chữ khác giữ nguyên. Tắt tự đặt vị trí để luôn dùng vị trí đáy hình. Preview và MP4 dùng chung dữ liệu bố cục; không còn dải blur ngang hay tự che chữ nguồn.

Giọng Edge sử dụng mốc từ do dịch vụ trả về, đối chiếu với âm thanh sau khi căn tốc độ. Khoảng lặng ở đầu/cuối âm thanh tổng hợp được cắt gọn trước khi tính tốc độ để câu đáp ngắn không bị ép nhanh vì thời gian im lặng. VieNeu/Piper chưa cung cấp mốc từ: đầu/cuối tiếng được đo trên PCM, mốc bên trong câu là ước tính và ghi rõ `audio-onset-estimate`. Sửa transcript sẽ tạo lại cả giọng và mốc sub.

Model nhận dạng và OCR được giải phóng sau xử lý để giảm RAM. Cần xem lại nội dung, vị trí và tốc độ đọc trước khi xuất; có API không bảo đảm mọi câu đúng. Thay đổi áp dụng cho tác vụ mới sau khi mở lại ứng dụng; không tự sửa phiên đã xử lý. Kết quả kiểm tra mẫu thật và giới hạn dịch miễn phí được ghi trong [báo cáo kiểm tra dịch](translation-validation.md).

### Sửa transcript cạnh video

Ở cửa sổ rộng từ 1200 px, Studio có ba cột: **nguồn video và tác vụ** bên trái, **video và âm thanh** ở giữa, **Transcript và Giọng lồng tiếng** thành hai khung riêng bên phải. Từ 960 px, transcript vẫn nằm cạnh video, còn nguồn và tác vụ chuyển xuống dưới. Danh sách câu và giọng cuộn riêng. Hình ảnh giữ tỷ lệ nguồn, không bị kéo giãn.

Thanh phát có nút phát/tạm dừng, âm lượng tổng, tắt tiếng và toàn màn hình. Âm lượng tổng áp dụng cả giọng Việt và nhạc nền; hai thanh bên dưới điều chỉnh tỷ lệ trộn từng phần. **Thư viện** liệt kê tối đa 100 video nguồn mới nhất đã lưu trong `workspace/inputs`, bỏ qua file tạm và link thư mục; bấm video chỉ chọn để xem trước, bấm **Bắt đầu** mới chạy dịch.

Khi tải, phần trăm, dung lượng đã nhận/tổng dung lượng, tốc độ và thời gian còn lại (ước tính) hiện ngay trong khung video. Nếu không có tổng dung lượng thì hiện **Chưa có %**. Studio đối chiếu trạng thái với máy chủ mỗi hai giây kể cả khi tab Tác vụ đóng; tác vụ đã dừng sẽ tắt vòng xoay. Khi mất kết nối hoặc không tìm thấy tác vụ, giao diện báo rõ và không tự nhận là đã tải xong. Phản hồi cũ không ghi đè tiến độ mới hơn.

1. Bấm **mốc thời gian** hoặc **câu gốc** để tua đến đầu câu. Bấm **Nghe gốc** để phát tiếng nguồn trong khoảng câu, kể cả câu **Cần kiểm tra**; giọng Việt và nhạc nền tạm dừng. Phát hết câu sẽ tự dừng.
2. Khi câu đã **Sẵn sàng** hoặc **Cần kiểm tra**, bấm vào chữ cần sửa trong **bản dịch tiếng Việt**. Video tạm dừng, ô sửa mở tại vị trí chữ đã bấm; có thể sửa từng chữ hoặc cả câu.
3. Bấm **Lưu và tạo lại giọng** hoặc nhấn **Ctrl+Enter**. Studio tạo lại giọng đọc, căn vào thời lượng câu cũ rồi cập nhật phụ đề, phát lại và dữ liệu xuất video. Chỉ xác nhận lưu sau khi máy chủ xử lý thành công.
4. Bấm **Hủy sửa** hoặc nhấn **Esc** để bỏ bản nháp. Khi lỗi lưu, bản nháp vẫn được giữ để thử lại. Cập nhật realtime của câu khác không ghi đè nội dung đang sửa.

Nếu nghe lại xác nhận câu **Cần kiểm tra** thực sự không có người nói, bấm **Không có lời thoại**. Thao tác này bỏ lời và giọng nháp trong đúng khoảng câu, giữ thời gian và chữ nguồn để đối chiếu; có thể sửa lại thành lời thoại sau đó. Để trống ô sửa rồi lưu thông thường vẫn bị từ chối nhằm tránh xóa nhầm lời. Xác nhận im lặng không duyệt hoặc che chữ OCR trên hình.

**Xuất Video Hoàn Chỉnh** bị khóa khi còn câu cần kiểm tra, bản sửa chưa lưu hoặc đang tạo lại giọng. Lưu hoặc hủy bản sửa trước khi xuất. Mốc tua vẫn theo **câu**; mốc sub lấy theo giọng tổng hợp như mô tả ở trên. Vị trí chữ được bấm dùng để đặt con trỏ soạn thảo.

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

Giới hạn hiện tại: Qt trên máy này cần bản xem trước WebM để phát H264/AAC. Studio tạo bản xem trước theo phần đã chuẩn bị (mặc định 24 giây, tối đa 10 phút và tối đa 90 MB), nên video gần 3 giờ chưa phát trọn vẹn trong Studio; tải nguồn thành công không đồng nghĩa preview toàn bộ video dài đã được hỗ trợ. MP4 xuất vẫn dùng toàn bộ nguồn. Khi tắt đọc hình ảnh, phụ đề dùng vị trí đáy hình và giữ nguyên chữ nguồn. Video không có luồng âm thanh chưa được chế độ này hỗ trợ.

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

Kết quả kiểm thử hiện tại, task ID, output thực tế, số bài hồi quy và giới hạn chưa kiểm chứng được ghi trong [Runtime QA report](RUNTIME_QA_REPORT.md). Đọc mục có thời gian gần nhất: một kết quả PASS của phiên bản cũ hoặc một bộ test dùng mock không xác nhận toàn bộ luồng sản xuất của phiên bản đang chạy.

Các lượt Qt smoke ngày 05/10/2026 đã kiểm tra bố cục ở 1920, 1672, 1366, 1280 và 1024 px, sửa transcript, nghe mẫu/tiếng gốc và phát/tua/tạm dừng. Tiến độ trong smoke dùng dữ liệu giả lập; các lần chạy thật với provider và kiểm tra MP4 được ghi riêng trong báo cáo QA. Fullscreen đã kiểm tra handler Qt và JavaScript; kết quả đó không chứng minh thao tác bằng chuột thật trong phiên người dùng.

Kết nối Chrome đang dùng có bộ kiểm thử `node --test tests/test_chrome_connection.mjs tests/test_muse_chat_adapter.mjs`: kiểm tra giữ nguyên các tab khác, ngắt kết nối đúng lúc, xử lý đóng tool trong khi đang kết nối, mở đoạn chat phụ trên giao diện tiếng Việt và đợi nội dung trả lời xuất hiện. Cần người dùng bật quyền Chrome để kiểm tra Muse thật; các kiểm thử giả lập không xác nhận quyền tài khoản.

Các mẫu tổng hợp ngắn dùng OpenRouter/Gemini trong lượt kiểm tra cũ đã xuất MP4; đó không phải chứng nhận kết quả Muse hoặc video dài hiện tại. Edge-TTS có retry khi dịch vụ kết thúc mà không trả âm thanh. Bản dịch chưa đủ bằng chứng vẫn được ghi rõ trong transcript và báo cáo đi kèm; kiểm thử thực thi thành công không biến nội dung chưa chắc thành đã xác minh.

Môi trường dùng package hệ thống có thể phát sinh xung đột theo thời gian. Dùng `venv\Scripts\python.exe -X utf8 -m pip check` để xem dependency hiện có và `venv\Scripts\python.exe -B scripts/check_runtime.py` để kiểm tra các yêu cầu thực tế của Studio. Không xem xung đột riêng của aider-chat/selenium là lỗi Studio, nhưng lỗi của package Studio dùng như NumPy/SciPy cần được xử lý trước khi chạy. Không tự sửa hoặc gỡ công cụ toàn cục.

`.env`, video, model và venv không được đẩy lên GitHub. Các bài audit cũ của dự án có yêu cầu model/mẫu video riêng và không thuộc bộ kiểm thử hồi quy trên.
