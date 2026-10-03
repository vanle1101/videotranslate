import os
import sys
import time
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from config import settings
from core.services.service_manager import service_manager

from PySide6.QtCore import Qt, QTimer, QUrl, QEventLoop
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings

IMAGES_DIR = APP_ROOT / "docs" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

def wait_for(ms: int):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()

def run_js_sync(view: QWebEngineView, code: str):
    res = []
    loop = QEventLoop()
    def _cb(val):
        res.append(val)
        loop.quit()
    view.page().runJavaScript(code, _cb)
    loop.exec()
    return res[0] if res else None

def capture_ui_screenshots():
    print("[*] Starting backend for UI screenshot capture...")
    port = service_manager.start_backend(timeout=30.0)
    url = f"http://127.0.0.1:{port}"
    print(f"[+] Backend running on: {url}")

    app = QApplication.instance() or QApplication(sys.argv)

    view = QWebEngineView()
    view.resize(1440, 900)
    view.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    view.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    view.settings().setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)

    print("[*] Loading UI inside QWebEngineView...")
    view.load(QUrl(url))
    view.show()

    # Wait for page load
    loop = QEventLoop()
    view.loadFinished.connect(lambda ok: loop.quit())
    loop.exec()
    print("[+] Page load finished, waiting for fonts & assets...")
    wait_for(2000)

    # Inject realistic demo state for high-impact screenshot
    demo_script = """
    (() => {
        // Update hardware pill
        const hw = document.getElementById("gpu-info-text");
        if (hw) hw.textContent = "NVIDIA RTX 4090 (24GB VRAM) · CUDA 12.4";

        // Update telemetry ribbon
        const tPlay = document.getElementById("tel-playing");
        if (tPlay) tPlay.textContent = "00:24";

        const tPlayable = document.getElementById("tel-playable");
        if (tPlayable) tPlayable.textContent = "01:10";

        const tBuf = document.getElementById("tel-buffer");
        if (tBuf) {
            tBuf.textContent = "+46.0s (Ahead)";
            tBuf.className = "px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-950/80 text-emerald-400 border border-emerald-800/80";
        }

        const tTtfp = document.getElementById("tel-ttfp");
        if (tTtfp) tTtfp.textContent = "3.2s";

        const tRtf = document.getElementById("tel-rtf");
        if (tRtf) tRtf.textContent = "1.52x";

        const tSup = document.getElementById("tel-suppression");
        if (tSup) tSup.textContent = "-26.0 dB (Center Cancellation)";

        // Segment list
        const segContainer = document.getElementById("segments-list");
        if (segContainer) {
            segContainer.innerHTML = `
                <div class="p-3.5 rounded-xl border border-pink-500/40 bg-pink-950/20 shadow-md space-y-2 relative overflow-hidden">
                    <div class="flex items-center justify-between text-xs">
                        <span class="font-mono text-pink-400 font-bold flex items-center gap-1.5">
                            <span class="h-2 w-2 rounded-full bg-pink-500 animate-pulse"></span>
                            #0 [00:00.00 - 00:04.20]
                        </span>
                        <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                            <i class="fa-solid fa-check mr-1"></i> READY (0.28s)
                        </span>
                    </div>
                    <p class="text-xs text-gray-300 font-medium font-sans">
                        <span class="text-gray-500 mr-1.5 font-mono">ZH:</span>今天带大家来打卡成都最火的蛋烘糕！
                    </p>
                    <p class="text-xs text-white font-semibold font-sans bg-black/40 p-2 rounded-lg border border-gray-800">
                        <span class="text-pink-400 mr-1.5 font-mono">VI:</span>Hôm nay dắt mọi người đi ăn bánh nướng trứng hot nhất Thành Đô nha!
                    </p>
                </div>
                <div class="p-3.5 rounded-xl border border-gray-800/90 bg-[#12151f] hover:border-gray-700 space-y-2">
                    <div class="flex items-center justify-between text-xs">
                        <span class="font-mono text-gray-400 font-semibold">#1 [00:04.20 - 00:08.50]</span>
                        <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                            <i class="fa-solid fa-check mr-1"></i> READY (0.31s)
                        </span>
                    </div>
                    <p class="text-xs text-gray-300 font-medium font-sans">
                        <span class="text-gray-500 mr-1.5 font-mono">ZH:</span>老板娘的手速真的是绝了，肉松奶油太香了吧！
                    </p>
                    <p class="text-xs text-gray-200 font-sans bg-black/30 p-2 rounded-lg border border-gray-800/80">
                        <span class="text-emerald-400 mr-1.5 font-mono">VI:</span>Tay nghề bà chủ đỉnh thật sự, kem trứng ruốc thơm nức mũi luôn!
                    </p>
                </div>
                <div class="p-3.5 rounded-xl border border-gray-800/90 bg-[#12151f] hover:border-gray-700 space-y-2">
                    <div class="flex items-center justify-between text-xs">
                        <span class="font-mono text-gray-400 font-semibold">#2 [00:08.50 - 00:13.80]</span>
                        <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                            <i class="fa-solid fa-check mr-1"></i> READY (0.35s)
                        </span>
                    </div>
                    <p class="text-xs text-gray-300 font-medium font-sans">
                        <span class="text-gray-500 mr-1.5 font-mono">ZH:</span>外酥里嫩，一口下去满满都是幸福感！
                    </p>
                    <p class="text-xs text-gray-200 font-sans bg-black/30 p-2 rounded-lg border border-gray-800/80">
                        <span class="text-emerald-400 mr-1.5 font-mono">VI:</span>Vỏ ngoài giòn rụm bên trong mềm mịn, cắn một miếng ngập tràn hạnh phúc!
                    </p>
                </div>
                <div class="p-3.5 rounded-xl border border-cyan-500/30 bg-cyan-950/10 space-y-2">
                    <div class="flex items-center justify-between text-xs">
                        <span class="font-mono text-cyan-400 font-semibold">#3 [00:13.80 - 00:18.00]</span>
                        <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 animate-pulse">
                            <i class="fa-solid fa-spinner fa-spin mr-1"></i> DUBBING (VieNeu-TTS)
                        </span>
                    </div>
                    <p class="text-xs text-gray-400 font-medium font-sans">
                        <span class="text-gray-500 mr-1.5 font-mono">ZH:</span>记得点赞收藏，下期带你们吃钵钵鸡！
                    </p>
                    <p class="text-xs text-gray-300 font-sans bg-black/20 p-2 rounded-lg border border-gray-800/50">
                        <span class="text-cyan-400 mr-1.5 font-mono">VI:</span>Nhớ thả tim lưu lại nha, tập sau dắt mọi người đi ăn gà xiên que!
                    </p>
                </div>
            `;
        }
    })();
    """
    run_js_sync(view, demo_script)
    wait_for(1000)

    # 1. Capture Main Studio Dashboard
    dash_path = IMAGES_DIR / "studio_dashboard.png"
    pix_dash = view.grab()
    pix_dash.save(str(dash_path))
    print(f"[+] Saved Main Dashboard: {dash_path}")

    # 2. Switch to Models Tab & Capture
    run_js_sync(view, "document.getElementById('tab-models').click();")
    wait_for(800)
    models_path = IMAGES_DIR / "models_tab.png"
    pix_models = view.grab()
    pix_models.save(str(models_path))
    print(f"[+] Saved Models Tab: {models_path}")

    # 3. Switch to Settings Tab & Capture
    run_js_sync(view, "document.getElementById('tab-settings').click();")
    wait_for(800)
    settings_path = IMAGES_DIR / "settings_tab.png"
    pix_settings = view.grab()
    pix_settings.save(str(settings_path))
    print(f"[+] Saved Settings Tab: {settings_path}")

    view.close()
    service_manager.shutdown_all()
    print("[+] UI Screenshots successfully captured.")

