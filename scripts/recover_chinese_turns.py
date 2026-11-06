"""Native Chinese decode of independently detected code-switch regions."""
from pathlib import Path
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.recover_korean_source import atomic_json


def main():
    sys.stdout.reconfigure(encoding='utf8')
    from core.media_process import run_media
    from core.engines.asr.whisper_process import SubprocessWhisperModel
    from core.engines.asr.sensevoice_engine import SenseVoiceEngine
    import soundfile as sf
    directory = ROOT / 'workspace/audit/korean_recovery_20261014'
    project = json.loads((ROOT / 'workspace/projects/2b5ef22b.json').read_text(encoding='utf8'))
    source = project['session']['video_path']
    recognizer = SubprocessWhisperModel('medium', device='cpu')
    sense = SenseVoiceEngine(language='zh')
    sense._ensure_loaded()
    results = []
    for start, end in [(13, 24), (88.7, 101), (117, 125), (148, 155.5), (339.3, 342), (351, 353)]:
        path = directory / f'zh_{int(start*1000)}.json'
        if path.exists():
            results.append(json.loads(path.read_text(encoding='utf8')))
            continue
        wav = directory / f'zh_{int(start*1000)}.wav'
        run_media(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(start), '-t', str(end-start),
                   '-i', source, '-vn', '-ac', '1', '-ar', '16000', str(wav)])
        samples, rate = sf.read(str(wav), dtype='float32')
        stream = sense.recognizer.create_stream()
        stream.accept_waveform(rate, samples)
        sense.recognizer.decode_stream(stream)
        evidence = {'text': stream.result.text, 'language': stream.result.lang,
                    'tokens': [{'text': text, 'at': start+at} for text, at in zip(stream.result.tokens, stream.result.timestamps)]}
        print(f'CHINESE {start}-{end}: {evidence["text"]}', flush=True)
        parts, _ = recognizer.transcribe(wav, language='zh', task='transcribe', beam_size=5,
                                        word_timestamps=True, vad_filter=False, condition_on_previous_text=False)
        rows = [{'start': start+part.start, 'end': start+part.end, 'text': part.text.strip(),
                 'source_language': 'zh', 'words': [{'text': w.word, 'start': start+w.start, 'end': start+w.end}
                                                   for w in part.words]} for part in parts]
        result = {'start': start, 'end': end, 'sensevoice': evidence, 'segments': rows,
                  'source_method': 'medium-zh-novad-original-audio'}
        atomic_json(path, result)
        results.append(result)
        atomic_json(directory/'chinese_turns.json', results)
        print(f'SAVED Chinese {start}-{end}', flush=True)
    atomic_json(directory/'chinese_turns.json', results)


if __name__ == '__main__':
    main()
