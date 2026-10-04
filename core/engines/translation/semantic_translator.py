import json
import os
import re
from typing import List, Dict, Any, Optional
from config import settings
from core.engines.translation.base import TranslationEngine

VIDEOLINGO_SUMMARY_PROMPT = """Bạn là chuyên gia phân tích ngữ cảnh và thuật ngữ video ngắn Douyin/TikTok Trung Quốc - Việt Nam.
Dựa trên toàn bộ bản transcript tiếng Trung sau, hãy thực hiện 2 việc:
1. "theme": Tóm tắt chủ đề chính và ngữ cảnh/cảm xúc của video trong 1-2 câu.
2. "terms": Trích xuất danh sách thuật ngữ, tên riêng, tiếng lóng mạng Trung Quốc (trà xanh, tổng tài, pua, cuốn, v.v.) và cách chuyển ngữ tương đương trong văn hóa TikTok Việt Nam.
3. "pronouns": Xác định cách xưng hô tối ưu xuyên suốt video (ví dụ: mình - các bạn, anh - em, tui - bà con).

Đầu ra định dạng JSON:
```json
{
  "theme": "...",
  "terms": [{"src": "...", "tgt": "...", "note": "..."}],
  "pronouns": "mình - các bạn"
}
```
"""

VIDEOLINGO_TRANSLATE_PROMPT = """Bạn là biên dịch viên cao cấp của VideoLingo chuyên Việt hóa video ngắn TikTok.
Dựa trên ngữ cảnh và thuật ngữ sau:
Ngữ cảnh: {theme}
Cách xưng hô: {pronouns}
Bảng thuật ngữ: {terms}

Hãy dịch từng câu tiếng Trung sang tiếng Việt qua 3 cấp độ:
1. "literal_vi": Dịch sát nghĩa gốc, đủ ý.
2. "natural_vi": Dịch thoát ý, dùng văn nói đời thường của người Việt, dí dỏm/kịch tính theo đúng tinh thần TikTok, xưng hô nhất quán.
3. "final_vi": Khống chế độ dài (Time-Budgeting) để vừa khít với thời lượng duration cho trước (khoảng 3 từ/giây). Cắt bớt từ thừa nếu câu bị dài quá slot.

Đầu ra JSON duy nhất:
```json
{{
  "results": [
    {{
      "id": 0,
      "literal_vi": "...",
      "natural_vi": "...",
      "final_vi": "..."
    }}
  ]
}}
```
"""

