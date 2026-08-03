import os
import subprocess
import tempfile
import shutil
import urllib.request
from flask import Flask, request, send_file, jsonify
import yt_dlp

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


@app.route("/tiktok/hashtag", methods=["POST"])
def tiktok_hashtag():
    if request.headers.get("x-token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    hashtag = str(data.get("hashtag") or "").strip().lstrip("#")
    limit = int(data.get("limit") or 10)
    limit = max(1, min(limit, 30))
    if not hashtag:
        return jsonify({"error": "missing hashtag"}), 400

    url = "https://www.tiktok.com/tag/%s" % hashtag
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "playlistend": limit,
        "socket_timeout": 30,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = (info or {}).get("entries") or []
        results = []
        for e in entries[:limit]:
            if not e:
                continue
            results.append({
                "id": e.get("id"),
                "url": e.get("webpage_url") or e.get("url"),
                "description": e.get("description") or e.get("title") or "",
                "view_count": e.get("view_count"),
                "like_count": e.get("like_count"),
                "duration": e.get("duration"),
                "cover_url": e.get("thumbnail"),
                "timestamp": e.get("timestamp"),
                "uploader": e.get("uploader") or e.get("channel"),
            })
        return jsonify({"hashtag": hashtag, "results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/tiktok/video", methods=["POST"])
def tiktok_video():
    if request.headers.get("x-token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    video_url = str(data.get("url") or "").strip()
    if not video_url:
        return jsonify({"error": "missing url"}), 400

    work = tempfile.mkdtemp()
    try:
        out_tmpl = os.path.join(work, "video.%(ext)s")
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": out_tmpl,
            "format": "mp4/best",
            "socket_timeout": 60,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])

        files = [f for f in os.listdir(work) if f.startswith("video.")]
        if not files:
            return jsonify({"error": "download produced no file"}), 500
        out = os.path.join(work, files[0])
        return send_file(out, mimetype="video/mp4", as_attachment=True, download_name="tiktok.mp4")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.route("/tiktok/cover", methods=["POST"])
def tiktok_cover():
    if request.headers.get("x-token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    cover_url = str(data.get("url") or "").strip()
    if not cover_url or not cover_url.startswith("http"):
        return jsonify({"error": "missing url"}), 400

    work = tempfile.mkdtemp()
    try:
        out = os.path.join(work, "cover.jpg")
        fetch(cover_url, out)
        return send_file(out, mimetype="image/jpeg", as_attachment=True, download_name="cover.jpg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
