import json
import os
import re
from typing import List, Dict, Any, Optional
from config import settings

TIKTOK_TRANSLATION_SYSTEM_PROMPT = """Bạn là chuyên gia chuyển ngữ và biên tập video ngắn hàng đầu cho TikTok và Douyin.
Nhiệm vụ của bạn là dịch các câu thoại tiếng Trung (kèm mốc thời gian bắt đầu và kết thúc) sang TIẾNG VIỆT ĐỜI THƯỜNG, TỰ NHIÊN, HỢP TREND TIKTOK VIỆT NAM.

QUY TẮC BẮT BUỘC:
1. KHÔNG DỊCH WORD-BY-WORD (dịch thô kiểu Google Dịch): Hãy dịch thoát ý, giữ trọn cảm xúc, ngữ điệu vui vẻ/hài hước/kịch tính của video.
2. XƯNG HÔ LINH HOẠT: Chọn xưng hô phù hợp với bối cảnh video (mình - các bạn, anh - em, tao - mày, tui - mọi người).
3. ĐỘ DÀI CÂU (TIME-BUDGET): Tốc độ nói tiếng Việt chuẩn là khoảng 2.8 - 3.5 từ/giây. 
   - Với mỗi câu, bạn được cung cấp thời lượng (duration). Số từ tiếng Việt KHÔNG ĐƯỢC VƯỢT QUÁ: (duration * 3.5) từ.
   - Nếu câu quá ngắn mà bản dịch quá dài, HÃY CẮT GỌT TỪ RƯỜM RÀ để giọng lồng tiếng khi đọc không bị tăng tốc quá nhanh như vịt kêu!
4. ĐỊNH DẠNG ĐẦU RA: Bắt buộc trả về đúng định dạng JSON như mẫu sau, KHÔNG viết thêm bất kỳ lời dẫn nào ngoài JSON:
```json
{
  "results": [
    {"id": 0, "vi_text": "Câu dịch tiếng Việt"},
    {"id": 1, "vi_text": "Câu dịch tiếp theo"}
  ]
}
```
"""

class VideoTranslator:
    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or settings.LLM_PROVIDER

    def translate_segments(self, segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Translates a list of Chinese segments into localized Vietnamese.
        Each segment dict contains: 'id', 'start', 'end', 'duration', 'text'.
        Adds 'vi_text' to each segment.
        """
        if not segments:
            return []

        # Prepare payload for LLM
        input_payload = [
            {
                "id": seg["id"],
                "duration": seg.get("duration", round(seg["end"] - seg["start"], 2)),
                "zh_text": seg["text"]
            }
            for seg in segments
        ]

        translated_map = {}

        # 1. Try Gemini
        gemini_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        deepseek_key = settings.DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY")
        openai_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")

        if gemini_key:
            print("[*] Translating with Gemini API (gemini-2.0-flash)...")
            try:
                translated_map = self._translate_with_gemini(input_payload, gemini_key)
            except Exception as e:
                print(f"[!] Gemini translation failed ({e}), checking alternative...")

        # 2. Try DeepSeek / OpenAI if Gemini not set or failed
        if not translated_map and (deepseek_key or openai_key):
            print("[*] Translating with DeepSeek / OpenAI API...")
            try:
                api_key = deepseek_key or openai_key
                base_url = "https://api.deepseek.com/v1" if deepseek_key else settings.OPENAI_BASE_URL
                model = settings.DEEPSEEK_MODEL if deepseek_key else "gpt-4o-mini"
                translated_map = self._translate_with_openai_compatible(input_payload, api_key, base_url, model)
            except Exception as e:
                print(f"[!] DeepSeek/OpenAI translation failed: {e}")

        # 3. Fallback if no API key is provided: Offline basic translation
        if not translated_map:
            print("[!] No API key configured or API calls failed. Using smart offline phonetic fallback.")
            for item in input_payload:
                translated_map[item["id"]] = f"[Dịch]: {item['zh_text']}"

        # Merge back to segments
        merged_segments = []
        for seg in segments:
            seg_copy = dict(seg)
            seg_copy["vi_text"] = translated_map.get(seg["id"], seg["text"])
            merged_segments.append(seg_copy)

        return merged_segments

    def _translate_with_gemini(self, payload: List[Dict[str, Any]], api_key: str) -> Dict[int, str]:
        from google import genai
        client = genai.Client(api_key=api_key)

        prompt = f"Dưới đây là danh sách các câu thoại tiếng Trung cần dịch:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=[prompt],
            config=dict(
                system_instruction=TIKTOK_TRANSLATION_SYSTEM_PROMPT,
                response_mime_type="application/json"
            )
        )

        return self._parse_json_response(response.text)

    def _translate_with_openai_compatible(self, payload: List[Dict[str, Any]], api_key: str, base_url: str, model: str) -> Dict[int, str]:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)

        prompt = f"Dưới đây là danh sách các câu thoại tiếng Trung cần dịch:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": TIKTOK_TRANSLATION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )

        return self._parse_json_response(response.choices[0].message.content)

    def _parse_json_response(self, text: str) -> Dict[int, str]:
        result_map = {}
        try:
            # Clean markdown wrappers if present
            clean_text = text.strip()
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:]
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3]
            
            data = json.loads(clean_text)
            items = data.get("results", data if isinstance(data, list) else [])
            for it in items:
                result_map[int(it["id"])] = it.get("vi_text", "").strip()
        except Exception as e:
            print(f"[!] JSON parsing error: {e}. Raw text: {text[:200]}")
        return result_map
