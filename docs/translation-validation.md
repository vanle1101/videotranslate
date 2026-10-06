# Kiểm tra dịch video — 06/10/2026

## Cấu hình mới nhất: Muse Spark Free qua OpenCode

Theo lựa chọn của người dùng, Studio dùng `LLM_PROVIDER=opencode` và `OPENCODE_MODEL=muse-spark-1.3-contributor-free`. Danh mục chính thức `opencode models opencode --verbose` trên máy liệt kê Muse Spark 1.3 Free active, giá input/output 0. Đã nối provider này vào luồng ASR + OCR, giữ provenance đúng và ngăn tự gọi OpenRouter khi OpenCode thất bại. Muse qua Chrome là lựa chọn khác, không cần dùng cho luồng này.

Ba lần kiểm tra thật bằng CLI 1.18.30 (adapter text-only, agent mặc định, đăng nhập OpenCode đã lưu) đều trả `FreeTierError`; chưa có câu dịch thành công qua Muse trên máy để xác nhận chất lượng. Không tạo video mới từ kết quả lỗi. Studio đã mở lại và API cấu hình xác nhận đúng provider/model, nhận được đăng nhập và CLI. Không thay cấu hình OpenCode toàn máy hoặc dùng model trả phí.

214 bài Python, 28 subtest, 83 bài JavaScript và Qt desktop smoke đạt; một bài symlink bỏ qua theo quyền Windows. Test mới kiểm tra URL/file/upload qua OpenCode, sửa nguồn/dịch/kiểm tra bằng đúng model, metadata/tên provider, từ chối bản Muse trả phí và giữ nguyên provider khi lỗi. Kiểm thử giả lập xác nhận đường nối ứng dụng; chúng không thay thế lần gọi dịch vụ thật đang bị từ chối.

## Lượt mới nhất: phụ đề theo lượt thoại và vị trí chữ nguồn

Đã đối chiếu trực quan hai video TikTok được chỉ định: `@douyinsub4/video/7559200419537259783` và `@1.ting.trung.mi.n/video/7611469031224167700`. Yêu cầu hiện tại là hiện trọn lời đang được đọc, không đưa câu đáp tiếp theo lên sớm. Khi có phụ đề Trung, giữ chữ nguồn và đặt lời Việt đen trên ô vàng bên dưới; nếu không đủ chỗ thì đặt phía trên. Khi không đo được vùng phụ đề nguồn đáng tin cậy, dùng ô trắng nhỏ ở đáy hình. Không dùng dải blur tự động.

Mẫu kiểm tra là đoạn 15 giây có sẵn `workspace/inputs/douyin-preview-7688769264395767049.mp4`, thuộc link `https://v.douyin.com/_lAiSDH0bK8/`. Không tải lại bản gốc lớn. Bản mới tách 14 lượt thoại thay cho hai khối gộp ở kết quả cũ. Faster-Whisper nhận dạng toàn bộ âm thanh một lần và giữ mốc từ; tách câu theo dấu câu, khoảng nghỉ và nhãn người nói nếu nguồn đã có. Chưa có mô hình phân biệt người nói độc lập, nên không khẳng định tự tách đúng mọi nhân vật trong mọi video.

Edge-TTS cung cấp mốc từng từ; mốc phụ đề được chuyển theo tốc độ audio cuối, giới hạn trong phần tiếng thực sự phát. Đã sửa lỗi khoảng lặng ở đầu/cuối TTS làm câu ngắn như “Hả?” bị ép quá nhanh. Các engine không cung cấp mốc từ vẫn dùng ước lượng trong khoảng tiếng đo được và ghi rõ nguồn thời gian. Preview và export dùng chung kế hoạch vị trí, câu và thời điểm phụ đề.

