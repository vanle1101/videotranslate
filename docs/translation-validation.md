# Kiểm tra dịch video — 06/10/2026

## Mẫu kiểm tra

- Nội dung chia sẻ: `做自媒体这些常见的谣言 你都信过哪些？`, tác giả `野生军师🧢`.
- Link: https://v.douyin.com/FUSSxhVKiZw/ — video ID `7676801479801388282`.
- Tệp nguồn có sẵn: H264/AAC, 2160×3840, 32,601667 giây, 102.286.058 byte. Đã đối chiếu dung lượng và các mẫu byte đầu/giữa/cuối với nguồn tải, không tải thêm bản sao.
- Thành phẩm cục bộ: `workspace/outputs/douyin-7676801479801388282-vi.mp4` và `.srt` cùng tên; media không đưa lên Git.
- Thành phẩm: H264/AAC stereo, 2160×3840, 32,633333 giây, 79.077.222 byte.
- SHA-256 MP4: `164724a234cb03f7b29b5fc3583bc2963765739d8706616c42c70e7612886771`.

## Lỗi phát hiện và sửa

| Nguồn | Sai lệch từng gặp | Bản đã rà |
| --- | --- | --- |
| 点赞反而会更推流 | ASR đọc thành 反悔; dịch thành bỏ like/hối hận | Thả tim còn giúp tăng đề xuất. |
| 反而会限制你的流量 | ASR đọc 限制 thành 陷入 | Ngược lại còn bị hạn chế tiếp cận/bóp tương tác. |
| 一秒钟的爆款视频 | Biến độ dài video thành thời gian để nổi tiếng | Video viral dài một giây. |
| 还有不会进粉丝群… | Biến “chưa biết vào” thành “không vào được” | Còn ai chưa biết vào nhóm fan… |

Đã rà 10 câu thoại dựa trên bản nhận giọng, phản hồi phân tích video đã có và 37 dòng OCR đo tại máy; đối chiếu ảnh nguồn và thành phẩm ở đầu, giữa, cuối. Câu kết bị cắt nên giữ dấu lửng, không thêm lời kêu gọi. Thành phẩm có chỉnh biên tập qua luồng lưu transcript/tạo lại giọng; đây **không phải** bằng chứng rằng bản dịch tự động đã đúng cả 10 câu mà không cần sửa. Không ghi các câu mẫu thành quy tắc thay thế trong mã sản phẩm.

Mốc câu lấy từ Faster-Whisper trên toàn bộ âm thanh. OCR đo vị trí/thời gian thay vì để Gemini ước lượng. Tiêu đề giữ trọn câu suốt khoảng xuất hiện; phụ đề dài chia trang cân đối. Bản đã biên tập giữ vùng che chữ nguồn, hiển thị thoại một lần ở đáy hình. Mask nằm dưới chữ Việt trong export.

## API và phạm vi xác nhận

Gemini 2.5 Flash trả kết quả cho một số lượt kiểm tra, sau đó trả HTTP 429. Nhánh OpenRouter miễn phí đã chạy được đến dịch/TTS/export, nhưng đối chiếu nghĩa phát hiện lỗi ngay cả sau khi model tự kiểm tra. Vì vậy nhánh này nay sửa nguồn có dẫn chứng OCR trước, dịch và kiểm tra nghĩa riêng, đồng thời luôn giữ câu thoại ở trạng thái cần rà. Chữ trên hình từ nhánh dự phòng cũng chưa được duyệt, giữ nguyên vùng chữ nguồn khi xem trước/xuất; sửa transcript không xác nhận thay cho bản dịch OCR. Model/provider được ghi đúng trong transcript, không nhận kết quả dự phòng là Gemini đã xem/nghe video.

Hai model OpenRouter thay thế thử nghiệm chưa trả được kết quả dùng được (giới hạn lượt/phản hồi trống). Gemini Flash Lite trả 404, Gemini 3 Flash Preview trả 503; cấu hình model chính không được đổi. Chưa xác nhận lại luồng Gemini tự động hoàn chỉnh sau khi key bị giới hạn.

## Kiểm thử

- 245 bài Python đạt, 1 bài bỏ qua do quyền symlink Windows, 70 subtest đạt trong nhóm kiểm tra tập trung.
- 82 bài JavaScript đạt.
- Desktop Qt smoke đạt: H264/AAC qua preview, phát/tạm dừng/tua, nhạc nền, giọng mẫu, dán nguyên nội dung chia sẻ, tiến độ, tác vụ, sao chép log và sửa transcript; bố cục 1024–1920 px. Luồng thao tác UI dùng dữ liệu thử có kiểm soát, không thay thế kiểm tra chất lượng dịch.
- MP4 thành phẩm giải mã toàn bộ cả hình và tiếng không lỗi; kích thước và thời lượng được kiểm tra bằng ffprobe. Nhận dạng lại âm thanh Việt xác nhận có thoại theo trình tự, nhưng còn lỗi phiên âm nên không dùng nó làm chứng nhận phát âm chính xác tuyệt đối.

Phiên Studio cũ của người dùng được giữ nguyên. Mã mới có hiệu lực sau lần mở lại ứng dụng; không tự sửa phiên đã xử lý. Chưa xác nhận mọi video, mọi giọng hoặc video dài đều hoạt động đúng.
