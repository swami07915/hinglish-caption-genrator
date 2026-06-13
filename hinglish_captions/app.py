import json
import os
import uuid

from flask import Flask, render_template, request, send_file, redirect, url_for
from werkzeug.utils import secure_filename

from caption_engine import transcribe_only, render

app = Flask(__name__)
app.jinja_env.globals["enumerate"] = enumerate   # expose Python's enumerate to templates
app.config["UPLOAD_FOLDER"] = "uploads"
app.config["OUTPUT_FOLDER"] = "outputs"
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

ALLOWED_EXT = {"mp4", "mov", "avi", "mkv"}


def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def _job_path(uid):
    return os.path.join(app.config["UPLOAD_FOLDER"], f"job_{uid}.json")


# ── Step 1: Upload + Transcribe ───────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/process", methods=["POST"])
def process_video():
    video_file      = request.files.get("video")
    api_key         = request.form.get("api_key", "").strip()
    style           = request.form.get("style", "yellow")
    language        = request.form.get("language", "en")
    words_per_group = int(request.form.get("words_per_group", 3))

    if not video_file or video_file.filename == "":
        return render_template("index.html", error="Please select a video file.")
    if not _allowed(video_file.filename):
        return render_template("index.html", error="Unsupported format. Use .mp4 or .mov.")
    if not api_key:
        return render_template("index.html", error="AssemblyAI API key is required.")

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    uid      = uuid.uuid4().hex[:10]
    filename = secure_filename(video_file.filename)
    stem, ext = os.path.splitext(filename)
    save_name  = f"{stem}_{uid}{ext}"
    video_path = os.path.join(app.config["UPLOAD_FOLDER"], save_name)
    video_file.save(video_path)

    try:
        words = transcribe_only(video_path, api_key, language=language)
    except Exception as exc:
        return render_template("index.html", error=str(exc))

    # Persist job so the review + render steps can access it
    job = {
        "video_path":     video_path,
        "style":          style,
        "language":       language,
        "words_per_group": words_per_group,
        "words":          words,
    }
    with open(_job_path(uid), "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False)

    return redirect(url_for("review", uid=uid))


# ── Step 2: Review & edit transcript ─────────────────────────────────────────

@app.route("/review/<uid>")
def review(uid):
    path = _job_path(uid)
    if not os.path.exists(path):
        return redirect(url_for("index"))
    with open(path, encoding="utf-8") as f:
        job = json.load(f)
    return render_template("review.html", uid=uid, job=job)


# ── Step 3: Render with (possibly edited) words ───────────────────────────────

@app.route("/render/<uid>", methods=["POST"])
def render_video(uid):
    path = _job_path(uid)
    if not os.path.exists(path):
        return redirect(url_for("index"))

    with open(path, encoding="utf-8") as f:
        job = json.load(f)

    # Collect edited words from form
    corrected = []
    for i, w in enumerate(job["words"]):
        text = request.form.get(f"w{i}", w["text"]).strip()
        if text:                              # skip blanks (deleted words)
            corrected.append({"text": text, "start": w["start"], "end": w["end"]})

    try:
        output_path = render(
            job["video_path"],
            corrected,
            style=job["style"],
            words_per_group=job["words_per_group"],
            output_dir=app.config["OUTPUT_FOLDER"],
        )
    except Exception as exc:
        return render_template("review.html", uid=uid, job=job, error=str(exc))

    # Clean up job file
    try:
        os.unlink(path)
    except OSError:
        pass

    return render_template(
        "result.html",
        output_file=os.path.basename(output_path),
        words=corrected,
        style=job["style"],
        word_count=len(corrected),
    )


# ── File serving ──────────────────────────────────────────────────────────────

@app.route("/output/<path:filename>")
def serve_output(filename):
    return send_file(
        os.path.join(app.config["OUTPUT_FOLDER"], filename), mimetype="video/mp4"
    )


@app.route("/download/<path:filename>")
def download(filename):
    return send_file(
        os.path.join(app.config["OUTPUT_FOLDER"], filename),
        as_attachment=True,
        download_name=filename,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
