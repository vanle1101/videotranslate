# Sửa xưng hô theo ngữ cảnh — 13/10/2026

Dự án `265796c3`, 195 câu. Đã áp dụng xác nhận của người dùng: bố–con, mẹ–con; giữ lời đùa đổi vai ở câu 153–159.

## Thay đổi chương trình

- Policy xưng hô revision 11 áp dụng chung lúc dịch, kiểm định và rút gọn để đọc. Giữ chuỗi hỏi–đáp và lời gọi xuất hiện sau câu đang xét; phân biệt người sở hữu với người thứ ba được nhắc đến.
- Đối chiếu đại từ thực tế với vai có bằng chứng ngay cả khi AI báo chắc chắn. Trước đây `uncertain=false` có thể bỏ qua đối chiếu này.
- Review gate revision 3. Cache kiểm định đổi theo mã/policy; các kết luận xưng hô cũ được rà lại khi tiếp tục xử lý, giữ câu sửa tay.
- Quy tắc giữ lời đùa đổi vai có căn cứ, không lan vai đùa sang các cảnh khác.

## Kiểm chứng

- 405 kiểm thử về xưng hô, cache, kiểm định, rút gọn và bằng chứng nguồn đã qua.
- 392 kiểm thử bổ sung về policy, tiếp tục pipeline, sửa thoại và xác nhận người nói đã qua (có trùng một phần với nhóm trên; không cộng hai số).
- Thử AI thật chỉ bằng nguồn Trung đã xác định đúng ID66 self=con/listener=mẹ và ID67 self=mẹ/listener=con. Xem `context-roles-live-probe.json`.
- 41 câu được sửa bằng API chính thức; lời và giọng được lưu theo giao dịch. Toàn bộ 41 WAV tải được, giải mã được, có tín hiệu âm thanh; không còn timing_issue ở các câu này.
- Giữ nguyên chữ nguồn của 195 câu, lời của 154 câu còn lại, toàn bộ đoạn đùa 153–159 và các lời kể.
- Edge-TTS có một lần trả rỗng ở câu118; thử lại thành công, dữ liệu cũ được giữ cho đến khi bản mới lưu xong.
- Thoát sạch lúc 08:35:30, mở lại backend ở cổng 57144 lúc 08:36:32. Kiểm tra sau khởi động giữ đủ 195 câu; lời, revision và SHA-256 của cả 41 WAV sửa khớp tuyệt đối trước khi đóng app. Xem `context-roles-verified-after-restart.json`.

## Phạm vi còn lại

- Câu39 (ID38) vốn chưa có lời Việt/giọng; vẫn để cảnh báo, không tự xác nhận im lặng.
- Những câu nguồn hoặc phân vai chưa chắc vẫn giữ trạng thái cần kiểm tra. Các sửa đổi này không phải xác nhận toàn bộ nội dung video đã hoàn hảo.
- MP4 xuất trước lần sửa này chưa chứa 41 lời/giọng mới. Bản mới hiện nằm trong dự án và phần xem trước; cần xuất lại để có MP4 cập nhật.

## Lời đã sửa

| Câu | Trước | Sau |
|---|---|---|
| 33 | Tôi 19 tuổi. | Con 19 tuổi. |
| 37 | Tôi cứ tưởng là. | Con cứ tưởng là... |
| 38 | Hồi trẻ bạn có thể đẹp trai hơn chút. | Hồi trẻ bố đẹp trai hơn chứ. |
| 49 | Vậy gen sau này của tôi khá tốt đấy chứ. | Vậy gen của bố sau này cũng tốt đấy chứ. |
| 50 | Chuyện đó chẳng liên quan mấy đến bạn. | Chẳng nhờ bố mấy đâu. |
| 51 | Vậy bạn về tìm tôi làm gì? | Vậy con về tìm bố làm gì? |
| 59 | Cô ấy sau này sẽ là vợ của bạn. | Sau này cô ấy là vợ bố. |
| 63 | Vì chính mẹ tôi bảo tôi phải về. | Chính mẹ con bảo con về. |
| 67 | Nghe cô ấy nói là con gái tôi. | Cô ấy bảo là con gái của con. |
| 68 | Chắc chắn đó không phải con của bạn đâu. | Chắc chẳng phải con của con đâu. |
| 70 | Tự soi gương xem đi. | Con tự soi gương xem đi. |
| 74 | Đúng là con gái của ông ấy thật. | Cháu thật là con gái nó à? |
| 76 | Ừ. | Dạ. |
| 77 | Mẹ có xinh không? | Mẹ con có xinh không? |
| 78 | Thấy sao? | Bố thấy sao? |
| 83 | Sau này anh ấy cũng không ăn nữa. | Sau này bố cháu cũng không ăn. |
| 92 | Con thật sự là con gái ta sao? | Con thật là con gái bố sao? |
| 94 | Vậy sau này có đẹp trai không? | Sau này bố đẹp trai hơn à? |
| 98 | Vậy ra mẹ bạn thật sự rất xinh đẹp. | Vậy mẹ con xinh thật nhỉ. |
| 101 | Tối nay tôi có đi hay không. | Tối nay bố có đi không. |
| 104 | Còn tôi thì sao? | Con thì sao? |
| 109 | Còn lại 10% giống tôi. | Mười phần trăm còn lại giống bố. |
| 114 | Tôi thật sự không phải quay về. | Con thật sự không về... |
| 115 | Để đùa với bạn đâu. | Để đùa với bố đâu. |
| 118 | Mẹ tôi chỉ nói với tôi đúng một câu. | Mẹ con chỉ dặn con một câu. |
| 122 | Còn bạn thì sao? | Còn con thì sao? |
| 123 | Tôi... | Con... |
| 124 | Nếu chúng ta không quen nhau... | Nếu bố mẹ không quen nhau... |
| 125 | Thì còn có bạn không? | Thì còn có con không? |
| 136 | Anh ấy không đi. | Bố tôi không đi. |
| 138 | Tối nay anh ấy không được đi đâu cả. | Tối nay bố tôi không đi đâu hết. |
| 140 | Vì tối nay anh ấy sẽ mắc sai lầm lớn nhất đời. | Tối nay bố tôi sẽ mắc sai lầm lớn nhất đời. |
| 143 | Mẹ tên gì? | Mẹ con tên gì? |
| 149 | Tôi đến cũng chẳng tìm được. | Bố đến cũng chẳng tìm được. |
| 160 | Sau này anh ta làm ăn thế nào? | Sau này nó làm ăn thế nào? |
| 162 | Anh ta có tiền không? | Có tiền không? |
| 164 | Anh ta có hiếu không? | Có hiếu không? |
| 172 | Mẹ tôi đâu rồi? | Mẹ con đâu rồi? |
| 173 | Tôi sao biết được? | Bố biết sao được? |
| 174 | Đâu có nói trông thế nào đâu. | Con có nói mẹ trông thế nào đâu. |
| 181 | Tôi là bố cậu ấy. | Tôi là bố con bé. |
