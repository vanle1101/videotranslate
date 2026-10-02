import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# Ensure root directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Explicitly load .env
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from config import settings

def run_gemini_diagnostics(test_prompt: str = "你好，欢迎来到这里。") -> dict:
    print("=" * 80)
    print("DIAGNOSTICS: REAL GEMINI API TRANSLATION VERIFICATION")
    print("=" * 80)

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip() or settings.GEMINI_API_KEY.strip()

    if not gemini_key:
        result = {
            "Provider": "Google Gemini",
            "Model": settings.GEMINI_MODEL,
            "Latency": "N/A",
            "Status": "Failure",
            "Reason": "GEMINI_API_KEY is not defined in .env"
        }
        print(f"Provider: {result['Provider']}")
        print(f"Model:    {result['Model']}")
        print(f"Latency:  {result['Latency']}")
        print(f"Status:   {result['Status']} ({result['Reason']})")
        print("\n[!] To activate Gemini, set your API key in .env or Settings tab in the Desktop App.")
        print("=" * 80)
        return result

    try:
        from google import genai
        t0 = time.time()
        client = genai.Client(api_key=gemini_key)
        
        sys_instruction = (
            "Bạn là chuyên gia chuyển ngữ video Douyin sang tiếng Việt TikTok.\n"
            "Dịch câu tiếng Trung sau sang tiếng Việt tự nhiên, ngắn gọn.\n"
            "Trả về JSON: {\"final_vi\": \"...\"}"
        )
        
        resp = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=[f"Dịch câu: {test_prompt}"],
            config=dict(system_instruction=sys_instruction, response_mime_type="application/json")
        )
        latency = round((time.time() - t0) * 1000, 1)

        import json
        data = json.loads(resp.text)
        translated_text = data.get("final_vi", resp.text.strip())

        result = {
            "Provider": "Google Gemini",
            "Model": settings.GEMINI_MODEL,
            "Latency": f"{latency} ms",
            "Status": "Success",
            "Input_ZH": test_prompt,
            "Output_VI": translated_text
        }

        print(f"Provider:  {result['Provider']}")
        print(f"Model:     {result['Model']}")
        print(f"Latency:   {result['Latency']}")
        print(f"Status:    {result['Status']}")
        print(f"Input ZH:  {result['Input_ZH']}")
        print(f"Output VI: {result['Output_VI']}")
        print("=" * 80)
        return result

    except Exception as e:
        result = {
            "Provider": "Google Gemini",
            "Model": settings.GEMINI_MODEL,
            "Latency": "N/A",
            "Status": "Failure",
            "Reason": str(e)
        }
        print(f"Provider: {result['Provider']}")
        print(f"Model:    {result['Model']}")
        print(f"Latency:  {result['Latency']}")
        print(f"Status:   {result['Status']} ({result['Reason']})")
        print("=" * 80)
        return result

if __name__ == "__main__":
    run_gemini_diagnostics()