Lượt OpenRouter miễn phí trên mẫu này gặp dẫn chứng nguồn không hợp lệ, sai ID câu, sau đó hết hạn mức. Kết quả sửa nguồn không hợp lệ nay giữ nguyên bản ASR và đánh dấu cần rà; không tự coi nội dung đó là bản đã xác minh. Thành phẩm dưới đây dùng câu đã biên tập qua luồng sửa transcript và tạo lại giọng. Đây không phải bằng chứng rằng model miễn phí tự dịch đúng toàn bộ. Mẫu hiện dùng một giọng Hoài My; chưa xác nhận số giọng trong âm thanh hai video TikTok.

- MP4: `workspace/outputs/dialogue-7688769264395767049-vi.mp4`.
- Phụ đề: `workspace/outputs/dialogue-7688769264395767049-vi.srt`.
- H264/AAC, 1920×1080, 15,016667 giây, 11.983.595 byte.
- SHA-256: `68a01057f9c8aeec328b5a1b14a1f25d948ad8b07dfa4d78f76669d436740e98`.

Đã giải mã toàn bộ MP4 bằng FFmpeg không lỗi và xem các khung hình trước tiếng, trong các câu ngắn, lúc đổi lượt và câu cuối. Các bản xuất có sẵn vẫn được giữ; không sửa chúng thành kết quả mới. Media thành phẩm không đưa lên Git.

Rà độc lập đã sửa thêm việc reconnect giữ audio revision cũ, giọng trước phát qua câu được xác nhận im lặng, word timestamp làm trùng khoảng câu, metadata từ thiếu làm mất chữ, và ghi audio chưa hoàn tất. Vị trí OCR được xác minh riêng bằng dữ liệu local và lời nguồn cùng thời điểm; việc đặt hộp không tự duyệt bản dịch OCR. Caption cố định trong một lượt thoại nhưng tránh mọi vùng phụ đề nguồn giao nhau với lượt đó.

Lượt kiểm tra cuối: **321 bài Python đạt, 19 subtest đạt**, một bài symlink bỏ qua do quyền Windows; **83 bài JavaScript đạt**. Qt desktop smoke đạt kiểm tra toàn câu/đổi lượt, nền vàng/trắng, giữ chữ nguồn, phát/tạm dừng/tua/nghe gốc, chỉnh transcript, lựa chọn xuất, dán link, tiến độ và danh sách 32 giọng. Smoke dùng dữ liệu kiểm soát, không phải chứng nhận chất lượng dịch tự động. Backend thử nghiệm đã dừng và media thử của smoke đã được dọn.

Lệnh dọn các file tạm kiểm chứng `dialogue-*`, `caption-reference-*`, script render và cache `dialogue-validation` của lượt này bị bộ duyệt tự động từ chối (`blocked by policy`); chúng vẫn còn tại máy và không được đưa lên Git. Không thử lại bằng công cụ khác. Các tệp thành phẩm và media có sẵn được giữ nguyên.

## Lượt trước: OpenRouter trực tiếp và phụ đề viền vàng

Đã chuyển cấu hình tại máy sang `openrouter-free`, model `inclusionai/ling-3.0-flash-sante:free`. Luồng này dùng Faster-Whisper và RapidOCR tại máy, gửi văn bản để sửa nguồn có dẫn chứng, dịch và kiểm tra nghĩa. Không khởi tạo Gemini, không gửi video tới Gemini và không cần key Gemini. Dịch vụ miễn phí vẫn có hạn mức và phụ thuộc model còn được cung cấp; ứng dụng không tự chuyển sang model trả phí.

Mẫu vẫn là `https://v.douyin.com/FUSSxhVKiZw/`, video `7676801479801388282`. Dùng lại nguồn có sẵn, không tải bản sao. Lượt thử đầu phát hiện phản hồi JSON thiếu trường bắt buộc; đã thêm tối đa một lượt yêu cầu sửa cấu trúc cho từng bước, giữ nguyên mọi cờ cần rà từ phản hồi trước. Lượt chạy lại hoàn tất phân tích: một câu đủ bằng chứng để xử lý, chín câu cần duyệt. Bản nháp còn lỗi như trộn chữ Trung vào lời Việt và rút ngắn sai câu cuối; các câu đó không tự tạo giọng hoặc cho phép xuất.

