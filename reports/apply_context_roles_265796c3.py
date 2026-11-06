"""Apply reviewed wording through the running application's transactional API."""
import json
import sys
from pathlib import Path
import requests

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:53887"
TASK = "/api/streaming/265796c3"
OUT = Path(__file__).parent

# Displayed sentence numbers, not zero-based segment IDs. Keep the deliberate
# role reversal at 153–159 and narration in first person.
EDITS = {
    33: "Con 19 tuổi.",
    37: "Con cứ tưởng là...",
    38: "Hồi trẻ bố đẹp trai hơn chứ.",
    49: "Vậy gen của bố sau này cũng tốt đấy chứ.",
    50: "Chẳng nhờ bố mấy đâu.",
    51: "Vậy con về tìm bố làm gì?",
    59: "Sau này cô ấy là vợ bố.",
    63: "Chính mẹ con bảo con về.",
    67: "Cô ấy bảo là con gái của con.",
    68: "Chắc chẳng phải con của con đâu.",
    70: "Con tự soi gương xem đi.",
    74: "Cháu thật là con gái nó à?",
    76: "Dạ.",
    77: "Mẹ con có xinh không?",
    78: "Bố thấy sao?",
    83: "Sau này bố cháu cũng không ăn.",
    92: "Con thật là con gái bố sao?",
    94: "Sau này bố đẹp trai hơn à?",
    98: "Vậy mẹ con xinh thật nhỉ.",
    101: "Tối nay bố có đi không.",
    104: "Con thì sao?",
    109: "Mười phần trăm còn lại giống bố.",
    114: "Con thật sự không về...",
    115: "Để đùa với bố đâu.",
    118: "Mẹ con chỉ dặn con một câu.",
    122: "Còn con thì sao?",
    123: "Con...",
    124: "Nếu bố mẹ không quen nhau...",
    125: "Thì còn có con không?",
    136: "Bố tôi không đi.",
    138: "Tối nay bố tôi không đi đâu hết.",
    140: "Tối nay bố tôi sẽ mắc sai lầm lớn nhất đời.",
    143: "Mẹ con tên gì?",
    149: "Bố đến cũng chẳng tìm được.",
    160: "Sau này nó làm ăn thế nào?",
    162: "Có tiền không?",
    164: "Có hiếu không?",
    172: "Mẹ con đâu rồi?",
    173: "Bố biết sao được?",
    174: "Con có nói mẹ trông thế nào đâu.",
    181: "Tôi là bố con bé.",
}

def snapshot():
    response = requests.get(BASE + TASK, timeout=30)
    response.raise_for_status()
    return response.json()

def run():
    initial = snapshot()
    backup = OUT / "context-roles-265796c3-before.json"
    if not backup.exists():
        backup.write_text(json.dumps(initial, ensure_ascii=False, indent=2), encoding="utf-8")
    results_path = OUT / "context-roles-265796c3-results.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else []
    for sid, label, own, listener in ((66, "Trần Dã nói với mẹ", "con", "mẹ"),
                                      (67, "Mẹ nói với Trần Dã", "mẹ", "con")):
        row = next(r for r in snapshot()["segments"] if r["id"] == sid)
        response = requests.patch(BASE + TASK + "/speaker-confirmation", json={
            "anchor_segment_id": sid, "expected_revision": row["revision"],
            "apply_same_voice": False, "label": label,
            "self_address": own, "listener_address": listener}, timeout=60)
        response.raise_for_status()
        print("CONFIRMED", sid + 1, flush=True)
    for number, wording in EDITS.items():
        row = next(r for r in snapshot()["segments"] if r["id"] == number - 1)
        if row["final_vi"] == wording:
            print("ALREADY", number, wording, flush=True)
            continue
        response = requests.patch(BASE + TASK + f"/segments/{number - 1}",
                                  json={"final_vi": wording}, timeout=600)
        result = {"number": number, "before": row["final_vi"], "after": wording,
                  "http_status": response.status_code, "response": response.json()}
        results.append(result)
        results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(response.status_code, number, wording,
              "" if response.ok else str(response.text)[:500], flush=True)
    final = snapshot()
    (OUT / "context-roles-265796c3-after.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    run()
