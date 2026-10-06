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

Giữ đúng ý nghĩa và hành động của câu gốc ở cả ba cấp độ. Không thêm thông tin, danh tính, lời giới thiệu hay lời kêu gọi không có trong bản gốc. Ưu tiên đúng nghĩa hơn tiếng lóng hoặc sự dí dỏm.

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
    STRICT_PROVIDERS = frozenset({"opencode", "openrouter-free", "gemini", "muse"})

    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or settings.LLM_PROVIDER

    def _api_keys(self):
        # Only the selected provider may receive transcript data or incur usage.
        return (
            (settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")) if self.provider == "gemini" else None,
            (settings.DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY")) if self.provider == "deepseek" else None,
            (settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")) if self.provider == "openai" else None,
        )

    def _opencode_key(self) -> Optional[str]:
        """Return the OpenCode key without ever borrowing another provider key."""
        return getattr(settings, "OPENCODE_API_KEY", "") or os.getenv("OPENCODE_API_KEY")

    def _opencode_request(self, system_prompt: str, user_prompt: str) -> str:
        """Use only the selected provider, without cross-provider fallback.

        Keep the historical method name for integrations that mock this boundary.
        """
        from core.engines.translation.gemini_client import GeminiClient, GeminiError
        from core.engines.translation.opencode_client import OpenCodeClientError, OpenCodeZenClient
        from core.engines.translation.openrouter_client import OpenRouterClientError, OpenRouterFreeClient
        from core.services.muse_service import MuseError, muse_service
        try:
            if self.provider == "gemini":
                client = GeminiClient()
            elif self.provider == "muse":
                client = muse_service
            elif self.provider == "openrouter-free":
                client = OpenRouterFreeClient(model=settings.OPENROUTER_MODEL, timeout=settings.OPENCODE_TIMEOUT)
            elif self.provider == "opencode":
                client = OpenCodeZenClient(
                    api_key=self._opencode_key(),
                    model=getattr(settings, "OPENCODE_MODEL", "big-pickle"),
                    timeout=getattr(settings, "OPENCODE_TIMEOUT", 60),
                )
            else:
                raise RuntimeError("Nhà cung cấp dịch không hỗ trợ chế độ JSON.")
            return client.translate(user_prompt, system=system_prompt)
        except (GeminiError, MuseError, OpenCodeClientError, OpenRouterClientError) as exc:
            raise RuntimeError(f"{exc} Hãy thử lại hoặc đổi cấu hình dịch.") from None
        except Exception:
            raise RuntimeError("API không dịch được. Hãy thử lại hoặc đổi cấu hình dịch.") from None

    def _translation_error(self, message: str) -> RuntimeError:
        label = {"gemini": "Gemini", "muse": "Muse", "opencode": "OpenCode", "openrouter-free": "OpenRouter"}.get(self.provider, "AI")
        return RuntimeError(f"{label} {message} Hãy thử lại hoặc đổi cấu hình dịch.")

    @staticmethod
    def _json_response(raw_response: str) -> Any:
        """Parse JSON with optional markdown fences and surrounding prose."""
        clean = (raw_response or "").strip()
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", clean, re.I | re.S)
        if match:
            clean = match.group(1).strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            # Some models add one sentence before/after the JSON object.
            start = min([p for p in (clean.find("{"), clean.find("[")) if p >= 0], default=-1)
            end = max(clean.rfind("}"), clean.rfind("]"))
            if start >= 0 and end > start:
                return json.loads(clean[start:end + 1])
            raise

    @staticmethod
    def _nonempty_string(value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip())

    def _parse_opencode_results(self, raw_response: str, payload: List[Dict[str, Any]], single: bool = False) -> Dict[int, Dict[str, str]]:
        try:
            data = self._json_response(raw_response)
        except Exception:
            raise self._translation_error("trả về JSON không hợp lệ.") from None
        if single:
            if isinstance(data, dict) and "id" not in data and any(k in data for k in ("literal_vi", "natural_vi", "final_vi")):
                data = dict(data)
                data["id"] = int(payload[0]["id"])
            data = {"results": [data]}
        items = data if isinstance(data, list) else data.get("results", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            items = []
        expected = {int(item["id"]): item.get("text_zh", "") for item in payload if "id" in item}
        result: Dict[int, Dict[str, str]] = {}
        for item in items:
            if not isinstance(item, dict):
                raise self._translation_error("trả về mục bản dịch không hợp lệ.")
            raw_id = item.get("id")
            if isinstance(raw_id, bool) or not (isinstance(raw_id, int) or isinstance(raw_id, str) and re.fullmatch(r"-?\d+", raw_id)):
                raise self._translation_error("trả về ID bản dịch không hợp lệ.")
            item_id = int(raw_id)
            if item_id not in expected or item_id in result:
                raise self._translation_error("trả về ID bản dịch thừa hoặc trùng.")
            fields = ("literal_vi", "natural_vi", "final_vi")
            values = {field: item.get(field) for field in fields}
            if not all(self._nonempty_string(v) for v in values.values()):
                raise self._translation_error("trả về bản dịch thiếu nội dung.")
            if any(re.search(r"[\u3400-\u9fff]", value) for value in values.values()):
                raise self._translation_error("trả về bản dịch còn chữ Trung chưa chuyển ngữ.")
            result[item_id] = {field: values[field].strip() for field in fields}
            if "needs_review" in item:
                if not isinstance(item["needs_review"], bool):
                    raise self._translation_error("trả về đánh giá độ chắc chắn không hợp lệ.")
                result[item_id]["needs_review"] = item["needs_review"]
                reason = item.get("review_reason", "")
                if not isinstance(reason, str):
                    raise self._translation_error("trả về lý do cần kiểm tra không hợp lệ.")
                result[item_id]["review_reason"] = reason.strip()[:500]
        if set(result) != set(expected):
            missing = sorted(set(expected) - set(result))
            raise self._translation_error(f"thiếu hoặc sai bản dịch cho segment {missing}.")
        return result

    @property
    def name(self) -> str:
        return "VideoLingo-Semantic-Translator"

    @property
    def is_available(self) -> bool:
        return True

    def get_info(self) -> Dict[str, Any]:
        gemini_key, deepseek_key, openai_key = self._api_keys()
        has_llm_key = bool(gemini_key or deepseek_key or openai_key)
        if self.provider == "opencode":
            from core.engines.translation.opencode_client import resolve_api_key
            has_llm_key = bool(resolve_api_key(self._opencode_key()))
        elif self.provider == "openrouter-free":
            from core.engines.translation.openrouter_client import resolve_openrouter_key
            has_llm_key = bool(resolve_openrouter_key())
        elif self.provider == "muse":
            # Muse authenticates in its own browser; probing must not launch it.
            has_llm_key = True
        return {
            "name": self.name,
            "provider": self.provider,
            "is_available": self.is_available,
            "has_llm_key": has_llm_key,
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
            lookup_id = int(seg["id"])
            res = results_map.get(lookup_id, {})
            text_zh = seg.get("text_zh", seg.get("text", ""))
            seg_copy["text_zh"] = text_zh
            if self.provider in self.STRICT_PROVIDERS and not res:
                raise self._translation_error("thiếu bản dịch.")
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
        if self.provider in self.STRICT_PROVIDERS:
            raw = self._opencode_request(
                VIDEOLINGO_SUMMARY_PROMPT,
                f"Transcript video:\n{full_text}",
            )
            try:
                data = self._json_response(raw)
            except Exception:
                raise self._translation_error("trả về JSON ngữ cảnh không hợp lệ.") from None
            if not isinstance(data, dict) or not self._nonempty_string(data.get("theme")) or not self._nonempty_string(data.get("pronouns")) or not isinstance(data.get("terms", []), list):
                raise self._translation_error("trả về ngữ cảnh thiếu trường bắt buộc.")
            return data
        return {"theme": "Video ngắn Douyin đời thường", "terms": [], "pronouns": "mình - các bạn"}

    def _execute_3tier_translation(self, payload: List[Dict[str, Any]], context_info: Dict[str, Any]) -> Dict[int, Dict[str, str]]:
        if self.provider in self.STRICT_PROVIDERS:
            system_prompt = VIDEOLINGO_TRANSLATE_PROMPT.format(
                theme=context_info.get("theme", "Đời thường"),
                pronouns=context_info.get("pronouns", "mình - các bạn"),
                terms=json.dumps(context_info.get("terms", []), ensure_ascii=False)
            )
            raw = self._opencode_request(
                system_prompt,
                f"Danh sách các câu thoại cần chuyển ngữ:\n{json.dumps(payload, ensure_ascii=False, indent=2)}",
            )
            return self._parse_opencode_results(raw, payload)
        _, deepseek_key, openai_key = self._api_keys()

        system_prompt = VIDEOLINGO_TRANSLATE_PROMPT.format(
            theme=context_info.get("theme", "Đời thường"),
            pronouns=context_info.get("pronouns", "mình - các bạn"),
            terms=json.dumps(context_info.get("terms", []), ensure_ascii=False)
        )

        user_content = f"Danh sách các câu thoại cần chuyển ngữ:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"

        raw_response = ""
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
                res_map = self._parse_opencode_results(clean, payload)
            except Exception as e:
                print(f"[!] JSON parsing error in 3-tier translation: {e}")

        # Fallback for missing ids or empty results
        for item in payload:
            i = int(item["id"])
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
                if res.strip() and not re.search(r"[\u3400-\u9fff]", res):
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
                if isinstance(trans, str) and trans.strip() and "MYMEMORY WARNING" not in trans and not re.search(r"[\u3400-\u9fff]", trans):
                    return trans
        except Exception as e:
            print(f"[!] MyMemory fallback error: {e}")

        raise RuntimeError("Không thể kết nối dịch thuật miễn phí. Kiểm tra Internet hoặc cấu hình API dịch trong Cài đặt.")

    def translate_single_segment(
        self,
        text_zh: str,
        duration: float,
        rolling_context: Optional[List[Dict[str, str]]] = None,
        pronouns: str = "Theo nhân vật và ngữ cảnh; không tự đổi quan hệ xưng hô"
    ) -> Dict[str, str]:
        """
        Translates a single segment with rolling context and strict duration budgeting.
        """
        clean_zh = text_zh.strip()
        if not clean_zh:
            return {"literal_vi": "", "natural_vi": "", "final_vi": ""}

        target_words = max(3, int(duration * 3.0))
        if self.provider in self.STRICT_PROVIDERS:
            ctx_text = ""
            if rolling_context:
                ctx_text = "\n".join(f"- Trung: {c.get('zh', '')} -> Việt: {c.get('vi', '')}" for c in rolling_context[-5:])
            sys_instruction = f"""Bạn là chuyên gia chuyển ngữ video Douyin sang tiếng Việt TikTok trong thời gian thực.
Ngữ cảnh 5 câu thoại trước đó:
{ctx_text or '(Đầu video)'}

Quy tắc bắt buộc:
1. Dịch câu tiếng Trung được cung cấp sang tiếng Việt, không lặp lại nguyên văn tiếng Trung.
2. Xưng hô: {pronouns}
3. Thời lượng đọc: {duration:.1f} giây (mục tiêu tối đa {target_words} từ, chỉ tham khảo). Ưu tiên đủ ý, giữ phủ định/đối tượng/hành động hơn giới hạn từ; được vượt mục tiêu để tránh mất ý.
4. Trả về JSON duy nhất: {{"literal_vi": "...", "natural_vi": "...", "final_vi": "...", "needs_review": false, "review_reason": ""}}
5. Giữ đúng ý gốc. Không thêm thông tin hoặc hành động không có trong câu tiếng Trung; ưu tiên đúng nghĩa hơn văn phong.
6. Đầu vào có thể bị nhận dạng âm thanh sai. Nếu câu vô nghĩa, mâu thuẫn ngữ cảnh hoặc không đủ căn cứ để hiểu, KHÔNG bịa thành câu có vẻ hợp lý. Giữ bản dịch nháp sát nguồn và needs_review=true, giải thích ngắn bằng tiếng Việt để người dùng nghe lại. Không tự sửa từ tiếng Trung chỉ vì nghe giống nhau.
"""
            raw = self._opencode_request(sys_instruction, f"Dịch câu: {clean_zh}")
            parsed = self._parse_opencode_results(raw, [{"id": 0, "text_zh": clean_zh}], single=True)
            return parsed[0]
        _, deepseek_key, openai_key = self._api_keys()

        if deepseek_key or openai_key:
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
            if deepseek_key or openai_key:
                try:
                    from openai import OpenAI
                    client = OpenAI(api_key=deepseek_key or openai_key, base_url="https://api.deepseek.com/v1" if deepseek_key else settings.OPENAI_BASE_URL)
                    resp = client.chat.completions.create(
                        model=settings.DEEPSEEK_MODEL if deepseek_key else "gpt-4o-mini",
                        messages=[{"role": "system", "content": sys_instruction}, {"role": "user", "content": f"Dịch: {clean_zh}"}],
                        response_format={"type": "json_object"}
                    )
                    return self._parse_opencode_results(
                        resp.choices[0].message.content,
                        [{"id": 0, "text_zh": clean_zh}], single=True)[0]
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
