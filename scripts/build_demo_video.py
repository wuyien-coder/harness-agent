"""把 record_demo.py 生成的帧序列合成为带阶段字幕的演示视频。

依赖：ffmpeg（需在 PATH）。字幕用 PIL 画在每帧底部深色条上，中文用微软雅黑。

产物：docs/assets/demo.mp4（H.264，1440 宽，约 30 秒）

用法：
    python scripts/build_demo_video.py
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
FRAMES = ASSETS / "frames"
MANIFEST = ASSETS / "frames-manifest.json"
OUT = ASSETS / "demo.mp4"
WORK = ASSETS / "_video_frames"

FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
FPS = 1
TITLE = "Agent Harness · Plan-and-Execute 问答演示"


def load_manifest() -> dict[str, str]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {item["frame"]: item["label"] for item in data}


def annotate() -> list[Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    labels = load_manifest()
    frames = sorted(FRAMES.glob("frame-*.png"))
    out: list[Path] = []

    for i, frame in enumerate(frames):
        img = Image.open(frame).convert("RGB")
        w, h = img.size
        label = labels.get(frame.name, "")
        # 底部加 64px 字幕条
        bar_h = 72
        canvas = Image.new("RGB", (w, h + bar_h), (24, 28, 38))
        canvas.paste(img, (0, 0))
        draw = ImageDraw.Draw(canvas)
        try:
            font_label = ImageFont.truetype(FONT_PATH, 26)
            font_num = ImageFont.truetype(FONT_PATH, 20)
        except OSError:
            font_label = ImageFont.load_default()
            font_num = font_label

        text = f"{i + 1:02d} / {len(frames):02d}   {label}" if label else f"{i + 1:02d} / {len(frames):02d}"
        draw.text((28, h + 14), text, fill=(235, 238, 245), font=font_label)
        # 顶部标题角标
        draw.rectangle([0, 0, min(560, w), 46], fill=(24, 28, 38))
        draw.text((20, 10), TITLE, fill=(160, 200, 255), font=font_num)

        target = WORK / f"v-{i:03d}.png"
        canvas.save(target, optimize=True)
        out.append(target)
    return out


def encode(frames: list[Path]) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(FPS),
        "-i", str(WORK / "v-%03d.png"),
        "-vf", "scale=1440:-2",
        "-c:v", "libx264", "-preset", "medium", "-crf", "26",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(OUT),
    ]
    subprocess.run(cmd, check=True)


def cleanup() -> None:
    for f in WORK.glob("v-*.png"):
        f.unlink(missing_ok=True)
    try:
        WORK.rmdir()
    except OSError:
        pass


if __name__ == "__main__":
    annotated = annotate()
    print(f"已标注 {len(annotated)} 帧")
    encode(annotated)
    size = OUT.stat().st_size / 1024
    print(f"视频: {OUT} ({size:.0f} KB, {len(annotated) / FPS:.0f}s)")
    cleanup()
