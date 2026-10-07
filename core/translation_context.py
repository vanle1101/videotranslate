"""Shared, source-grounded dialogue context for Vietnamese translation.

This module never infers a speaker or translates a pronoun by a keyword rule.
It retains source evidence for the language model, including earlier forms of
address which may have fallen outside the immediately preceding dialogue.
"""
from __future__ import annotations

import json
import re


ADDRESS_POLICY_REVISION = 1

VIETNAMESE_ADDRESS_POLICY = """
QUY TẮC XƯNG HÔ THEO NGỮ CẢNH (áp dụng cả dịch, kiểm định và rút gọn lời đọc):
- Trước khi dịch mỗi lượt, xác định ai đang nói với ai, vai trò/quan hệ, mức độ
  thân mật và thái độ, dựa trên lời nguồn và bằng chứng được cung cấp. Tôi/tao/tớ,
  con/em/chị/anh là những lựa chọn theo quan hệ; 我 không có một bản dịch cố định.
  你 cũng không mặc định là mày. Không tự thêm sắc thái hỗn hoặc hạ vai người nói.
- Khi con nói với bố/mẹ đã được nguồn xác nhận: xưng con, gọi bố/mẹ theo ngữ cảnh;
  không đổi thành tôi/tao/tớ chỉ vì nguồn dùng 我. Khi nguồn xác nhận cách gọi 姐,
  xem ai gọi ai là chị; người gọi có thể xưng em, người được gọi có thể xưng chị.
  Một lời gọi chị và câu giải thích ngay sau có thể vẫn do cùng người nói tiếp.
  Segment/đổi mốc ASR không đồng nghĩa đổi
  người nói. 我 phải theo ĐÚNG người đang nói; giữ chiều xưng hô khi nói tiếp,
  chỉ đổi chiều khi có căn cứ đổi lượt. Không mặc định tao hoặc tự luân phiên
  chị/em theo số câu; không gán một chiều đại từ cho toàn bộ nhân vật.
- 姐/哥 có thể là cách gọi xã giao; 爸/妈/老师 có thể nằm trong lời kể hoặc lời trích.
  Một từ đơn lẻ không chứng minh quan hệ ruột thịt, người nói, tuổi hay giới tính.
  Không gán quan hệ của cảnh trước cho nhân vật mới. Dùng mốc câu, lời gọi/đáp,
  nhân vật và bằng chứng thật; không bịa nhận diện giọng hay hình khi chỉ có chữ.
- Mệnh lệnh, dấu chấm than, 快说 hoặc giọng gấp KHÔNG tự cho phép dùng tao/mày.
  Chỉ dùng xưng hô thô khi ngữ cảnh gốc đủ chứng minh; không tự làm thoại gây gổ.
  Không viết tắt đại từ thành m/t, ko, hoặc tiếng chat trong lời để đọc.
- Nếu chưa xác định được người nói/người nghe hoặc quan hệ: dùng câu trung tính,
  lược đại từ khi tự nhiên và không mất nghĩa; needs_review=true và nêu điều chưa
  rõ ở review_reason. Không tự coi bản dịch trôi chảy là bằng chứng quan hệ.
- Lời gọi trực tiếp trong chính câu nguồn cũng là bằng chứng: 妈妈，我饿了 có
  thể là 'Mẹ ơi, con đói rồi' dù không có speaker_id. Ngược lại, ID người nói
  đơn lẻ không chứng minh tuổi, quan hệ hoặc người đang nghe. Nếu cách xưng hô
  phụ thuộc việc nối một câu ở xa với người nói hiện tại mà chưa có căn cứ nối,
  giữ needs_review=true; không dùng đại từ của bản Việt cũ để tự xác nhận.
- Bản Việt cũ và tóm tắt AI chỉ là bản nháp có thể sai, không phải bằng chứng.
  Ưu tiên nguồn Trung/OCR/âm thanh được cung cấp. Rà mọi lần đổi xưng hô; chỉ
  semantic_verified=true khi nghĩa, vai người nói/nghe và sắc thái đều phù hợp.
  verification_reason cần nêu căn cứ nguồn cho cách xưng hô khi câu có đại từ.
- Khi rút gọn cho vừa tiếng: giữ nguyên quan hệ và mức độ lịch sự đã có căn cứ;
  không đổi em/chị/con sang tao/mày để tiết kiệm âm tiết. Nếu thiếu căn cứ, giữ
  lời có căn cứ và báo chưa chắc; thời lượng không quyết định cách xưng hô.
""".strip()

