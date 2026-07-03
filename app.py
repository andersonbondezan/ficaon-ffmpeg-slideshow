import os
import subprocess
import tempfile
import shutil
import urllib.request
from flask import Flask, request, send_file, jsonify

app = Flask(__name__)
TOKEN = os.environ.get("SLIDESHOW_TOKEN", "ugc_slideshow_7yKp29Qm")


def fetch(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        f.write(r.read())


@app.route("/health")
def health():
    return "ok", 200


@app.route("/slideshow", methods=["POST"])
def slideshow():
    if request.headers.get("x-token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    images = data.get("images") or []
    secs = float(data.get("seconds_each", 3))
    images = [u for u in images if isinstance(u, str) and u.startswith("http")][:10]
    if len(images) < 2:
        return jsonify({"error": "need at least 2 image URLs"}), 400

    work = tempfile.mkdtemp()
    try:
        paths = []
        for i, url in enumerate(images):
            p = os.path.join(work, "img%d.in" % i)
            fetch(url, p)
            paths.append(p)

        out = os.path.join(work, "reel.mp4")
        total = secs * len(paths)

        inputs = []
        for p in paths:
            inputs += ["-loop", "1", "-t", str(secs), "-i", p]
        inputs += ["-f", "lavfi", "-t", str(total), "-i", "anullsrc=r=44100:cl=stereo"]

        parts = []
        for i in range(len(paths)):
            parts.append(
                "[%d:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p[v%d]"
                % (i, i)
            )
        concat_in = "".join("[v%d]" % i for i in range(len(paths)))
        filt = ";".join(parts) + ";%sconcat=n=%d:v=1:a=0[v]" % (concat_in, len(paths))
        audio_idx = len(paths)

        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filt,
            "-map", "[v]", "-map", "%d:a" % audio_idx,
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", out,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if res.returncode != 0 or not os.path.exists(out):
            return jsonify({"error": "ffmpeg failed", "stderr": res.stderr[-1500:]}), 500

        return send_file(out, mimetype="video/mp4", as_attachment=True, download_name="reel.mp4")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.route("/concat", methods=["POST"])
def concat():
    if request.headers.get("x-token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    videos = data.get("videos") or []
    videos = [u for u in videos if isinstance(u, str) and u.startswith("http")][:8]
    if len(videos) < 1:
        return jsonify({"error": "need at least 1 video URL"}), 400

    work = tempfile.mkdtemp()
    try:
        paths = []
        for i, url in enumerate(videos):
            p = os.path.join(work, "clip%d.mp4" % i)
            fetch(url, p)
            paths.append(p)

        out = os.path.join(work, "final.mp4")

        # Re-encode each clip to a common format (1080x1920, 30fps, stereo 44.1k)
        # and concat preserving audio. Robust even if clips differ slightly.
        inputs = []
        for p in paths:
            inputs += ["-i", p]

        parts = []
        for i in range(len(paths)):
            parts.append(
                "[%d:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30,format=yuv420p[v%d];"
                "[%d:a]aresample=44100,aformat=channel_layouts=stereo[a%d]"
                % (i, i, i, i)
            )
        concat_in = "".join("[v%d][a%d]" % (i, i) for i in range(len(paths)))
        filt = ";".join(parts) + ";%sconcat=n=%d:v=1:a=1[v][a]" % (concat_in, len(paths))

        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filt,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode != 0 or not os.path.exists(out):
            return jsonify({"error": "ffmpeg failed", "stderr": res.stderr[-1500:]}), 500

        return send_file(out, mimetype="video/mp4", as_attachment=True, download_name="final.mp4")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(work, ignore_errors=True)


FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


@app.route("/watermark", methods=["POST"])
def watermark():
    if request.headers.get("x-token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    video_url = data.get("video")
    text = str(data.get("text") or request.args.get("text") or "Fica ON Brasil")

    # escapa aspas simples e dois-pontos, que quebram a sintaxe do filtro drawtext
    safe_text = text.replace("\\", "").replace("'", "").replace(":", "\\:")

    work = tempfile.mkdtemp()
    try:
        src = os.path.join(work, "in.mp4")
        if video_url and isinstance(video_url, str) and video_url.startswith("http"):
            fetch(video_url, src)
        elif request.data:
            # corpo cru (binario) enviado direto, sem passar por uma URL publica
            with open(src, "wb") as f:
                f.write(request.data)
        else:
            return jsonify({"error": "need a video URL (JSON {video: url}) or raw video body"}), 400
        out = os.path.join(work, "watermarked.mp4")

        drawtext = (
            "drawtext=fontfile=%s:text='%s':fontsize=42:fontcolor=white@0.92:"
            "box=1:boxcolor=black@0.45:boxborderw=16:x=(w-text_w)/2:y=h-th-60"
            % (FONT_PATH, safe_text)
        )

        cmd = [
            "ffmpeg", "-y", "-i", src,
            "-vf", drawtext,
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", out,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if res.returncode != 0 or not os.path.exists(out):
            return jsonify({"error": "ffmpeg failed", "stderr": res.stderr[-1500:]}), 500

        return send_file(out, mimetype="video/mp4", as_attachment=True, download_name="watermarked.mp4")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
