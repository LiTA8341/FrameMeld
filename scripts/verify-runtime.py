"""Verify a packaged runtime, including the synthetic silent-truncation case."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def verify(root: Path) -> dict:
    manifest = json.loads((root / "runtime-manifest.json").read_text(encoding="utf-8-sig"))
    results = {}
    for name in ("ffmpeg", "ffprobe"):
        core = root / "lib/ffmpeg" / ("ffmpeg-core.exe" if name == "ffmpeg" else "ffprobe.exe")
        digest = hashlib.file_digest(core.open("rb"), "sha256").hexdigest()
        assert digest == manifest["ffmpeg"][f"{name}_sha256"], f"Wrong {name} hash"
        for tool in (root / f"{name}.exe", core):
            output = subprocess.check_output([str(tool), "-version"], text=True, encoding="utf-8")
            assert output.startswith(f"{name} version {manifest['ffmpeg']['version']}"), output[:150]
            results[str(tool.relative_to(root))] = output.splitlines()[0]
    cap = json.loads(subprocess.check_output([str(root / 'ffmpeg.exe'), '-framemeld', '--capabilities-json'], text=True))
    assert cap['version'] == manifest['distribution']['version'] == '0.1.5'
    assert cap['protocol'] == 'org.framemeld.cli' and cap['api_version'] == 1
    assert 'independent-sharpen-v1' in cap['features']
    with tempfile.TemporaryDirectory(prefix="framemeld-regression-") as tmp:
        for relative in ('ffmpeg.exe', 'lib/ffmpeg/ffmpeg-core.exe'):
            output = Path(tmp) / 'regression.framecrc'
            command = [str(root / relative), '-y', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'testsrc2=s=16x16:r=1/6:d=6[out0];sine=r=48000:d=6,asetnsamples=n=1[out1]',
                '-filter_complex', '[0:v]trim=end=6,setpts=PTS-STARTPTS[v0];[0:a]atrim=end=6,asetpts=PTS-STARTPTS[a0];[v0][a0]concat=n=1:v=1:a=1[v][a];[a]asetnsamples=n=4096:p=0[aout]',
                '-map', '[v]', '-map', '[aout]', '-c:v', 'rawvideo', '-c:a', 'pcm_s16le', '-f', 'framecrc', str(output)]
            subprocess.run(command, check=True, timeout=120)
            samples = sum(int(line.split(',')[3]) for line in output.read_text().splitlines() if line.startswith('1,'))
            assert samples == 288000, f'{relative}: truncated audio {samples}/288000'
            results[relative + ':audio_samples'] = samples
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.runtime.resolve()), indent=2))
