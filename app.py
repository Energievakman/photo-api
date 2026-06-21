from flask import Flask, request, send_file, render_template_string
from PIL import Image, ImageOps
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import os
import re

app = Flask(__name__)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

HTML = """
<!doctype html>
<html>
<head>
  <title>Energievakman Foto Verwerker</title>
  <style>
    body { font-family: Arial; max-width: 700px; margin: 40px auto; }
    button { padding: 10px 16px; cursor: pointer; }
  </style>
</head>
<body>
  <h1>Foto's verkleinen</h1>
  <p>Upload foto's. De app comprimeert ze en maakt een ZIP-bestand.</p>

  <form action="/process-photos" method="post" enctype="multipart/form-data">
    <input type="file" name="photos" multiple accept="image/*">
    <br><br>
    <button type="submit">Verwerk foto's</button>
  </form>
</body>
</html>
"""


def allowed_file(filename):
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[-1].lower()
    return ext in ALLOWED_EXTENSIONS


def safe_filename(name):
    name = re.sub(r"[^a-zA-Z0-9_\- .]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip() or "foto"


def compress_image(file_storage, max_width=1600, quality=75):
    file_storage.stream.seek(0)
    image = Image.open(file_storage.stream)

    # Corrigeert iPhone-rotatie
    image = ImageOps.exif_transpose(image)

    # Alles naar RGB voor JPG-output
    if image.mode != "RGB":
        image = image.convert("RGB")

    # Verkleinen als foto breder is dan max_width
    if image.width > max_width:
        ratio = max_width / image.width
        new_height = int(image.height * ratio)
        image = image.resize((max_width, new_height))

    output = BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    output.seek(0)
    return output


@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML)


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}


@app.route("/process-photos", methods=["POST"])
def process_photos():
    files = request.files.getlist("photos")

    if not files:
        return "Geen foto's ontvangen", 400

    zip_buffer = BytesIO()

    with ZipFile(zip_buffer, "w", ZIP_DEFLATED) as zip_file:
        for index, file in enumerate(files, start=1):
            if file.filename == "" or not allowed_file(file.filename):
                continue

            original_name = safe_filename(file.filename)
            base_name = os.path.splitext(original_name)[0]

            compressed = compress_image(file, max_width=1600, quality=75)

            # Simpele veilige naam: originele naam behouden met prefix
            new_filename = f"gecomprimeerd_{index:03d}_{base_name}.jpg"

            zip_file.writestr(new_filename, compressed.read())

    zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name="verwerkte_fotos.zip"
    )


if __name__ == "__main__":
    app.run(debug=True)
