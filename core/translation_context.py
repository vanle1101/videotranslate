"""Shared, source-grounded dialogue context for Vietnamese translation.

This module never infers a speaker or translates a pronoun by a keyword rule.
It retains source evidence for the language model, including earlier forms of
address which may have fallen outside the immediately preceding dialogue.
"""
from __future__ import annotations

import json
import re


ADDRESS_POLICY_REVISION = 6

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
  chỉ đổi chiều khi có căn cứ đổi lượt. Tuy nhiên, thiếu dấu đổi lượt KHÔNG là
  bằng chứng cùng người nói: phải xét cả cách đọc nối lời và hỏi–đáp/đối chiếu
  giữa những người khác nhau. Không mặc định tao hoặc tự luân phiên
  chị/em theo số câu; không gán một chiều đại từ cho toàn bộ nhân vật.
- Các câu tự giới thiệu, tuổi hoặc quan điểm đối lập ở gần nhau có thể là hai
  người đáp nhau, lời sửa lại của một người, hoặc lời trích. Kiểm tra từng cách
  đọc với cả câu trước và sau; không ép hai 我 thành cùng người chỉ vì không có
  speaker_id. Giữ nguyên đối lập, số và người sở hữu ý, không bỏ 我 khi việc bỏ
  khiến hai lời tự giới thiệu thành một chuỗi số không rõ ai nói.
- Phân biệt quan hệ với tuổi ở thời điểm cảnh đang diễn ra. Trong phim có thể
  có hồi tưởng, du hành thời gian, đổi thân phận hoặc lời giả định. Năm sinh/tuổi
  không tự chứng minh người đang nói, không dùng lịch hiện tại hoặc giả định
  bố/mẹ luôn lớn tuổi hơn để loại một cách phân vai hợp lời nguồn. Không tự bịa
  du hành thời gian; giữ các cách đọc còn hợp lý và báo chưa rõ nếu nguồn thiếu.
- 姐/哥 có thể là cách gọi xã giao; 爸/妈/老师 có thể nằm trong lời kể hoặc lời trích.
  Một từ đơn lẻ không chứng minh quan hệ ruột thịt, người nói, tuổi hay giới tính.
  Không gán quan hệ của cảnh trước cho nhân vật mới. Dùng mốc câu, lời gọi/đáp,
  nhân vật và bằng chứng thật; không bịa nhận diện giọng hay hình khi chỉ có chữ.
- Mệnh lệnh, dấu chấm than, 快说 hoặc giọng gấp KHÔNG tự cho phép dùng tao/mày.
  Chỉ dùng xưng hô thô khi ngữ cảnh gốc đủ chứng minh; không tự làm thoại gây gổ.
  Không viết tắt đại từ thành m/t, ko, hoặc tiếng chat trong lời để đọc.
- Nếu chưa xác định được người nói/người nghe hoặc quan hệ: ưu tiên câu trung tính,
  lược đại từ khi tự nhiên và không mất nghĩa. Đánh giá điều chưa rõ có ảnh hưởng
  đến CHÍNH lời Việt hay không: câu trung tính giữ đủ người làm/người chịu tác động,
  đối lập và sắc thái có thể được xác nhận nghĩa mà không xác nhận quan hệ. Chỉ giữ
  needs_review=true nếu lời Việt còn dựa vào cách phân vai chưa rõ hoặc việc lược
  làm mất ý. Tôi/bạn vẫn là cách xưng hô cần rà, không tự xem là trung tính an toàn.
  Không tự coi bản dịch trôi chảy là bằng chứng quan hệ.
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

# Select complete role phrases without deciding their meaning or certainty.
# The provider may ground ``cô gái`` or ``bố mẹ`` as one role. Splitting the
# phrase makes its exact comparison with that independently read role fail.
# Enumerate the phrases: repeated calls such as ``Mẹ, mẹ`` remain separate
# occurrences, and this selector must never equate a phrase with its prefix.
_ADDRESS_MULTIWORD = re.compile(
    r"(?<!\w)(?:cô\s+gái|anh\s+trai|chị\s+gái|em\s+(?:gái|trai)|"
    r"(?:bố|ba)\s+(?:mẹ|má)|(?:mẹ|má)\s+(?:bố|ba))(?!\w)", re.I)


