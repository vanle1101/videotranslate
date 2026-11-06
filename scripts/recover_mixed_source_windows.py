"""Short native-language windows; complementary original source hypotheses."""
from pathlib import Path
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.recover_korean_source import atomic_json

REGIONS = [(0,13,'ko'),(13,24,'zh'),(24,88.7,'ko'),(88.7,101,'zh'),
           (101,117,'ko'),(117,125,'zh'),(125,148,'ko'),(148,155.5,'zh'),
           (155.5,339.3,'ko'),(339.3,342,'zh'),(342,351,'ko'),(351,353,'zh'),(353,395.782676,'ko')]


def main():
    sys.stdout.reconfigure(encoding='utf8')
    from core.media_process import run_media
    from core.engines.asr.whisper_process import SubprocessWhisperModel
    directory = ROOT/'workspace/audit/korean_recovery_20261014/short_windows'
    directory.mkdir(exist_ok=True)
    project = json.loads((ROOT/'workspace/projects/2b5ef22b.json').read_text(encoding='utf8'))
    source = project['session']['video_path']
    recognizer = SubprocessWhisperModel('small', device='cpu')
    records=[]
    for region_start,region_end,language in REGIONS:
        start=region_start
        while start < region_end:
            end=min(region_end,start+16)
            decode_start=max(region_start,start-2)
            decode_end=min(region_end,end+2)
            filename=directory/f'{int(start*1000):09d}.json'
            if filename.exists():
                record=json.loads(filename.read_text(encoding='utf8'))
            else:
                wav=filename.with_suffix('.wav')
                run_media(['ffmpeg','-v','error','-nostdin','-y','-ss',str(decode_start),'-t',str(decode_end-decode_start),
                           '-i',source,'-vn','-ac','1','-ar','16000',str(wav)])
                rows,_=recognizer.transcribe(wav,language=language,beam_size=5,word_timestamps=True,
                                            vad_filter=False,condition_on_previous_text=False)
                record={'core_start':start,'core_end':end,'crop_start':decode_start,'crop_end':decode_end,
                        'source_language':language,'model':'small-cpu-int8','segments':[
                            {'start':decode_start+r.start,'end':decode_start+r.end,'text':r.text.strip(),
                             'words':[{'text':w.word,'start':decode_start+w.start,'end':decode_start+w.end} for w in r.words]}
                            for r in rows]}
                atomic_json(filename,record)
            records.append(record)
            atomic_json(directory/'all.json',records)
            print(f'SHORT {start:.2f}-{end:.2f} {language}: '+str([(r['start'],r['end'],r['text']) for r in record['segments']]),flush=True)
            start=end


if __name__ == '__main__':
    main()