Rà mã sau lượt chạy còn phát hiện so khớp gần đúng có thể bỏ qua chữ phủ định hoặc khác biệt số lượng, và kiểm tra riêng từng batch có thể bỏ sót bằng chứng OCR ở batch khác. Đã chuyển sang đối chiếu nguyên chữ theo thứ tự và kiểm tra sau khi ghép các batch; thêm bảy trường hợp hồi quy. Mọi cờ chưa chắc chắn từ các bước trước vẫn được giữ.

Thành phẩm cuối được rà và sửa cả mười câu qua luồng lưu transcript/tạo lại giọng Edge-TTS Hoài My, rồi xuất. Đây là bản có biên tập, không phải xác nhận model tự dịch đúng toàn bộ. Câu kết vẫn giữ dấu lửng vì video nguồn bị cắt. Không đưa câu mẫu thành quy tắc thay thế trong mã sản phẩm.

- MP4: `workspace/outputs/douyin-7676801479801388282-vi-free.mp4`.
- Phụ đề: `workspace/outputs/douyin-7676801479801388282-vi-free.srt`.
- H264/AAC stereo, 2160×3840, 32,633333 giây, 84.196.464 byte.
- SHA-256: `639880a1a8e3545e3b01b6e7641b850bdc0f9a160cd4557e8e4499f5de88eae1`.
- Chữ vàng đậm, viền đen, nền trong, tối đa hai dòng ở phía dưới. Bản này giữ nguyên chữ/hình nguồn, không có vùng che OCR.

Đã kiểm tra thông số bằng ffprobe, giải mã toàn bộ luồng hình và tiếng bằng FFmpeg không lỗi, xem khung hình đã render để kiểm tra vị trí và kiểu chữ. Nhóm hồi quy cuối đạt 260 bài Python và 32 subtest; nhóm API/transcript đạt 71 bài và 15 subtest, bỏ qua một bài symlink do quyền Windows. Các nhóm này có bài trùng nhau, không cộng tổng. JavaScript đạt 87 bài. Qt smoke đạt kiểm tra kiểu phụ đề, trạng thái mặc định, payload xuất, phát/tạm dừng/tua, nghe gốc, danh sách giọng, dán link, sao chép log, sửa transcript và năm kích thước cửa sổ. Backend thử nghiệm đã dừng và media tạm của smoke đã được dọn.

Studio đang mở được giữ nguyên; cần mở lại để nạp mã mới. Các bản xuất cũ bên dưới vẫn được giữ làm kết quả của lượt trước.

## Lượt trước: Gemini và nhánh dự phòng

### Mẫu kiểm tra

- Nội dung chia sẻ: `做自媒体这些常见的谣言 你都信过哪些？`, tác giả `野生军师🧢`.
- Link: https://v.douyin.com/FUSSxhVKiZw/ — video ID `7676801479801388282`.
- Tệp nguồn có sẵn: H264/AAC, 2160×3840, 32,601667 giây, 102.286.058 byte. Đã đối chiếu dung lượng và các mẫu byte đầu/giữa/cuối với nguồn tải, không tải thêm bản sao.
- Thành phẩm cục bộ: `workspace/outputs/douyin-7676801479801388282-vi.mp4` và `.srt` cùng tên; media không đưa lên Git.
- Thành phẩm: H264/AAC stereo, 2160×3840, 32,633333 giây, 79.077.222 byte.
- SHA-256 MP4: `164724a234cb03f7b29b5fc3583bc2963765739d8706616c42c70e7612886771`.

### Lỗi phát hiện và sửa