def dialogue_context(rows, focus=(), *, max_rows=64, max_chars=18000):
    """Bound context while retaining nearby turns and distant address evidence.

    Stable source IDs/timestamps preserve direction and chronology. The draft
    remains explicitly untrusted. Older evidence includes its adjacent turns
    so an isolated kinship keyword is less likely to be mistaken for a speaker.
    """
    rows = list(rows)
    # Timestamp order, never mapping/insertion order, governs continuity.
    if rows and all(isinstance(row.get("start"), (int, float)) for row in rows):
        rows = sorted(rows, key=lambda row: (row["start"], row.get("end", row["start"])))
    normalized = []
    for index, row in enumerate(rows):
        source = row.get("text_zh") or row.get("asr_text") or row.get("zh", "")
        if not isinstance(source, str) or not source.strip():
            continue
        item = {key: row[key] for key in ("id", "start", "end", "speaker_id", "addressee_id")
                if key in row and isinstance(row[key], (str, int, float))}
        item["text_zh"] = source[:1500]
        if row.get("is_focus") is True:
            item["is_focus"] = True
        # The selector can be called more than once (for example when a
        # reviewed context is formatted for a second provider request). Keep
        # source-risk markers from the first pass instead of silently making
        # the same row look more certain on the second pass.
        if row.get("source_truncated") or len(source) > 1500:
            item["source_truncated"] = True
        source_audit = row.get("verification") or {}
        if not isinstance(source_audit, dict):
            source_audit = {}
        if (row.get("source_needs_review") or row.get("source_truncated")
                or ("asr_text" in row and not row.get("text_zh"))
                or (row.get("needs_review") and source_audit.get("source_supported") is not True)):
            item["source_needs_review"] = True
        draft = row.get("final_vi", row.get("vi", ""))
        if isinstance(draft, str) and draft:
            item["final_vi"] = draft[:1500]
            item["translation_is_draft"] = True
        elif row.get("translation_is_draft"):
            item["translation_is_draft"] = True
        verification = row.get("verification") or {}
        if (isinstance(verification, dict) and verification.get("address_verified") is True
                and isinstance(verification.get("address_context"), dict)):
            item["reviewed_address_context"] = verification["address_context"]
        elif isinstance(row.get("reviewed_address_context"), dict):
            item["reviewed_address_context"] = row["reviewed_address_context"]
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


def focus_identity(rows):
    focus = [row for row in rows if row.get("is_focus") is True]
    if len(focus) != 1:
        return {}
    return {key: focus[0][key] for key in ("id", "start", "end") if key in focus[0]}


def address_expressions(candidate):
    """Select text needing a pronoun audit; never infer roles or translate it.

    Selection and verification must use the same vocabulary. Remove ordinary
    noun/third-person spans only, preserving any other pronoun in the sentence.
    """
    text = re.sub(r"\b(?:cô ấy|anh ấy|chị ấy|ông ấy|bà ấy|cậu ấy)\b", "", str(candidate or ""), flags=re.I)
    text = re.sub(r"\bcon\s+(?:mèo|vật|số|đường|người)\b", "", text, flags=re.I)
    text = re.sub(r"\bmột\s+mình\b", "", text, flags=re.I)
    # A small set of unambiguous numeric-unit spans is not an address. Keep
    # the rest of the sentence: 'Ba chờ ba phút' still contains the parent.
    text = re.sub(r"\bba\s+(?:phút|giây|giờ|ngày|tuần|tháng|năm|lần|chiếc|cái)\b", "", text, flags=re.I)
    text = re.sub(r"\bthứ\s+ba\b", "", text, flags=re.I)
    # Extract multiword role terms first and mask their spans so the token
    # pass cannot split them into unrelated address uses.  Return everything
    # in source order; the provider's address_uses is ordered the same way.
    spans = [(match.start(), match.end(), match.group(0))
             for match in _ADDRESS_MULTIWORD.finditer(text)]
    masked = list(text)
    for start, end, _ in spans:
        masked[start:end] = [" "] * (end - start)
    token_re = re.compile(
        r"(?<!\w)(?:tôi|tao|tớ|mình|bạn|mày|chị|em|anh|cô|chú|bác|con|bố|ba|mẹ|má|"
        r"thầy|cậu|ông|bà|ta|cháu|dì|cụ|cưng|ngươi|mi)(?!\w)", re.I)
    spans.extend((match.start(), match.end(), match.group(0))
                 for match in token_re.finditer("".join(masked)))
    return [term for _, _, term in sorted(spans, key=lambda item: item[0])]


def contains_address_expression(candidate):
    return bool(address_expressions(candidate))


def needs_address_audit(rows, context=()):
    """Select contextual dialogue for a semantic audit, never choose pronouns."""
    all_rows = list(rows) + list(context)
    return any(_ADDRESS_CUE.search(str(row.get("text_zh", row.get("zh", ""))))
               or contains_address_expression(row.get("final_vi", row.get("vi", "")))
               for row in all_rows)


