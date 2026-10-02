# 🎬 Douyin2TikTok AI Studio PRO (Dịch & Lồng Tiếng Video Triệu View)

Hệ thống tự động hóa toàn diện quy trình **lấy video Douyin/TikTok Trung Quốc về -> dịch thoát ý sang tiếng Việt -> lồng tiếng AI chuẩn ngữ điệu -> giữ trọn BGM/tiếng động gốc (Audio Ducking) -> che chữ cứng tiếng Trung -> burn phụ đề động chuẩn 9:16 TikTok**.

---

## ⚡ Các Tính Năng Đẳng Cấp Đã Được Tích Hợp Sẵn

1. **Bộ bóc tách âm thanh (Vocal & BGM Separation):**
   - Không bị "chết âm thanh": Tách riêng giọng nói tiếng Trung và nhạc nền/tiếng động môi trường (tiếng cười, tiếng đập bàn, tiếng xe cộ,...).
   - Tích hợp chuẩn **Sidechain Audio Ducking**: Nhạc nền tự động giảm nhỏ khi có giọng thuyết minh tiếng Việt và tự động lớn lên khi ngừng nói.
2. **Nhận dạng tiếng Trung (ASR):**
   - Sử dụng **Faster-Whisper** tối ưu hóa bộ nhớ, tương thích cực tốt với card đồ họa (GTX 1050 Ti 4GB hoặc CPU) mà không lo bị lỗi tràn bộ nhớ (CUDA Out of Memory).
   - Bóc mốc thời gian chính xác tới từng mili-giây.
3. **Bộ não dịch thuật TikTok (Time-Budgeting LLM):**
   - Hỗ trợ **Gemini 2.0 Flash** và **DeepSeek-V3**.
   - Prompt tối ưu cho văn nói đời thường của Gen Z / TikTok Việt Nam, xưng hô mượt mà, dịch thoát ý.
   - **Cơ chế khống chế độ dài (Time-Budget):** Ép số từ tiếng Việt phải khớp với thời lượng câu tiếng Trung gốc, triệt tiêu 100% tình trạng nói vội như vịt Donald.
4. **Lồng tiếng Việt (Vietnamese TTS):**
   - Tích hợp **Edge-TTS Neural** (Hoài My - Nữ truyền cảm, Nam Minh - Nam MC kịch tính) miễn phí, tốc độ ánh sáng, không cần GPU.
   - Hỗ trợ kết nối API sang **VieNeu-TTS** / **ZeroTTS** nếu muốn dùng model train riêng.
   - Tự động **Time-Stretching (atempo)** để căn chỉnh từng câu khớp khít với mốc thời gian hình ảnh.
5. **Che chữ cứng tiếng Trung & Phụ đề 9:16:**
   - Tạo dải mờ kính (Frosted Glass Blur) đè lên khu vực phụ đề cứng tiếng Trung cũ, không tốn tài nguyên inpainting mà vẫn cực kỳ thẩm mỹ.
   - Tự động xuất phụ đề `.ass` và `.srt` màu vàng viền đen tương phản cao, căn chỉnh an toàn phía trên giao diện TikTok.

---

## 🚀 Cách Khởi Động Nhanh (1-Click)

### Cách 1: Chạy giao diện Web Studio
Chỉ cần nhấp đúp chuột vào file:
👉 **`start.bat`**

Trình duyệt sẽ tự động mở tại địa chỉ: `http://127.0.0.1:8000`

### Cách 2: Chạy dòng lệnh (CLI / Batch Process)
```powershell
# Kích hoạt môi trường
.\venv\Scripts\activate

# Dịch 1 video đơn lẻ
python cli.py -i "https://v.douyin.com/xyz/"

# Dịch hàng loạt toàn bộ video trong 1 thư mục
python cli.py -f "C:/Users/phamc/Videos/Douyin" --voice "vi-VN-NamMinhNeural"
```

---

## 🔑 Cấu hình API Keys (Dịch thuật)

Mở file `.env` hoặc bấm nút **"Cấu hình API"** ngay trên giao diện Web Studio:
```env
# Lấy miễn phí tại: https://aistudio.google.com/
GEMINI_API_KEY=your_gemini_api_key_here

# Hoặc lấy tại: https://platform.deepseek.com/
DEEPSEEK_API_KEY=your_deepseek_api_key_here
```

*(Lưu ý: Nếu chưa nhập key, hệ thống vẫn hoạt động ở chế độ demo kiểm thử).*

---

## 📁 Cấu Trúc Dự Án

```
e:/Dịch video/
├── core/
│   ├── downloader.py      # Tải video không watermark từ Douyin/TikTok
│   ├── separator.py       # Tách Vocals và BGM/SFX
│   ├── asr.py             # Bóc giọng tiếng Trung sang text & timestamp
│   ├── translator.py      # Dịch thoát ý & khống chế thời lượng (Gemini/DeepSeek)
│   ├── tts.py             # Lồng tiếng Việt & time-stretch từng câu
│   ├── audio_ducking.py   # Sidechain compressor hòa trộn nhạc nền
│   ├── subtitle.py        # Tạo file phụ đề ASS / SRT chuẩn 9:16
│   ├── video_composer.py  # Render video cuối cùng, che chữ cứng & burn sub
│   └── pipeline.py        # Điều phối toàn bộ luồng tự động
├── templates/
│   └── index.html         # Giao diện Web Studio hiện đại
├── static/
│   ├── app.js             # Xử lý tương tác, WebSocket tiến trình, sửa phụ đề
│   └── style.css          # Giao diện Dark theme
├── workspace/             # Chứa video tải về, file tạm và video thành phẩm
│   ├── inputs/
│   ├── outputs/
│   └── temp/
├── config.py              # Cấu hình trung tâm
├── main.py                # Server FastAPI & WebSocket
├── cli.py                 # Chạy tự động hàng loạt
├── start.bat              # File kích hoạt 1-click trên Windows
└── requirements.txt       # Danh sách thư viện Python
```