| Nguồn | Sai lệch từng gặp | Bản đã rà |
| --- | --- | --- |
| 点赞反而会更推流 | ASR đọc thành 反悔; dịch thành bỏ like/hối hận | Thả tim còn giúp tăng đề xuất. |
| 反而会限制你的流量 | ASR đọc 限制 thành 陷入 | Ngược lại còn bị hạn chế tiếp cận/bóp tương tác. |
| 一秒钟的爆款视频 | Biến độ dài video thành thời gian để nổi tiếng | Video viral dài một giây. |
| 还有不会进粉丝群… | Biến “chưa biết vào” thành “không vào được” | Còn ai chưa biết vào nhóm fan… |

Đã rà 10 câu thoại dựa trên bản nhận giọng, phản hồi phân tích video đã có và 37 dòng OCR đo tại máy; đối chiếu ảnh nguồn và thành phẩm ở đầu, giữa, cuối. Câu kết bị cắt nên giữ dấu lửng, không thêm lời kêu gọi. Thành phẩm có chỉnh biên tập qua luồng lưu transcript/tạo lại giọng; đây **không phải** bằng chứng rằng bản dịch tự động đã đúng cả 10 câu mà không cần sửa. Không ghi các câu mẫu thành quy tắc thay thế trong mã sản phẩm.

Mốc câu lấy từ Faster-Whisper trên toàn bộ âm thanh. OCR đo vị trí/thời gian thay vì để Gemini ước lượng. Tiêu đề giữ trọn câu suốt khoảng xuất hiện; phụ đề dài chia trang cân đối. Bản đã biên tập giữ vùng che chữ nguồn, hiển thị thoại một lần ở đáy hình. Mask nằm dưới chữ Việt trong export.

### API và phạm vi xác nhận

Gemini 2.5 Flash trả kết quả cho một số lượt kiểm tra, sau đó trả HTTP 429. Nhánh OpenRouter miễn phí đã chạy được đến dịch/TTS/export, nhưng đối chiếu nghĩa phát hiện lỗi ngay cả sau khi model tự kiểm tra. Vì vậy nhánh này nay sửa nguồn có dẫn chứng OCR trước, dịch và kiểm tra nghĩa riêng, đồng thời luôn giữ câu thoại ở trạng thái cần rà. Chữ trên hình từ nhánh dự phòng cũng chưa được duyệt, giữ nguyên vùng chữ nguồn khi xem trước/xuất; sửa transcript không xác nhận thay cho bản dịch OCR. Model/provider được ghi đúng trong transcript, không nhận kết quả dự phòng là Gemini đã xem/nghe video.

Hai model OpenRouter thay thế thử nghiệm chưa trả được kết quả dùng được (giới hạn lượt/phản hồi trống). Gemini Flash Lite trả 404, Gemini 3 Flash Preview trả 503; cấu hình model chính không được đổi. Chưa xác nhận lại luồng Gemini tự động hoàn chỉnh sau khi key bị giới hạn.

### Kiểm thử

- 245 bài Python đạt, 1 bài bỏ qua do quyền symlink Windows, 70 subtest đạt trong nhóm kiểm tra tập trung.
- 82 bài JavaScript đạt.
- Desktop Qt smoke đạt: H264/AAC qua preview, phát/tạm dừng/tua, nhạc nền, giọng mẫu, dán nguyên nội dung chia sẻ, tiến độ, tác vụ, sao chép log và sửa transcript; bố cục 1024–1920 px. Luồng thao tác UI dùng dữ liệu thử có kiểm soát, không thay thế kiểm tra chất lượng dịch.
- MP4 thành phẩm giải mã toàn bộ cả hình và tiếng không lỗi; kích thước và thời lượng được kiểm tra bằng ffprobe. Nhận dạng lại âm thanh Việt xác nhận có thoại theo trình tự, nhưng còn lỗi phiên âm nên không dùng nó làm chứng nhận phát âm chính xác tuyệt đối.

Phiên Studio cũ của người dùng được giữ nguyên. Mã mới có hiệu lực sau lần mở lại ứng dụng; không tự sửa phiên đã xử lý. Chưa xác nhận mọi video, mọi giọng hoặc video dài đều hoạt động đúng.