def source_dialogue(rows):
    """Remove every Vietnamese draft before the independent source reading."""
    return [{key: value for key, value in row.items()
             if key in {"id", "start", "end", "text_zh", "asr_text", "speaker_id", "addressee_id",
                        "source_needs_review", "source_truncated"}}
            for row in rows]


def address_reading_prompt(rows, context):
    return (VIETNAMESE_ADDRESS_POLICY + "\nĐỌC NGỮ CẢNH XƯNG HÔ TỪ NGUỒN, CHƯA CÓ BẢN VIỆT. "
        "Đọc theo thứ tự thời gian. Xác định nối tiếp cùng người nói hay đổi lượt bằng lời nguồn; "
        "không bắt buộc có speaker_id, không tự gán danh tính, giới tính hoặc luân phiên theo segment. "
        "OCR chỉ chứng minh chữ nguồn, KHÔNG tự chứng minh ai nói. Được kết luận từ mạch đối thoại "
        "và lời gọi/đáp nếu đủ rõ; nếu hai cách phân vai vẫn hợp lý thì uncertain=true. "
        "Với câu phụ thuộc nối vai từ câu khác, so sánh ÍT NHẤT cách đọc cùng người nói và "
        "cách đọc đổi người/hỏi–đáp/đối chiếu; xét thêm tự sửa và trích dẫn khi phù hợp. "
        "Phải có căn cứ khẳng định cách đọc được chọn; 'không thấy đổi lượt' không đủ. "
        "Tìm mâu thuẫn về người sở hữu lời, số tuổi, năm sinh, lời đáp và câu kế tiếp. "
        "Không loại cách đọc chỉ bằng suy luận tuổi theo lịch hiện tại hoặc vai bố/mẹ. "
        "Chưa dịch câu; chỉ đề xuất xưng hô cho từng ID cần kiểm định, dẫn câu nguồn chính xác. "
        "Trả JSON {\"address_context\":[{\"id\":0,\"self_address\":\"\","
        "\"listener_address\":\"\",\"self_uncertain\":true,\"listener_uncertain\":true,"
        "\"uncertain\":true,\"reason\":\"lý do cho từng vai\","
        "\"turn_check\":{\"ambiguous_roles\":[\"self\"],"
        "\"reason\":\"so sánh cách đọc cùng/khác người; căn cứ loại hoặc giữ mỗi cách\","
        "\"evidence\":[{\"id\":0,\"quote\":\"câu nguồn thực có\"}]},"
        "\"evidence\":[{\"id\":0,\"quote\":\"câu nguồn thực có\"}]}]}. "
        "Xét self_uncertain và listener_uncertain RIÊNG: biết lời gọi người nghe không đồng nghĩa "
        "biết người nói phải tự xưng gì. Ví dụ 拜托姐 xác nhận listener_address=chị, "
        "listener_uncertain=false; nếu chưa rõ tự xưng thì self_address rỗng, self_uncertain=true, "
        "uncertain=true. Không cần biết tên, giới tính, tuổi hay ruột thịt để giữ đúng lời gọi trực tiếp. "
        "Không suy sự chắc chắn này sang câu kế chỉ vì gần thời gian. uncertain là kết luận toàn bộ; "
        "true nếu còn vai chưa xác định. Mỗi *_address chỉ ghi cách xưng/gọi được đề xuất, không ghi giải thích. "
        "Giữ nguyên cụm cách gọi đầy đủ, ví dụ 'cô gái' hay 'bố mẹ', không chỉ ghi từ đầu cụm. "
        "Cụm tập thể chỉ được xác nhận khi nguồn chứng minh chính nhóm đó; không tự bỏ qua vai chưa rõ. "
        "Mỗi ID có turn_check. ambiguous_roles chỉ chứa self/listener: ghi vai mà các cách phân "
        "lượt còn hợp lý khiến xưng hô khác nhau; [] khi đã có căn cứ loại các cách còn lại hoặc "
        "câu không phụ thuộc phân lượt. Mọi vai trong ambiguous_roles phải *_uncertain=true. "
        "turn_check.evidence phải có chính ID đang xét cùng các câu làm căn cứ, trích nguồn "
        "đủ nhận ra lời tiếp hay lời đáp. Trực tiếp gọi chị/mẹ có thể rõ listener dù self chưa rõ. "
        "Đại từ có thể rỗng nếu không cần hoặc chưa rõ; khi *_address rỗng, "
        "bắt buộc đặt *_uncertain=true và uncertain=true. *_uncertain=false chỉ khi "
        "*_address không rỗng và có dẫn chứng nguồn. Vai không dùng không được ghi false với "
        "tên rỗng; bước kiểm định lời Việt sẽ xét riêng các vai thực sự dùng. "
        "Không chèn lời Việt nháp hay sửa lời nguồn. "
        "Mỗi ID trả đúng một lần. Dẫn đủ bằng chứng nối cách gọi với câu hiện tại, không chỉ trích một từ rời.\n"
        + "ID cần kiểm định: " + json.dumps([row["id"] for row in rows])
        + "\nNguồn thoại theo thời gian: " + json.dumps(source_dialogue(context), ensure_ascii=False))