class SemanticTranslator(TranslationEngine):
    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or settings.LLM_PROVIDER

    def _api_keys(self):
        # Only the selected provider may receive transcript data or incur usage.
        return (
            (settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")) if self.provider == "gemini" else None,
            (settings.DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY")) if self.provider == "deepseek" else None,
            (settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")) if self.provider == "openai" else None,
        )

    @property
    def name(self) -> str:
        return "VideoLingo-Semantic-Translator"

    @property
    def is_available(self) -> bool:
        return True

    def get_info(self) -> Dict[str, Any]:
        gemini_key, deepseek_key, openai_key = self._api_keys()
        return {
            "name": self.name,
            "provider": self.provider,
            "is_available": self.is_available,
            "has_llm_key": bool(gemini_key or deepseek_key or openai_key),
            "pipeline": ["Glossary/Summary", "Literal", "Natural Adaptation", "Duration-Constrained Rewrite"]
        }

    def translate(
        self,
        segments: List[Dict[str, Any]],
        target_lang: str = "vi",
        progress_callback: Optional[callable] = None
    ) -> List[Dict[str, Any]]:
        """
        Translates Chinese transcript segments into Vietnamese using multi-stage reflection:
        1. Context & Glossary extraction
        2. 3-tier translation: literal -> natural -> final (duration constrained)
        """
        if not segments:
            return []

        # Join full transcript text for context analysis
        full_text = " ".join([seg.get("text_zh", seg.get("text", "")) for seg in segments])

        # Step 1: Summary & Glossary
        if progress_callback:
            progress_callback(10, "VideoLingo Stage 1: Phân tích ngữ cảnh & trích xuất bảng thuật ngữ...")

        context_info = self._extract_context_and_glossary(full_text)

        # Step 2: Multi-tier translation
        if progress_callback:
            progress_callback(50, "VideoLingo Stage 2: Dịch phản chiếu 3 cấp độ (Literal -> Natural -> Final Time-Budget)...")

        payload = [
            {
                "id": seg["id"],
                "duration": seg.get("duration", round(seg["end"] - seg["start"], 2)),
                "text_zh": seg.get("text_zh", seg.get("text", ""))
            }
            for seg in segments
        ]

        results_map = self._execute_3tier_translation(payload, context_info)

        # Step 3: Merge back to internal segments
        merged = []
        for seg in segments:
            seg_copy = dict(seg)
            res = results_map.get(seg["id"], {})
            text_zh = seg.get("text_zh", seg.get("text", ""))
            seg_copy["text_zh"] = text_zh
            seg_copy["literal_vi"] = res.get("literal_vi", text_zh)
            seg_copy["natural_vi"] = res.get("natural_vi", text_zh)
            seg_copy["final_vi"] = res.get("final_vi", text_zh)
            # For backward compatibility
            seg_copy["vi_text"] = seg_copy["final_vi"]
            merged.append(seg_copy)

        if progress_callback:
            progress_callback(100, "Hoàn tất dịch thuật VideoLingo!")

        return merged

    def _extract_context_and_glossary(self, full_text: str) -> Dict[str, Any]:
        gemini_key, deepseek_key, openai_key = self._api_keys()

        if gemini_key:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            try:
                resp = client.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=[f"Transcript video:\n{full_text}"],
                    config=dict(
                        system_instruction=VIDEOLINGO_SUMMARY_PROMPT,
                        response_mime_type="application/json"
                    )
                )
                return json.loads(resp.text)
            except Exception as e:
                print(f"[!] Context extraction error: {e}")

        return {"theme": "Video ngắn Douyin đời thường", "terms": [], "pronouns": "mình - các bạn"}

    def _execute_3tier_translation(self, payload: List[Dict[str, Any]], context_info: Dict[str, Any]) -> Dict[int, Dict[str, str]]:
        gemini_key, deepseek_key, openai_key = self._api_keys()

        system_prompt = VIDEOLINGO_TRANSLATE_PROMPT.format(
            theme=context_info.get("theme", "Đời thường"),
            pronouns=context_info.get("pronouns", "mình - các bạn"),
            terms=json.dumps(context_info.get("terms", []), ensure_ascii=False)
        )

        user_content = f"Danh sách các câu thoại cần chuyển ngữ:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"

        raw_response = ""
        if gemini_key:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            try:
                resp = client.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=[user_content],
                    config=dict(
                        system_instruction=system_prompt,
                        response_mime_type="application/json"
                    )
                )
                raw_response = resp.text
            except Exception as e:
                print(f"[!] Gemini 3-tier translation failed: {e}")

        if not raw_response and (deepseek_key or openai_key):
            from openai import OpenAI
            client = OpenAI(api_key=deepseek_key or openai_key, base_url="https://api.deepseek.com/v1" if deepseek_key else settings.OPENAI_BASE_URL)
            try:
                resp = client.chat.completions.create(
                    model=settings.DEEPSEEK_MODEL if deepseek_key else "gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    response_format={"type": "json_object"}
                )
                raw_response = resp.choices[0].message.content
            except Exception as e:
                print(f"[!] DeepSeek translation failed: {e}")

        # Parse output
        res_map = {}
        if raw_response:
            try:
                clean = raw_response.strip()
                if clean.startswith("```json"): clean = clean[7:]
                if clean.endswith("```"): clean = clean[:-3]
                data = json.loads(clean)
                items = data if isinstance(data, list) else data.get("results", [])
                for it in items:
                    res_map[int(it["id"])] = {
                        "literal_vi": it.get("literal_vi", ""),
                        "natural_vi": it.get("natural_vi", ""),
                        "final_vi": it.get("final_vi", "")
                    }
            except Exception as e:
                print(f"[!] JSON parsing error in 3-tier translation: {e}")

        # Fallback for missing ids or empty results
        for item in payload:
            i = item["id"]
            if i not in res_map or not res_map[i].get("final_vi"):
                trans = self._fallback_translate(item["text_zh"])
                res_map[i] = {
                    "literal_vi": trans,
                    "natural_vi": trans,
                    "final_vi": trans
                }

        return res_map

    def _fallback_translate(self, text: str) -> str:
        if not text or not text.strip():
            return ""
        # 1. Try Google Translate API
        try:
            import urllib.parse
            import urllib.request
            url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=zh-CN&tl=vi&dt=t&q=" + urllib.parse.quote(text)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                res = "".join([part[0] for part in data[0] if part and part[0]])
                if res.strip():
                    return res
        except Exception:
            pass

        # 2. Try MyMemory API
        try:
            import urllib.parse
            import urllib.request
            url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(text)}&langpair=zh|vi"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                trans = data.get("responseData", {}).get("translatedText", "")
                if trans and "MYMEMORY WARNING" not in trans:
                    return trans
        except Exception as e:
            print(f"[!] MyMemory fallback error: {e}")

        raise RuntimeError("Không thể kết nối dịch thuật miễn phí. Kiểm tra Internet hoặc cấu hình API dịch trong Cài đặt.")

    def translate_single_segment(
        self,
        text_zh: str,
        duration: float,
        rolling_context: Optional[List[Dict[str, str]]] = None,
        pronouns: str = "mình - các bạn"
    ) -> Dict[str, str]:
        """
        Translates a single segment with rolling context and strict duration budgeting.
        """
        clean_zh = text_zh.strip()
        if not clean_zh:
            return {"literal_vi": "", "natural_vi": "", "final_vi": ""}

        target_words = max(3, int(duration * 3.0))
        gemini_key, deepseek_key, openai_key = self._api_keys()

        if gemini_key or deepseek_key or openai_key:
            ctx_text = ""
            if rolling_context:
                ctx_lines = [f"- Trung: {c.get('zh', '')} -> Việt: {c.get('vi', '')}" for c in rolling_context[-5:]]
                ctx_text = "\n".join(ctx_lines)

            sys_instruction = f"""Bạn là chuyên gia chuyển ngữ video Douyin sang tiếng Việt TikTok trong thời gian thực.
Ngữ cảnh 5 câu thoại trước đó:
{ctx_text or '(Đầu video)'}

Quy tắc bắt buộc:
1. Dịch câu tiếng Trung: "{clean_zh}"
2. Xưng hô: {pronouns}
3. Thời lượng đọc cho phép: {duration:.1f} giây (tối đa {target_words} từ tiếng Việt).
4. Phải dịch thoát ý, văn nói tự nhiên của người Việt, ngắn gọn để vừa khít thời lượng nói trên mà không bị ép tốc độ.
5. Trả về JSON:
{{"literal_vi": "...", "natural_vi": "...", "final_vi": "..."}}
"""
            if gemini_key:
                try:
                    from google import genai
                    client = genai.Client(api_key=gemini_key)
                    resp = client.models.generate_content(
                        model=settings.GEMINI_MODEL,
                        contents=[f"Dịch câu: {clean_zh}"],
                        config=dict(system_instruction=sys_instruction, response_mime_type="application/json")
                    )
                    data = json.loads(resp.text)
                    return {
                        "literal_vi": data.get("literal_vi", clean_zh),
                        "natural_vi": data.get("natural_vi", clean_zh),
                        "final_vi": data.get("final_vi", data.get("natural_vi", clean_zh))
                    }
                except Exception as e:
                    print(f"[!] Gemini single translate error: {e}")

            if deepseek_key or openai_key:
                try:
                    from openai import OpenAI
                    client = OpenAI(api_key=deepseek_key or openai_key, base_url="https://api.deepseek.com/v1" if deepseek_key else settings.OPENAI_BASE_URL)
                    resp = client.chat.completions.create(
                        model=settings.DEEPSEEK_MODEL if deepseek_key else "gpt-4o-mini",
                        messages=[{"role": "system", "content": sys_instruction}, {"role": "user", "content": f"Dịch: {clean_zh}"}],
                        response_format={"type": "json_object"}
                    )
                    data = json.loads(resp.choices[0].message.content)
                    return {
                        "literal_vi": data.get("literal_vi", clean_zh),
                        "natural_vi": data.get("natural_vi", clean_zh),
                        "final_vi": data.get("final_vi", data.get("natural_vi", clean_zh))
                    }
                except Exception as e:
                    print(f"[!] DeepSeek single translate error: {e}")

        # Fallback translation
        raw_vi = self._fallback_translate(clean_zh)
        # Never discard the end of a sentence to fit a time budget.
        final_vi = raw_vi

        return {
            "literal_vi": raw_vi,
            "natural_vi": raw_vi,
            "final_vi": final_vi
        }
