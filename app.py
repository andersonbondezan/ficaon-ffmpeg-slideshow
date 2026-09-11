import os
import subprocess
import tempfile
import shutil
import glob
import random
import urllib.request
from flask import Flask, request, send_file, jsonify
import yt_dlp
import edge_tts

app = Flask(__name__)
TOKEN = os.environ.get("SLIDESHOW_TOKEN", "ugc_slideshow_7yKp29Qm")
BACKGROUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backgrounds")
DEFAULT_VOICE = "pt-BR-AntonioNeural"


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
    audio_url = data.get("audioUrl")
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

        # Se veio audioUrl, baixa o áudio de verdade e usa a duração real dele
        # pra dividir entre as imagens - assim a narração nunca é cortada.
        audio_path = None
        if audio_url and isinstance(audio_url, str) and audio_url.startswith("http"):
            audio_path = os.path.join(work, "audio.in")
            fetch(audio_url, audio_path)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path],
                capture_output=True, text=True, timeout=30,
            )
            try:
                audio_duration = float(probe.stdout.strip())
            except (ValueError, TypeError):
                audio_duration = None
            if audio_duration and audio_duration > 0:
                secs = audio_duration / len(paths)

        total = secs * len(paths)

        inputs = []
        for p in paths:
            inputs += ["-loop", "1", "-t", str(secs), "-i", p]
        if audio_path:
            inputs += ["-i", audio_path]
        else:
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


def _concat_mp4s(paths, out_path, width=1080, height=1920, timeout=600):
    # Re-encode each clip pro mesmo formato (resolucao/fps/audio) e concatena
    # preservando audio. Robusto mesmo se os clipes vierem levemente diferentes.
    inputs = []
    for p in paths:
        inputs += ["-i", p]

    parts = []
    for i in range(len(paths)):
        parts.append(
            "[%d:v]scale=%d:%d:force_original_aspect_ratio=decrease,"
            "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30,format=yuv420p[v%d];"
            "[%d:a]aresample=44100,aformat=channel_layouts=stereo[a%d]"
            % (i, width, height, width, height, i, i, i)
        )
    concat_in = "".join("[v%d][a%d]" % (i, i) for i in range(len(paths)))
    filt = ";".join(parts) + ";%sconcat=n=%d:v=1:a=1[v][a]" % (concat_in, len(paths))

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filt,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("ffmpeg concat failed: %s" % res.stderr[-1500:])


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
        _concat_mp4s(paths, out, width=1080, height=1920, timeout=600)

        return send_file(out, mimetype="video/mp4", as_attachment=True, download_name="final.mp4")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(work, ignore_errors=True)


FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _wrap_text(text, max_chars):
    words = text.split()
    lines = []
    cur = ""
    for w in words:
        candidate = (cur + " " + w).strip()
        if len(candidate) <= max_chars or not cur:
            cur = candidate
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit_watermark_text(text):
    # Escolhe fonte + quebra de linha pra caber em 1080px de largura sem cortar,
    # não importa o tamanho do texto recebido (hook variavel + sufixo de marca).
    for fontsize, max_chars in ((42, 30), (34, 36), (26, 42)):
        lines = _wrap_text(text, max_chars)
        if len(lines) <= 3:
            return fontsize, lines
    # texto extremo: força 3 linhas na menor fonte, truncando o resto
    fontsize, max_chars = 26, 42
    lines = _wrap_text(text, max_chars)[:3]
    if lines:
        lines[-1] = lines[-1][: max_chars - 1] + "…"
    return fontsize, lines


