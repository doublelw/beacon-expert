"""用无头浏览器驱动交互爆炸图，出静帧和视频（确定性：同参数同画面）。

    python render_media.py 爆炸图.html --stills 输出目录          # 装配/爆炸/剖切/运转四张
    python render_media.py 爆炸图.html --video 输出.mp4 [--seconds 8] [--fps 30]
    可选：--size 1920x1080  --dark  --clean（不带界面，纯画面）

视频脚本：装配态缓慢环绕→齿轮运转→展开爆炸→爆炸态继续环绕→收回。
需要 playwright 与 chromium（首次：python -m playwright install chromium）、ffmpeg。
"""
import argparse, subprocess, shutil, math
from pathlib import Path
from playwright.sync_api import sync_playwright

ARGS_TRY = [["--use-angle=metal", "--enable-gpu"], ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]]


def open_page(p, html, w, h):
    last = None
    for args in ARGS_TRY:
        b = p.chromium.launch(args=args)
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(Path(html).resolve().as_uri() + "?static")
        try:
            pg.wait_for_function("window.CAD && window.CAD.ready", timeout=60000)
            if not errs:
                return b, pg
            last = errs
        except Exception as e:
            last = e
        b.close()
    raise RuntimeError(f"网页没有就绪：{last}")


def smooth(x):
    return x * x * (3 - 2 * x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--stills")
    ap.add_argument("--video")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--dark", action="store_true")
    ap.add_argument("--clean", action="store_true")
    a = ap.parse_args()
    w, h = map(int, a.size.split("x"))
    with sync_playwright() as p:
        b, pg = open_page(p, a.html, w, h)
        base = dict(clean=a.clean, dark=a.dark)
        if a.stills:
            out = Path(a.stills); out.mkdir(parents=True, exist_ok=True)
            shots = {
                "01-装配": dict(explode=0, section=False, labels=False, t=0, cam=[-38, 24]),
                "02-爆炸": dict(explode=1, section=False, labels=True, t=0, cam=[-38, 18]),
                "03-剖切": dict(explode=0, section=True, labels=False, t=0, cam=[-62, 20]),
                "04-爆炸剖切": dict(explode=0.55, section=True, labels=False, t=0.15, cam=[-50, 26]),
            }
            for name, s in shots.items():
                pg.evaluate("o=>CAD.set(o)", {**base, **s})
                pg.screenshot(path=str(out / f"{name}.png"))
                print(out / f"{name}.png")
        if a.video:
            frames = Path(a.video).with_suffix("")
            frames = frames.parent / (frames.name + "_frames")
            if frames.exists():
                shutil.rmtree(frames)
            frames.mkdir(parents=True)
            n = int(a.seconds * a.fps)
            for i in range(n):
                u = i / (n - 1)
                az = -60 + 70 * u                       # 环绕 70°
                el = 26 - 6 * math.sin(u * math.pi)
                # 0-25% 装配运转；25-50% 展开；50-80% 爆炸态运转；80-100% 收回
                if u < .25:
                    ex = 0
                elif u < .5:
                    ex = smooth((u - .25) / .25)
                elif u < .8:
                    ex = 1
                else:
                    ex = 1 - smooth((u - .8) / .2)
                t = u * a.seconds * 0.35
                pg.evaluate("o=>CAD.set(o)", {**base, "explode": ex, "t": t, "cam": [az, el], "labels": False, "section": False})
                pg.screenshot(path=str(frames / f"f{i:05d}.png"))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(a.fps), "-i", str(frames / "f%05d.png"),
                            "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", a.video], check=True)
            shutil.rmtree(frames)
            print(a.video)
        b.close()


if __name__ == "__main__":
    main()
