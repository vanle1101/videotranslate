"""Apply evidence-based editorial corrections to this isolated recovery only."""
import asyncio
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TASK='recovered_2b5ef22b_kozh'
CORRECTIONS={
    29: ('Tôi thề đấy!', '我儿豁 là lời thề phương ngữ Tứ Xuyên, không phải câu nói đùa.'),
    64: ('Khỏi thối lại.', 'Đối thoại mua gà; âm nhận dạng gần 거스름돈은 됐어, liên hệ tiền thừa được trả ở câu 74; nguồn vẫn cần rà.'),
    73: ('Này, tiền thừa của cậu.', 'OCR đúng thời điểm: 同学 / 你的找零; ASR 걸음이 là lỗi nghe 거스름돈, không phải bảo quay đi.'),
    85: ('Rốt cuộc cậu làm nghề gì?', 'OCR 你到底是干什么的 và câu trả lời làm thêm, không hỏi hành động tức thời.'),
    88: ('Vừa học vừa làm kiếm tiền.', 'OCR 勤工俭学, nguồn âm thanh độc lập 공부하면서 돈 벌어요.'),
    89: ('Giờ hình như tôi đã biết...', 'Mệnh đề tiếp ở câu sau 네가 누군지; OCR 我想...现在我大概 / 知道你是谁了.'),
    90: ('cậu là ai rồi.', 'Nối nghĩa với mệnh đề trước, giữ cùng chủ thể/người nghe.'),
    96: ('Này, người mới!', 'OCR 喂新来的; đây là lời gọi người mới, không thông báo có người vừa tới.'),
}

async def main():
    sys.stdout.reconfigure(encoding='utf8')
    from core.streaming.session_store import restore_saved_session
    from core.streaming.status_history import record_status
    from scripts.publish_multilingual_recovery import srt_text, atomic_json
    audit=ROOT/'workspace/audit'/('published_'+TASK)
    if (audit/'publisher.lock').exists():
        raise RuntimeError('Publisher still owns project')
    session=restore_saved_session(TASK)
    session.is_running=True
    session.is_stopped=False
    session.pause_event.set()
    changes=[]
    for sid,(text,reason) in CORRECTIONS.items():
        row=session.segments[sid]
        if (row.verification or {}).get('status')=='manual':
            continue
        if row.final_vi==text:
            continue
        changes.append({'id':sid,'before':row.final_vi,'after':text,'reason':reason})
        if row.audio_path:
            row.superseded_audio_paths.append(row.audio_path)
        row.audio_path=row.audio_url=None
        row.final_vi=row.literal_vi=row.natural_vi=text
        row.revision+=1
        row.status,row.failed_stage='WAITING','TTS'
        row.subtitle_cues=[]
        row.speech_start=row.speech_end=None
        row.tts_duration=0
        row.verification={**(row.verification or {}),'status':'unresolved',
                          'semantic_verified':False,'reason':reason}
        row.needs_review=True
        row.review_reason=reason
        session.persist()
        try:
            await session._synthesize_segment(row,allow_pacing=False)
            row.error=None
        except Exception as error:
            row.status='FAILED'
            row.failed_stage='ALIGNING' if type(error).__name__=='SpeechBudgetError' else 'TTS'
            row.error=str(error)
        session.persist()
        print(sid,row.status,flush=True)
    session.is_running=False
    failed=sum(r.status!='READY' for r in session.segments.values())
    session.error=f'Còn {failed} câu chưa có giọng; toàn bộ 100 câu đã có lời Việt.' if failed else None
    session._refresh_processed_duration()
    session._recalculate_telemetry()
    record_status(session,'FAILED' if failed else 'COMPLETED','native_recovery',
                  session.error or 'Đã dịch và tạo giọng bản phục hồi.')
    session.persist()
    atomic_json(audit/'editorial_corrections.json',changes)
    rows=[{**r.to_dict(),'text_vi':r.final_vi} for r in session.segments.values()]
    (audit/'recovered.vi.srt').write_text(srt_text(rows),encoding='utf-8-sig')
    print(json.dumps({'ready':len(rows)-failed,'failed':failed,'translated':len(rows)}))

if __name__=='__main__':
    asyncio.run(main())