# Only chooses evidence to retain; it must never decide the Vietnamese pronoun.
_ADDRESS_CUE = re.compile(r"[爸妈媽父母姐哥弟妹爷爺奶叔姨姑舅婶嬸伯嫂叔婆娘]|老师|老師|师傅|師傅|师父|師父|先生|女士|您")


def dialogue_context(rows, focus=(), *, max_rows=64, max_chars=18000):
    """Bound context while retaining nearby turns and distant address evidence.

    Stable source IDs/timestamps preserve direction and chronology. The draft
    remains explicitly untrusted. Older evidence includes its adjacent turns
    so an isolated kinship keyword is less likely to be mistaken for a speaker.
    """
    normalized = []
    for index, row in enumerate(rows):
        source = row.get("text_zh") or row.get("asr_text") or row.get("zh", "")
        if not isinstance(source, str) or not source.strip():
            continue
        item = {key: row[key] for key in ("id", "start", "end", "speaker_id", "addressee_id")
                if key in row and isinstance(row[key], (str, int, float))}
        item["text_zh"] = source[:1500]
        # The selector can be called more than once (for example when a
        # reviewed context is formatted for a second provider request). Keep
        # source-risk markers from the first pass instead of silently making
        # the same row look more certain on the second pass.
        if row.get("source_truncated") or len(source) > 1500:
            item["source_truncated"] = True
        if (row.get("source_needs_review") or row.get("needs_review")
                or ("asr_text" in row and not row.get("text_zh"))):
            item["source_needs_review"] = True
        draft = row.get("final_vi", row.get("vi", ""))
        if isinstance(draft, str) and draft:
            item["final_vi"] = draft[:1500]
            item["translation_is_draft"] = True
        elif row.get("translation_is_draft"):
            item["translation_is_draft"] = True
        normalized.append((index, item))
    if not normalized:
        return []
    focus_ids = {row.get("id") for row in focus if "id" in row}
    focused = [i for i, (_, item) in enumerate(normalized) if item.get("id") in focus_ids]
    if not focused:
        times = [row["start"] for row in focus if isinstance(row.get("start"), (int, float))]
        if times:
            centre = sum(times) / len(times)
            focused = [min(range(len(normalized)), key=lambda i: abs(normalized[i][1].get("start", centre) - centre))]
        else:
            focused = [len(normalized) - 1]
    anchor = sum(focused) / len(focused)
    nearby = sorted(range(len(normalized)), key=lambda i: min(abs(i - target) for target in focused))
    cue_indices = [i for i, (_, item) in enumerate(normalized) if _ADDRESS_CUE.search(item["text_zh"])]
    cue_indices.sort(key=lambda i: abs(i - anchor))
    cue_neighbors = [j for i in cue_indices for j in (i, i - 1, i + 1) if 0 <= j < len(normalized)]
    # Preserve the current exchange first, then relationships, then other turns.
    priority = focused + nearby[:12] + cue_neighbors + nearby
    selected, size = {}, 0
    for index in priority:
        if index in selected:
            continue
        item = normalized[index][1]
        cost = len(json.dumps(item, ensure_ascii=False)) + 2
        if len(selected) >= max_rows or size + cost > max_chars:
            continue
        selected[index] = item
        size += cost
    return [selected[i] for i in sorted(selected)]


def format_dialogue_context(rows, focus=()):
    return json.dumps(dialogue_context(rows, focus), ensure_ascii=False)


def added_rude_address(previous, candidate):
    """A timing rewrite cannot introduce an unrequested hostile register."""
    def terms(text):
        # These ordinary words are not forms of address (eyebrows, tinkering,
        # elegance). This guard only rejects an added, unambiguous register;
        # full contextual judgement remains with the independent review.
        value = str(text).casefold()
        value = re.sub(r"\b(?:(?:lông|chân|hàng|nhíu|chau)\s+mày|mày\s+mò|thanh\s+tao|tao\s+nhã)\b", "", value)
        return set(re.findall(r"\b(?:tao|mày)\b", value))
    return sorted(terms(candidate) - terms(previous))


