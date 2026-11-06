"""Read-only, resumable Korean phrase recovery; never edits project state.

Each 40s ownership region is decoded with 4s context on either side. Raw
overlap alternatives remain in chunk files and the final audit ledger; ambiguous
boundaries are explicitly reported for review rather than silently merged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def atomic_json(path, value):
    path = Path(path)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    pending.replace(path)


def windows(duration):
    index = 0
    while index * 40 < duration:
        start, end = index * 40., min((index + 1) * 40., duration)
        yield index, start, end, max(0., start - 4), min(duration, end + 4)
        index += 1


def owns(row, start, end, duration):
    midpoint = (row['start'] + row['end']) / 2
    return start <= midpoint < end or (end == duration and midpoint == end)


def assemble(chunks, identity, duration):
    rows, alternatives = [], []
    for chunk in chunks:
        for row in chunk['rows']:
            target = rows if owns(row, chunk['core_start'], chunk['core_end'], duration) else alternatives
            target.append({**row, 'chunk': chunk['index']})
    rows.sort(key=lambda row: (row['start'], row['end']))
    issues = []
    for index, row in enumerate(rows):
        row['id'] = index
        if index and row['start'] < rows[index - 1]['end']:
            issues.append({'code': 'overlapping_phrases', 'ids': [index - 1, index],
                           'cross_chunk': row['chunk'] != rows[index - 1]['chunk']})
        if row['end'] - row['start'] < .15:
            issues.append({'code': 'short_phrase', 'ids': [index]})
    completed_end = max((chunk['core_end'] for chunk in chunks), default=0)
    for index, alternative in enumerate(alternatives):
        if (alternative['start'] + alternative['end']) / 2 >= completed_end:
            continue  # Neighboring ownership region has not been decoded yet.
        related = [row['id'] for row in rows if row['chunk'] != alternative['chunk']
                   and row['start'] < alternative['end'] and alternative['start'] < row['end']]
        if not related:
            issues.append({'code': 'unmatched_overlap_alternative', 'alternative_index': index,
                           'start': alternative['start'], 'end': alternative['end']})
    return {'version': 1, 'identity': identity, 'language': 'ko', 'duration': duration,
            'status': 'complete' if len(chunks) == len(list(windows(duration))) else 'running',
            'completed_chunks': len(chunks), 'total_chunks': len(list(windows(duration))),
            'source_method': 'faster-whisper-medium-ko-phrase', 'segments': rows,
            'boundary_review': issues, 'overlap_alternatives': alternatives,
            'note': 'ASR hypothesis, not independently verified. Original task unchanged.'}


def main():
    sys.stdout.reconfigure(encoding='utf8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', default='2b5ef22b')
    parser.add_argument('--output', default='workspace/audit/korean_recovery_20261014')
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    parser.add_argument('--no-vad', action='store_true')
    args = parser.parse_args()
    from config import settings
    from core.media_process import run_media
    from core.engines.asr.whisper_process import SubprocessWhisperModel
    manifest = json.loads((ROOT / 'workspace/projects' / f'{args.task}.json').read_text(encoding='utf8'))
    source = Path(manifest['session']['video_path']).resolve(strict=True)
    duration = float(manifest['session']['total_duration'])
    model = ROOT / 'workspace/models/faster-whisper-medium'
    if not all((model / name).is_file() for name in ('config.json', 'model.bin', 'tokenizer.json')):
        raise RuntimeError('Existing medium model missing; downloads are not allowed.')
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    options = {'language': 'ko', 'task': 'transcribe', 'beam_size': 5,
               'vad_filter': not args.no_vad, 'vad_parameters': {'min_silence_duration_ms': 400},
               'condition_on_previous_text': False, 'word_timestamps': True}
    identity = {'sha256': digest.hexdigest(), 'duration': duration, 'model': 'medium',
                'model_size': (model / 'model.bin').stat().st_size,
                'model_mtime_ns': (model / 'model.bin').stat().st_mtime_ns,
                'options': options, 'core_seconds': 40, 'overlap_seconds': 4,
                'device': args.device, 'compute_type': 'int8'}
    destination = ROOT / args.output
    destination.mkdir(parents=True, exist_ok=True)
    settings.WHISPER_COMPUTE_TYPE = 'int8'
    recognizer = SubprocessWhisperModel('medium', device=args.device)
    chunks = []
    for index, start, end, decode_start, decode_end in windows(duration):
        checkpoint = destination / f'chunk_{index:03d}.json'
        chunk = json.loads(checkpoint.read_text(encoding='utf8')) if checkpoint.exists() else None
        if not chunk or chunk.get('identity') != identity:
            print(f'ASR chunk {index + 1}/10 core={start:.2f}-{end:.2f} decode={decode_start:.2f}-{decode_end:.2f}', flush=True)
            wav = destination / f'chunk_{index:03d}.wav'
            run_media(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(decode_start),
                       '-t', str(decode_end - decode_start), '-i', str(source),
                       '-vn', '-ac', '1', '-ar', '16000', str(wav)])
            decoded, _ = recognizer.transcribe(wav, **options)
            rows = [{'start': round(decode_start + row.start, 3),
                     'end': round(min(duration, decode_start + row.end), 3),
                     'text': row.text.strip(),
                     'words': [{'text': word.word, 'start': round(decode_start + word.start, 3),
                                'end': round(decode_start + word.end, 3)} for word in row.words]}
                    for row in decoded]
            chunk = {'identity': identity, 'index': index, 'core_start': start, 'core_end': end,
                     'decode_start': decode_start, 'decode_end': decode_end, 'rows': rows}
            atomic_json(checkpoint, chunk)
        chunks.append(chunk)
        result = assemble(chunks, identity, duration)
        atomic_json(destination / 'source.json', result)
        print(f'SAVED {index + 1}/{result["total_chunks"]} phrases={len(result["segments"])} seconds={end:.2f}', flush=True)
    print(f'COMPLETE {destination / "source.json"}', flush=True)


if __name__ == '__main__':
    main()