def validate_address_reading(data, rows, context, *, require_turn_check=False):
    """Validate cited source text; this is not itself proof of interpretation."""
    if not isinstance(data, dict) or not isinstance(data.get("address_context"), list):
        raise ValueError("Thiếu kết quả đọc ngữ cảnh xưng hô.")
    expected = {row["id"] for row in rows}
    by_id = {row["id"]: str(row.get("text_zh", row.get("asr_text", "")))
             for row in context if "id" in row}
    result = {}
    for row in data["address_context"]:
        if (not isinstance(row, dict) or type(row.get("id")) is not int
                or row["id"] not in expected or row["id"] in result
                or type(row.get("uncertain")) is not bool
                or not all(isinstance(row.get(key), str) for key in ("self_address", "listener_address", "reason"))
                or not row["reason"].strip() or not isinstance(row.get("evidence"), list)):
            raise ValueError("Kết quả ngữ cảnh xưng hô không đúng cấu trúc.")
        if not row["uncertain"] and not row["evidence"]:
            raise ValueError("Chưa có bằng chứng cho kết luận xưng hô.")
        for role in ("self", "listener"):
            flag = f"{role}_uncertain"
            if flag in row and type(row[flag]) is not bool:
                raise ValueError("Kết luận từng vai xưng hô phải là boolean.")
            if row.get(flag) is False and (not row[f"{role}_address"].strip() or not row["evidence"]):
                raise ValueError("Vai xưng hô đã xác định phải có cách gọi và dẫn chứng nguồn.")
            if row.get(flag) is True and row["uncertain"] is False:
                raise ValueError("Kết luận toàn bộ không được bỏ qua vai xưng hô chưa rõ.")
        for citation in row["evidence"]:
            if (not isinstance(citation, dict) or type(citation.get("id")) is not int
                    or citation["id"] not in by_id or not isinstance(citation.get("quote"), str)
                    or not citation["quote"].strip() or citation["quote"] not in by_id[citation["id"]]):
                raise ValueError("Dẫn chứng xưng hô không khớp lời nguồn.")
        checked = dict(row)
        # Older saved readings did not contain a competing-turn analysis. Do
        # not invent one on their behalf. When the provider does declare that
        # several parses remain possible, its positive confidence fields must
        # never override that declaration, even if its quotes are exact.
        turn_check = row.get("turn_check")
        if require_turn_check and "turn_check" not in row:
            raise ValueError("Thiếu kiểm tra các cách phân lượt đối thoại.")
        if "turn_check" in row:
            if (not isinstance(turn_check, dict)
                    or not isinstance(turn_check.get("ambiguous_roles"), list)
                    or any(not isinstance(role, str) or role not in {"self", "listener"}
                           for role in turn_check["ambiguous_roles"])
                    or not isinstance(turn_check.get("reason"), str)
                    or not turn_check["reason"].strip()
                    or not isinstance(turn_check.get("evidence"), list)
                    or not turn_check["evidence"]):
                raise ValueError("Thiếu kiểm tra các cách phân lượt đối thoại.")
            turn_refs = set()
            for citation in turn_check["evidence"]:
                if (not isinstance(citation, dict) or type(citation.get("id")) is not int
                        or citation["id"] not in by_id or not isinstance(citation.get("quote"), str)
                        or not citation["quote"].strip() or citation["quote"] not in by_id[citation["id"]]):
                    raise ValueError("Dẫn chứng phân lượt không khớp lời nguồn.")
                turn_refs.add(citation["id"])
            if row["id"] not in turn_refs:
                raise ValueError("Kiểm tra phân lượt chưa dẫn chính câu đang xét.")
            for role in turn_check["ambiguous_roles"]:
                checked[f"{role}_uncertain"] = True
                checked["uncertain"] = True
            if turn_check["ambiguous_roles"]:
                checked["reason"] = row["reason"] + " Kiểm tra phân lượt: " + turn_check["reason"]
            # These citations must also participate in the source-version
            # invalidation used by later corrections and persisted reviews.
            checked["evidence"] = list(row["evidence"])
            for citation in turn_check["evidence"]:
                if citation not in checked["evidence"]:
                    checked["evidence"].append(dict(citation))
        result[row["id"]] = checked
    if set(result) != expected:
        raise ValueError("Thiếu câu khi đọc ngữ cảnh xưng hô.")
    return result


