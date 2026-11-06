# Phục hồi thoại Hàn–Trung cho dự án 2b5ef22b

## Nguyên nhân đã xác định

Video dài 395,782676 giây chứa cả thoại Hàn và thoại Trung, có đoạn giọng địa phương. Chữ Trung trên hình không chứng minh âm thanh cũng là tiếng Trung. Nhánh nhận dạng/đối chiếu hiện có chủ yếu giả định nguồn Trung nên sinh chữ sai khi nghe tiếng Hàn. Thử lại cùng bản nguồn đó không giải quyết được lỗi.

Ví dụ ở khoảng 202–212 giây, hai bộ nhận dạng độc lập nghe được `야 왜 이래 우리 친구잖아?`. Lượt dịch trực tiếp Hàn → Việt và kiểm tra nghĩa riêng đã tạo được “Này, sao vậy, là bạn bè với nhau mà?”. Đây là kiểm tra khả năng dịch, chưa chứng nhận toàn bộ bản nguồn.

Bản cũ còn có các mẩu nguồn 0,14 giây và 0,04 giây quanh 104,6 giây. Chúng không phải thời lượng phù hợp cho câu lồng tiếng hoàn chỉnh. Việc sửa bộ dịch hoặc thử TTS lại không thể khắc phục nguồn và cách chia câu sai.

Ngoài ra, nhận dạng nguyên khối có bật VAD bỏ sót một số vùng thoại; ép toàn bộ video sang tiếng Hàn cũng sai vì sẽ phiên âm nhầm thoại Trung thành chữ Hàn. Vì vậy phục hồi dùng cửa sổ ngắn, ngôn ngữ theo đoạn, đối chiếu thêm nguồn độc lập và OCR.

## Phương án đang thực hiện

- Giữ nguyên dự án cũ và các sửa tay.
- Lưu riêng kết quả nhận dạng, từ và thời gian, các phương án khác nhau và chỗ chưa chắc chắn.
- Dịch theo nhãn ngôn ngữ từng câu, kiểm tra nghĩa/xưng hô bằng lượt riêng; không coi kiểm tra nghĩa là xác minh ASR.
- Lưu checkpoint từng nhóm dịch, rồi tạo dự án phục hồi riêng và phụ đề Việt.
- Tạo giọng từ lời Việt đã lưu; không đưa nguồn Hàn qua bộ sửa lời tiếng Trung. Câu lỗi riêng không chặn các câu sau.

## Mã và kiểm tra

`core/multilingual_translation.py`: Hàn/Trung/trộn hai ngôn ngữ; bảo toàn ID, nguồn, thời gian; chặn chữ nguồn còn sót trong lời Việt; kiểm tra nghĩa riêng.

`scripts/translate_recovered_source.py`: dịch theo nhóm với checkpoint và ngữ cảnh hai phía.

`scripts/publish_multilingual_recovery.py`: lưu dự án mới, phụ đề và giọng; giữ cờ nguồn chưa xác minh; không ghi đè dự án gốc.

Kiểm tra ban đầu: 55 bài test của dịch đa ngôn ngữ, lưu dự án phục hồi và nhận diện ngôn ngữ đều qua. Kết quả chạy thực tế và các phần còn thiếu sẽ bổ sung sau khi hoàn thành lượt phục hồi.