@app.route("/watermark", methods=["POST"])
def watermark():
    if request.headers.get("x-token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    video_url = data.get("video")
    text = str(data.get("text") or request.args.get("text") or "Fica ON Brasil")

    fontsize, lines = _fit_watermark_text(text)
    # escapa aspas simples e dois-pontos (quebram a sintaxe do filtro drawtext) linha a linha
    safe_lines = [l.replace("\\", "").replace("'", "").replace(":", "\\:") for l in lines]
    safe_text = "\n".join(safe_lines)

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
            "drawtext=fontfile=%s:text='%s':fontsize=%d:fontcolor=white@0.92:"
            "line_spacing=8:box=1:boxcolor=black@0.45:boxborderw=16:x=(w-text_w)/2:y=h-th-60"
            % (FONT_PATH, safe_text, fontsize)
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


def _pick_background_image():
    imgs = glob.glob(os.path.join(BACKGROUNDS_DIR, "images", "*.jpg"))
    imgs += glob.glob(os.path.join(BACKGROUNDS_DIR, "images", "*.jpeg"))
    imgs += glob.glob(os.path.join(BACKGROUNDS_DIR, "images", "*.png"))
    return random.choice(imgs) if imgs else None


def _srt_timestamp(seconds):
    ms_total = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms_total, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def _synthesize_once(text, voice, audio_path):
    # WordBoundary offset/duration do edge-tts vêm em unidades de 100ns.
    words = []
    communicate = edge_tts.Communicate(text, voice)
    with open(audio_path, "wb") as f:
        for chunk in communicate.stream_sync():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append({
                    "start": chunk["offset"] / 10_000_000,
                    "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                    "text": chunk["text"],
                })
    return words


def _write_srt(groups, srt_path):
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, (start, end, ws) in enumerate(groups, 1):
            line = " ".join(w["text"] for w in ws)
            f.write("%d\n%s --> %s\n%s\n\n" % (i, _srt_timestamp(start), _srt_timestamp(end), line))


def _group_words(words):
    # agrupa palavras em blocos de legenda (~8 palavras ou ~4s, o que vier primeiro)
    groups = []
    cur = []
    cur_start = words[0]["start"]
    for w in words:
        if cur and (len(cur) >= 8 or (w["end"] - cur_start) > 4.0):
            groups.append((cur_start, cur[-1]["end"], cur))
            cur = []
            cur_start = w["start"]
        cur.append(w)
    if cur:
        groups.append((cur_start, cur[-1]["end"], cur))
    return groups


def _fallback_words_by_duration(text, audio_path):
    # edge-tts as vezes nao devolve WordBoundary nenhum (falha intermitente do
    # servico da Microsoft, nao do nosso codigo) - em vez de deixar a legenda
    # vazia, distribui as palavras uniformemente pela duracao real do audio.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        duration = float(probe.stdout.strip())
    except (ValueError, TypeError):
        duration = None
    tokens = text.split()
    if not duration or duration <= 0 or not tokens:
        return []
    per_word = duration / len(tokens)
    words = []
    for i, tok in enumerate(tokens):
        words.append({"start": i * per_word, "end": (i + 1) * per_word, "text": tok})
    return words


def _synthesize_with_captions(text, voice, audio_path, srt_path):
    words = _synthesize_once(text, voice, audio_path)
    if not words:
        # falha intermitente conhecida do edge-tts - tenta de novo antes de desistir
        words = _synthesize_once(text, voice, audio_path)
    if not words:
        # ainda sem boundaries: gera legenda com timing aproximado em vez de
        # deixar o video sair sem captions nenhuma.
        words = _fallback_words_by_duration(text, audio_path)
    if not words:
        open(srt_path, "w", encoding="utf-8").close()
        return
    _write_srt(_group_words(words), srt_path)


def _render_segment(texto, voice, image_path, out_path, fps=30, burn_captions=True):
    # Renderiza 1 trecho isolado: narracao (+ legenda queimada, opcional) sobre
    # a imagem (ou cor solida de fallback) daquele trecho, com Ken Burns pela
    # duracao real do audio desse trecho.
    work = os.path.dirname(out_path)
    tag = os.path.splitext(os.path.basename(out_path))[0]
    audio_path = os.path.join(work, "%s_audio.mp3" % tag)
    srt_path = os.path.join(work, "%s_captions.srt" % tag)
    _synthesize_with_captions(texto, voice, audio_path, srt_path)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        duration = float(probe.stdout.strip())
    except (ValueError, TypeError):
        duration = None
    if not duration or duration <= 0:
        raise RuntimeError("falha ao medir duração da narração do trecho")

    has_captions = burn_captions and os.path.getsize(srt_path) > 0
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
    subtitles_filter = (
        "subtitles=%s:force_style='FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=70'" % srt_escaped
    ) if has_captions else None

    if image_path:
        # imagem estatica (gerada por IA ou do banco de fundos) com zoom lento
        # continuo (efeito Ken Burns) pela duracao do audio desse trecho.
        total_frames = max(1, int(round(duration * fps)))
        vf_parts = [
            "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080",
            "zoompan=z='min(zoom+0.0007,1.3)':d=%d:s=1920x1080:fps=%d" % (total_frames, fps),
        ]
        src_input = ["-loop", "1", "-i", image_path]
    else:
        # sem nenhuma imagem disponivel - fallback de cor solida pra manter o
        # pipeline testavel ponta a ponta.
        vf_parts = ["scale=1920:1080:force_original_aspect_ratio=increase", "crop=1920:1080", "setsar=1"]
        src_input = ["-f", "lavfi", "-i", "color=c=0x14141f:s=1920x1080:r=%d" % fps]
    if subtitles_filter:
        vf_parts.append(subtitles_filter)
    vf = ",".join(vf_parts)

    cmd = ["ffmpeg", "-y"] + src_input + [
        "-i", audio_path,
        "-vf", vf,
        "-map", "0:v", "-map", "1:a",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("ffmpeg failed: %s" % res.stderr[-1500:])


def _fit_title_card_text(titulo):
    titulo = titulo.upper()
    for fontsize, max_chars in ((72, 22), (58, 28), (46, 34)):
        lines = _wrap_text(titulo, max_chars)
        if len(lines) <= 3:
            return fontsize, lines
    fontsize, max_chars = 46, 34
    return fontsize, _wrap_text(titulo, max_chars)[:3]


def _render_title_card(numero, titulo, image_path, out_path, duration=3.0, fps=30):
    # Placa de transicao entre historias: numero grande + titulo da historia,
    # sobre a mesma imagem daquele trecho (ou cor solida de fallback).
    fontsize, lines = _fit_title_card_text(titulo)
    safe_lines = [l.replace("\\", "").replace("'", "").replace(":", "\\:") for l in lines]
    safe_titulo = "\n".join(safe_lines)
    numero_text = "HISTORIA %d" % numero

    drawtext_num = (
        "drawtext=fontfile=%s:text='%s':fontsize=110:fontcolor=0xFFD400:"
        "borderw=8:bordercolor=black:box=1:boxcolor=black@0.5:boxborderw=24:"
        "x=(w-text_w)/2:y=(h/2)-190"
        % (FONT_PATH, numero_text)
    )
    drawtext_titulo = (
        "drawtext=fontfile=%s:text='%s':fontsize=%d:fontcolor=white:"
        "borderw=6:bordercolor=black:line_spacing=12:box=1:boxcolor=black@0.5:boxborderw=24:"
        "x=(w-text_w)/2:y=(h/2)+10"
        % (FONT_PATH, safe_titulo, fontsize)
    )
    vf = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080," + drawtext_num + "," + drawtext_titulo

    inputs = []
    if image_path:
        inputs += ["-loop", "1", "-t", str(duration), "-i", image_path]
    else:
        inputs += ["-f", "lavfi", "-t", str(duration), "-i", "color=c=0x14141f:s=1920x1080:r=%d" % fps]
    inputs += ["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=44100:cl=stereo"]

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-vf", vf,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", out_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("ffmpeg failed (title card): %s" % res.stderr[-1500:])


@app.route("/longform", methods=["POST"])
def longform():
    if request.headers.get("x-token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    voice = str(data.get("voice") or DEFAULT_VOICE)
    segmentos = data.get("segmentos")

    work = tempfile.mkdtemp()
    try:
        out = os.path.join(work, "longform.mp4")

        if isinstance(segmentos, list) and segmentos:
            # 1 imagem por trecho narrado (gerada por IA a partir do prompt_imagem
            # de cada historia) em vez de 1 imagem estatica pro video inteiro.
            # Cada historia e precedida por uma placa de transicao (numero + titulo),
            # sem legenda queimada (so a placa marca a mudanca de historia).
            seg_paths = []
            numero = 0
            for i, seg in enumerate(segmentos):
                seg_texto = str((seg or {}).get("texto") or "").strip()
                if len(seg_texto) < 5:
                    continue
                img_url = (seg or {}).get("imageUrl") or (seg or {}).get("image_url")
                if img_url and isinstance(img_url, str) and img_url.startswith("http"):
                    image_path = os.path.join(work, "seg%d_img" % i)
                    fetch(img_url, image_path)
                else:
                    image_path = _pick_background_image()

                titulo_historia = str((seg or {}).get("titulo") or (seg or {}).get("titulo_historia") or "").strip()
                if titulo_historia:
                    # numero so avanca quando comeca uma historia nova (titulo presente) -
                    # cada historia agora pode vir com varios trechos/imagens (partes), entao
                    # contar toda iteracao do loop numerava a placa pelo indice do TRECHO, nao
                    # da historia (ex: "HISTORIA 13" numa lista de 15 trechos/5 historias).
                    numero += 1
                    card_out = os.path.join(work, "seg%d_card.mp4" % i)
                    _render_title_card(numero, titulo_historia, image_path, card_out)
                    seg_paths.append(card_out)

                seg_out = os.path.join(work, "seg%d.mp4" % i)
                _render_segment(seg_texto, voice, image_path, seg_out, burn_captions=False)
                seg_paths.append(seg_out)
            if not seg_paths:
                return jsonify({"error": "nenhum segmento valido recebido"}), 400
            if len(seg_paths) == 1:
                shutil.copy(seg_paths[0], out)
            else:
                _concat_mp4s(seg_paths, out, width=1920, height=1080, timeout=1800)
        else:
            # modo legado: 1 texto corrido so, 1 imagem estatica pro video inteiro.
            texto = str(data.get("texto") or "").strip()
            if len(texto) < 20:
                return jsonify({"error": "texto muito curto ou ausente"}), 400
            bg_path = _pick_background_image()
            _render_segment(texto, voice, bg_path, out)

        return send_file(out, mimetype="video/mp4", as_attachment=True, download_name="longform.mp4")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _fit_thumbnail_text(text):
    # Texto grande e chamativo pra capa 1280x720 - fonte bem maior que a do
    # watermark, tudo em maiusculas pra maximo impacto visual.
    text = text.upper()
    for fontsize, max_chars in ((92, 15), (72, 20), (56, 26)):
        lines = _wrap_text(text, max_chars)
        if len(lines) <= 4:
            return fontsize, lines
    fontsize, max_chars = 56, 26
    lines = _wrap_text(text, max_chars)[:4]
    return fontsize, lines


@app.route("/thumbnail", methods=["POST"])
def thumbnail():
    # Gera uma capa 1280x720 chamativa: imagem de fundo (de uma das cenas do
    # video) + texto grande em caixa alta com contorno preto, pra chamar
    # atencao/curiosidade na lista de recomendados do YouTube.
    if request.headers.get("x-token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    image_url = data.get("imageUrl") or data.get("image_url")
    text = str(data.get("texto") or "").strip()
    if not text and not image_url:
        return jsonify({"error": "precisa de imageUrl e/ou texto"}), 400

    drawtext = None
    if text:
        fontsize, lines = _fit_thumbnail_text(text)
        safe_lines = [l.replace("\\", "").replace("'", "").replace(":", "\\:") for l in lines]
        safe_text = "\n".join(safe_lines)
        drawtext = (
            "drawtext=fontfile=%s:text='%s':fontsize=%d:fontcolor=0xFFD400:"
            "borderw=6:bordercolor=black:line_spacing=14:"
            "box=1:boxcolor=black@0.35:boxborderw=24:x=(w-text_w)/2:y=h-th-70"
            % (FONT_PATH, safe_text, fontsize)
        )

    work = tempfile.mkdtemp()
    try:
        src = None
        if image_url and isinstance(image_url, str) and image_url.startswith("http"):
            src = os.path.join(work, "in_img")
            fetch(image_url, src)
        out = os.path.join(work, "thumbnail.jpg")

        if src:
            vf = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720"
            if drawtext:
                vf += "," + drawtext
            cmd = ["ffmpeg", "-y", "-i", src, "-vf", vf, "-frames:v", "1", "-q:v", "2", out]
        else:
            # sem imagem nenhuma - fallback de cor solida (só acontece se nao veio
            # imageUrl nenhuma; sempre tem texto nesse caso, ja validado acima)
            cmd = [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x14141f:s=1280x720",
                "-vf", drawtext,
                "-frames:v", "1", "-q:v", "2", out,
            ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0 or not os.path.exists(out):
            return jsonify({"error": "ffmpeg failed", "stderr": res.stderr[-1500:]}), 500
        return send_file(out, mimetype="image/jpeg", as_attachment=True, download_name="thumbnail.jpg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _fit_banner_text(text):
    # Texto grande centralizado pra capa de canal 2560x1440 - posicionado na
    # faixa central "safe area" que fica visivel em qualquer dispositivo.
    text = text.upper()
    for fontsize, max_chars in ((140, 18), (110, 24), (86, 30)):
        lines = _wrap_text(text, max_chars)
        if len(lines) <= 2:
            return fontsize, lines
    fontsize, max_chars = 86, 30
    return fontsize, _wrap_text(text, max_chars)[:2]


@app.route("/banner", methods=["POST"])
def banner():
    # Gera uma capa de canal 2560x1440 (padrao YouTube) a partir de uma imagem
    # de fundo + texto grande centralizado na "safe area" (faixa central que
    # aparece em qualquer dispositivo: desktop, mobile, TV).
    if request.headers.get("x-token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    image_url = data.get("imageUrl") or data.get("image_url")
    text = str(data.get("texto") or "").strip()
    subtext = str(data.get("subtexto") or "").strip()
    if not text and not image_url:
        return jsonify({"error": "precisa de imageUrl e/ou texto"}), 400

    drawtext_parts = []
    if text:
        fontsize, lines = _fit_banner_text(text)
        safe_lines = [l.replace("\\", "").replace("'", "").replace(":", "\\:") for l in lines]
        safe_text = "\n".join(safe_lines)
        y_expr = "(h-text_h)/2-40" if subtext else "(h-text_h)/2"
        drawtext_parts.append(
            "drawtext=fontfile=%s:text='%s':fontsize=%d:fontcolor=white:"
            "borderw=8:bordercolor=black:line_spacing=10:x=(w-text_w)/2:y=%s"
            % (FONT_PATH, safe_text, fontsize, y_expr)
        )
    if subtext:
        sub_safe = subtext.replace("\\", "").replace("'", "").replace(":", "\\:")
        drawtext_parts.append(
            "drawtext=fontfile=%s:text='%s':fontsize=44:fontcolor=white:"
            "borderw=4:bordercolor=black:x=(w-text_w)/2:y=(h/2)+90"
            % (FONT_PATH, sub_safe)
        )

    work = tempfile.mkdtemp()
    try:
        src = None
        if image_url and isinstance(image_url, str) and image_url.startswith("http"):
            src = os.path.join(work, "in_img")
            fetch(image_url, src)
        out = os.path.join(work, "banner.jpg")

        if src:
            vf = "scale=2560:1440:force_original_aspect_ratio=increase,crop=2560:1440"
            if drawtext_parts:
                vf += "," + ",".join(drawtext_parts)
            cmd = ["ffmpeg", "-y", "-i", src, "-vf", vf, "-frames:v", "1", "-q:v", "2", out]
        else:
            vf = ",".join(drawtext_parts) if drawtext_parts else "null"
            cmd = [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x14141f:s=2560x1440",
                "-vf", vf, "-frames:v", "1", "-q:v", "2", out,
            ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0 or not os.path.exists(out):
            return jsonify({"error": "ffmpeg failed", "stderr": res.stderr[-1500:]}), 500
        return send_file(out, mimetype="image/jpeg", as_attachment=True, download_name="banner.jpg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.route("/debug/frame", methods=["POST"])
def debug_frame():
    # utilitario de debug: extrai 1 frame de um video (URL ou binario cru) em
    # JPEG, pra inspecionar visualmente texto/legenda sem baixar o video inteiro.
    if request.headers.get("x-token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    video_url = data.get("video")
    at = float(data.get("at") or request.args.get("at") or 1.0)

    work = tempfile.mkdtemp()
    try:
        src = os.path.join(work, "in.mp4")
        if video_url and isinstance(video_url, str) and video_url.startswith("http"):
            fetch(video_url, src)
        elif request.data:
            with open(src, "wb") as f:
                f.write(request.data)
        else:
            return jsonify({"error": "need a video URL (JSON {video: url}) or raw video body"}), 400
        out = os.path.join(work, "frame.jpg")
        cmd = ["ffmpeg", "-y", "-ss", str(at), "-i", src, "-vframes", "1", "-q:v", "2", out]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0 or not os.path.exists(out):
            return jsonify({"error": "ffmpeg failed", "stderr": res.stderr[-1500:]}), 500
        return send_file(out, mimetype="image/jpeg", as_attachment=True, download_name="frame.jpg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