def address_review_instruction(reading):
    if not reading:
        return ""
    return ("\nĐã có lượt đọc ngữ cảnh RIÊNG chỉ từ nguồn Trung, không nhìn bản Việt: "
        + json.dumps(list(reading.values()), ensure_ascii=False)
        + "\nĐối chiếu lại kết luận này với nguồn, không chấp thuận máy móc. Với MỖI segment, thêm "
          "address_applicable (boolean): true chỉ khi final_vi thực sự chọn/đổi một quan hệ hoặc "
          "chiều xưng hô; câu trung tính, số, tên riêng, thán từ và lời kể không áp dụng. "
          "Kiểm tra final_vi theo "
          "vai người nói/người nghe và nối câu liên tục, sửa bản Việt nếu sai chiều. "
          "Rà lại turn_check với cả lời trước và sau: thiếu dấu đổi lượt không chứng minh cùng "
          "người; không dùng tuổi/năm sinh và lịch hiện tại để tự bác cách phân vai trong phim. "
          "Không ép các lời tự giới thiệu/đối chiếu của hai người thành cùng người, và không bỏ "
          "chủ thể để làm mất đối lập. Các vai còn nhiều cách đọc phải giữ chưa xác nhận. "
          "Mỗi segment phải thêm address_verified (boolean) và address_reason (lý do cụ thể) nếu áp dụng. "
          "Thêm address_uses là mảng theo thứ tự TỪNG lần xuất hiện xưng hô trong final_vi, "
          "mỗi mục {\"term\":\"chị\",\"role\":\"listener\"}; role chỉ self hoặc listener. "
          "Ghi đủ cả từ lặp, đúng từ đã dùng; 'Chị ơi, nhờ chị đấy!' có hai mục chị/listener. "
          "'Em nhờ chị' có em/self và chị/listener. Không đổi vai của từ để né kết luận chưa rõ. "
          "Giữ nguyên cả cụm 'cô gái', 'anh trai', 'chị gái', 'em gái', 'em trai', 'bố mẹ', 'ba má' "
          "và các cụm bố/ba với mẹ/má: mỗi lần xuất hiện cụm là một mục có term đầy đủ, "
          "không tách 'bố mẹ' thành bố và mẹ, không đổi 'cô gái' thành cô. "
          "Cụm được dùng phải khớp toàn bộ cách gọi của vai đã được đọc riêng từ nguồn; "
          "không suy cụm đúng chỉ vì khớp một từ đầu, và không xác nhận tập thể khi thành viên còn chưa rõ. "
          "address_verified=true chỉ khi cách xưng hô của final_vi thực tế phù hợp nguồn/mạch thoại; "
          "Nếu address_applicable=false, thêm address_neutral_faithful=true chỉ khi đã kiểm tra "
          "câu nguồn và final_vi giữ đủ ý/người làm/người chịu tác động mà không cần phân vai; "
          "nêu đối chiếu cụ thể trong address_reason. Không được đặt true chỉ vì đã bỏ đại từ; "
          "bỏ người thực hiện hoặc mất đối lập 'tôi mới là người hỏi' phải false và needs_review=true. "
          "OCR trùng chữ không đủ. Đối chiếu CHỈ các vai thực sự xuất hiện trong final_vi: "
          "uncertain=true toàn bộ không chặn lời gọi listener đã được listener_uncertain=false xác nhận "
          "nếu final_vi không tự xưng hoặc thêm quan hệ khác. Tương tự cho self. "
          "Ví dụ chưa rõ tự xưng không ngăn 'Chị ơi, nhờ chị đấy!' khi nguồn trực tiếp 拜托姐. "
          "Nếu bất kỳ vai được dùng còn *_uncertain=true/thiếu kết luận, hoặc còn hai cách phân vai "
          "ảnh hưởng đến lời Việt, address_verified=false, needs_review=true. "
          "Không tự xác nhận em/chị ở câu tiếp theo chỉ vì mốc thời gian gần; không xác nhận chỉ vì bỏ đại từ. "
          "Nếu lời nguồn mới được sửa làm thay đổi căn cứ, address_verified=false để đọc lại nguồn.")


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