def create_hero_banner():
    """
    Generates a stunning, ultra-clean GitHub Hero Banner
    combining branding, tagline, feature badges, and a perspective-framed studio preview.
    Matches the sleek aesthetic of modern developer tools.
    """
    print("[*] Generating Hero Banner (docs/images/banner.png)...")
    banner_w, banner_h = 1280, 520
    banner = Image.new("RGBA", (banner_w, banner_h), (11, 13, 19, 255))
    draw = ImageDraw.Draw(banner)

    # Background subtle radial gradient / glow
    for r in range(400, 0, -10):
        alpha = int(45 * (r / 400))
        draw.ellipse([800 - r, 200 - r, 800 + r, 200 + r], fill=(236, 72, 153, alpha // 8))
        draw.ellipse([1100 - r, 350 - r, 1100 + r, 350 + r], fill=(139, 92, 246, alpha // 10))

    # Grid dots background effect
    for gx in range(40, banner_w, 40):
        for gy in range(40, banner_h, 40):
            draw.ellipse([gx, gy, gx + 2, gy + 2], fill=(255, 255, 255, 12))

    # Left Side: Branding & Badges
    try:
        font_title = ImageFont.truetype("arialbd.ttf", 46)
        font_sub = ImageFont.truetype("arial.ttf", 16)
        font_badge = ImageFont.truetype("arialbd.ttf", 12)
        font_bold_small = ImageFont.truetype("arialbd.ttf", 14)
    except Exception:
        font_title = ImageFont.load_default()
        font_sub = font_title
        font_badge = font_title
        font_bold_small = font_title

    # App Logo Pill (Top Left)
    lx, ly = 60, 60
    draw.rounded_rectangle([lx, ly, lx + 54, ly + 54], radius=16, fill=(244, 63, 94, 255))
    draw.polygon([(lx + 32, ly + 12), (lx + 16, ly + 28), (lx + 28, ly + 28), (lx + 22, ly + 42), (lx + 38, ly + 24), (lx + 26, ly + 24)], fill=(255, 255, 255))

    # Title
    draw.text((lx + 70, ly + 2), "Douyin2TikTok AI Studio", fill=(255, 255, 255), font=font_title)
    
    # Subtitle / Tagline
    tagline_lines = [
        "Realtime Chinese-to-Vietnamese Video Dubbing & Subtitling Studio",
        "Tách thoại Trung · Giữ nhạc nền BGM · FunAudioLLM SenseVoice",
        "Gemini VideoLingo Translation · VieNeu-TTS v3 · BS-RoFormer HQ"
    ]
    ty = ly + 72
    for line in tagline_lines:
        draw.text((lx, ty), line, fill=(148, 163, 184), font=font_sub)
        ty += 24

    # Feature Badges Row
    badges = [
        ("⚡ REALTIME STREAMING", (236, 72, 153), (80, 10, 40)),
        ("🎙️ VIENEU-TTS v3 TURBO", (139, 92, 246), (35, 15, 75)),
        ("🎵 BS-ROFORMER HQ", (16, 185, 129), (5, 45, 30)),
        ("✨ 1-CLICK LAUNCHER", (245, 158, 11), (50, 30, 5))
    ]
    by = ty + 24
    bx = lx
    for text, border_c, bg_c in badges:
        bw = len(text) * 8 + 24
        draw.rounded_rectangle([bx, by, bx + bw, by + 30], radius=8, fill=bg_c, outline=border_c, width=1)
        draw.text((bx + 12, by + 7), text, fill=(255, 255, 255), font=font_badge)
        bx += bw + 12

    # Bottom status indicator
    draw.ellipse([lx, banner_h - 70, lx + 10, banner_h - 60], fill=(52, 211, 153))
    draw.text((lx + 20, banner_h - 72), "Standalone Windows Desktop Edition (Zero terminal, Zero localhost)", fill=(100, 116, 139), font=font_bold_small)

    # Right Side: Framed Screenshot Preview
    dash_img_path = IMAGES_DIR / "studio_dashboard.png"
    if dash_img_path.exists():
        dash = Image.open(dash_img_path)
        # Crop top dashboard section and resize
        target_w, target_h = 580, 360
        dash_cropped = dash.resize((target_w, target_h), Image.Resampling.LANCZOS)

        # Rounded corners & shadow for frame
        frame_x, frame_y = 640, 80
        # Draw frame shadow
        for s in range(12, 0, -2):
            draw.rounded_rectangle(
                [frame_x - s, frame_y - s, frame_x + target_w + s, frame_y + target_h + s],
                radius=18,
                fill=(0, 0, 0, 30)
            )

        # Paste screenshot
        banner.paste(dash_cropped, (frame_x, frame_y))
        # Draw elegant border
        draw.rounded_rectangle([frame_x, frame_y, frame_x + target_w, frame_y + target_h], radius=14, outline=(75, 85, 99, 180), width=2)

    banner_path = IMAGES_DIR / "banner.png"
    banner.save(str(banner_path))
    print(f"[+] Saved Hero Banner: {banner_path} ({banner_path.stat().st_size / 1024:.1f} KB)")

if __name__ == "__main__":
    capture_ui_screenshots()
    create_hero_banner()