_VI_ADDRESS = r"(?:chị|em|anh|cô|chú|bác|con|bố|ba|mẹ|má|tao|mày)"
# Deliberately narrow negative check: pronoun-like positions, not every family
# word. It is not a Vietnamese parser or a positive semantic verification.
_ADDRESS_AS_ACTOR = re.compile(
    rf"\b({_VI_ADDRESS})\s+(?:đã|đang|sẽ|chưa|không|cũng|vẫn|phải|cần|muốn|"
    r"hỏi|nói|đi|đến|về|làm|ăn|biết|hiểu|nghĩ|thấy|nghe|bảo|gọi|đói|khát|mấy|bao nhiêu)\b", re.I)
_ADDRESS_AS_OBJECT = re.compile(
    rf"\b(?:theo|hỏi|bảo|với|cho|giúp|đợi|chờ)\s+({_VI_ADDRESS})\b", re.I)
_LOCAL_VOCATIVE = re.compile(
    r"^\s*(?:拜托|求求|请问|喂|嗨|嘿)?\s*"
    r"(?:妈妈|媽媽|爸爸|姐姐|哥哥|弟弟|妹妹|老师|老師|师傅|師傅|师父|師父|"
    r"爷爷|爺爺|奶奶|叔叔|阿姨|妈|媽|爸|姐|哥)"
    r"[啊呀哎诶唉哦，,！!：:\s]*(?:我|你|您|别|別|请|請|快|能不能|可不可以|帮|幫|让|讓)")


def unproven_relationship_address(source, candidate, context=()):
    """Flag a narrow, known evidence gap in pronoun-bearing dialogue.

    A neutral Chinese pronoun plus a Vietnamese relationship in a pronoun-like
    position needs contextual grounding. Direct source address already provides
    that grounding without speaker metadata. A random ID elsewhere proves
    nothing and must not bypass this guard. ``False`` is NOT an approval of
    equivalence or of the selected direction: independent semantic review is
    still required, including for directly addressed or quoted speech.
    """
    source_text = str(source or "")
    candidate_text = str(candidate or "")
    if not re.search(r"[我你您]", source_text):
        return False
    # These are clear, local vocatives. Merely mentioning someone's mother or
    # quoting a different character is deliberately not the same evidence.
    if _LOCAL_VOCATIVE.search(source_text):
        return False
    # A real matching speaker AND addressee can connect an explicit earlier
    # vocative to this turn. An ID on an unrelated row, gender label, or merely
    # the same words spoken by somebody else cannot authorize that transfer.
    rows = [row for row in (context or []) if isinstance(row, dict)]
    text = lambda row: str(row.get("text_zh") or row.get("asr_text") or row.get("zh", ""))
    current = [row for row in rows if text(row) == source_text]
    if len(current) == 1:
        current = current[0]
        speaker, listener = current.get("speaker_id"), current.get("addressee_id")
        if speaker not in (None, "") and listener not in (None, ""):
            if any(row.get("speaker_id") == speaker and row.get("addressee_id") == listener
                   and _LOCAL_VOCATIVE.search(text(row)) for row in rows):
                return False
    # Ordinary noun uses must not be turned into pronoun warnings. Source
    # kinship nouns also leave judgement with the semantic reviewer: this guard
    # targets pronoun assignments when Chinese does not name that relationship.
    # A named source noun can ground a matching Vietnamese noun object (我问
    # 妈妈 -> tôi hỏi mẹ), but it does not ground a new speaker pronoun. Keep
    # this mapping intentionally small; it is not a relationship classifier.
    noun_pairs = (("妈妈", "mẹ"), ("媽媽", "mẹ"), ("妈", "mẹ"), ("媽", "mẹ"),
                  ("爸爸", "bố"), ("爸", "bố"), ("姐姐", "chị"), ("姐", "chị"),
                  ("哥哥", "anh"), ("哥", "anh"), ("弟弟", "em"), ("弟", "em"),
                  ("妹妹", "em"), ("妹", "em"), ("老师", "thầy"), ("老師", "thầy"))
    candidate_folded = candidate_text.casefold()
    if any(source_term in source_text and vietnamese_term in candidate_folded
           for source_term, vietnamese_term in noun_pairs):
        # The matching word is grounded as a referent; an unrelated actor
        # pronoun in the same candidate is still checked by the patterns below.
        if not _ADDRESS_AS_ACTOR.search(candidate_folded):
            return False
    return bool(_ADDRESS_AS_ACTOR.search(candidate_text) or _ADDRESS_AS_OBJECT.search(candidate_text))


