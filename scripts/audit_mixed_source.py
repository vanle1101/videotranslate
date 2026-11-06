"""Independent automatic-language evidence in bounded original-audio windows."""
from pathlib import Path
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.recover_korean_source import atomic_json


def main():
    sys.stdout.reconfigure(encoding='utf8')
    from core.engines.asr.sensevoice_engine import SenseVoiceEngine
    import soundfile as sf
    directory = ROOT / 'workspace/audit/korean_recovery_20261014'
    output = directory / 'independent_evidence.json'
    recognizer = SenseVoiceEngine(language='')
    recognizer._ensure_loaded()
    evidence = json.loads(output.read_text(encoding='utf8')) if output.exists() else []
    for index in range(10):
        chunk = json.loads((directory / f'chunk_{index:03d}.json').read_text(encoding='utf8'))
        samples, rate = sf.read(str(directory / f'chunk_{index:03d}.wav'), dtype='float32')
        for start in range(int(chunk['core_start']), int(chunk['core_end']), 10):
            end = min(start + 10, chunk['core_end'])
            if any(row['start'] == start for row in evidence):
                continue
            crop_start, crop_end = max(chunk['decode_start'], start - 1), min(chunk['decode_end'], end + 1)
            left, right = int((crop_start - chunk['decode_start']) * rate), int((crop_end - chunk['decode_start']) * rate)
            stream = recognizer.recognizer.create_stream()
            stream.accept_waveform(rate, samples[left:right])
            recognizer.recognizer.decode_stream(stream)
            result = stream.result
            evidence.append({'start': start, 'end': end, 'crop_start': crop_start, 'crop_end': crop_end,
                             'language': result.lang, 'text': result.text,
                             'tokens': [{'text': text, 'at': crop_start + at}
                                        for text, at in zip(result.tokens, result.timestamps)],
                             'source_method': 'sensevoice-auto-original-audio'})
            atomic_json(output, evidence)
            print(f'{start:.1f}-{end:.1f} {result.lang} {result.text}', flush=True)


if __name__ == '__main__':
    main()
