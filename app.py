from flask import Flask, request, send_file, render_template_string, abort
from PIL import Image, ImageOps
from io import BytesIO
from zipfile import ZipFile, ZIP_STORED, BadZipFile
import os
import re
import html
import uuid
import tempfile
import requests
from urllib.parse import urlparse

app = Flask(__name__)

ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_UPLOAD_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | {"zip"}

SOFTR_API_KEY = os.environ.get("SOFTR_API_KEY")
SOFTR_DATABASE_ID = os.environ.get("SOFTR_DATABASE_ID")
SOFTR_TABLE_ID = os.environ.get("SOFTR_TABLE_ID")
SOFTR_FILE_FIELD = os.environ.get("SOFTR_FILE_FIELD")

OUTPUT_ZIP_NAME = "dossier_gecomprimeerd.zip"
TEMP_DIR = tempfile.gettempdir()

# Exacte mapnamen uit jouw projectdossier format
DOSSIER_CHECKS = [
    {
        "label": "BAG oriëntatie",
        "folder": "BAG orientatie",
        "extensions": None,
        "min_files": 1,
    },
    {
        "label": "Bouwtekeningen / plattegronden",
        "folder": "Bouwtekeningen of plattegronden",
        "extensions": None,
        "min_files": 1,
    },
    {
        "label": "Beschikbaar gestelde informatie opdrachtgever",
        "folder": "Beschikbaar gestelde informatie opdrachtgever",
        "extensions": None,
        "min_files": 1,
    },
    {
        "label": "Energielabelrapport",
        "folder": "Energielabelrapport",
        "extensions": ["pdf"],
        "min_files": 1,
    },
    {
        "label": "Foto's, facturen, bewijslast",
        "folder": "Foto's, facturen, bewijslast",
        "extensions": None,
        "min_files": 1,
    },
    {
        "label": "Opdrachtbevestiging",
        "folder": "Opdrachtbevestiging",
        "extensions": None,
        "min_files": 1,
    },
    {
        "label": "Rapportage",
        "folder": "Rapportage",
        "extensions": None,
        "min_files": 1,
    },
    {
        "label": "Uitvoerbestand Vabi of Uniec",
        "folder": "Uitvoerbestand Vabi of Uniec",
        "extensions": None,
        "min_files": 1,
    },
]

HTML = """
<!doctype html>
<html>
<head>
  <title>Energievakman Foto Verwerker</title>
  <style>
    body { font-family: Arial; max-width: 780px; margin: 40px auto; }
    button { padding: 10px 16px; cursor: pointer; }
    .hint { color: #666; font-size: 14px; line-height: 1.4; }
  </style>
</head>
<body>
  <h1>Foto's / ZIP verkleinen</h1>
  <p>Upload losse foto's of één ZIP-bestand. Afbeeldingen worden verkleind; andere bestanden in de ZIP blijven ongewijzigd.</p>

  <form action="/process-photos" method="post" enctype="multipart/form-data">
    <input type="file" name="photos" multiple accept="image/*,.zip">
    <br><br>

    <label>
      Max breedte:
      <select name="max_width">
        <option value="1200" selected>1200 px - snel / klein</option>
        <option value="1600">1600 px - beter detail</option>
        <option value="2000">2000 px - hoog detail</option>
      </select>
    </label>

    <br><br>

    <label>
      Kwaliteit:
      <select name="quality">
        <option value="65" selected>65 - klein bestand</option>
        <option value="75">75 - normaal</option>
        <option value="85">85 - hoog</option>
      </select>
    </label>

    <p class="hint">
      Na verwerking krijg je een controlescherm met JA/NEE, aantallen per map en een downloadknop voor <b>dossier_gecomprimeerd.zip</b>.
    </p>

    <button type="submit">Verwerk bestanden</button>
  </form>
</body>
</html>
"""

RESULT_HTML = """
<!doctype html>
<html>
<head>
  <title>Dossier verwerkt</title>
  <style>
    body { font-family: Arial; max-width: 860px; margin: 40px auto; }
    .btn { display: inline-block; padding: 12px 18px; background: #1a73e8; color: white; text-decoration: none; border-radius: 6px; font-weight: 700; }
    table { border-collapse: collapse; width: 100%; margin-top: 22px; }
    th { text-align: left; background: #f3f3f3; }
    th, td { border-bottom: 1px solid #ddd; padding: 10px 8px; vertical-align: top; }
    .ja { color: #0a7a32; font-weight: 800; }
    .nee { color: #c62828; font-weight: 800; }
    .details { color: #555; font-size: 13px; }
    .result { margin-top: 24px; font-size: 18px; }
    .muted { color: #666; }
  </style>
</head>
<body>
  <h1>Dossier verwerkt</h1>
  <p>De ZIP is aangemaakt. Hieronder staat de controle van het verwerkte dossier.</p>

  <a class="btn" href="/download/{{download_id}}">Download dossier_gecomprimeerd.zip</a>

  {{table_html|safe}}

  <p><a href="/">Nieuw dossier verwerken</a></p>
</body>
</html>
"""


def extension(filename):
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower().split("?")[0]


def is_image(filename):
    return extension(filename) in ALLOWED_IMAGE_EXTENSIONS


def is_zip(filename):
    return extension(filename) == "zip"


def allowed_upload(filename):
    return extension(filename) in ALLOWED_UPLOAD_EXTENSIONS


def should_skip_zip_entry(path):
    path = path.replace("\\", "/")
    parts = path.split("/")
    filename = parts[-1] if parts else ""
    return "__MACOSX" in parts or filename in {".DS_Store", "Thumbs.db"} or filename.startswith("._") or filename == ""


def safe_filename(name):
    # Houd mapstructuur intact, maar maak vreemde tekens veiliger.
    name = name.replace("\\", "/")
    parts = []
    for part in name.split("/"):
        part = re.sub(r"[^a-zA-Z0-9_\- .,']", "", part)
        part = re.sub(r"\s+", " ", part).strip()
        if part and part not in {".", ".."}:
            parts.append(part)
    return "/".join(parts) or "bestand"


def jpg_output_path(path):
    directory = os.path.dirname(path)
    base = os.path.splitext(os.path.basename(path))[0]
    new_name = f"{base}.jpg"
    return f"{directory}/{new_name}" if directory else new_name


def unique_zip_name(name, used_names):
    base, ext = os.path.splitext(name)
    candidate = name
    counter = 2
    while candidate.lower() in used_names:
        candidate = f"{base} {counter}{ext}"
        counter += 1
    used_names.add(candidate.lower())
    return candidate


def compress_image_from_filelike(file_obj, max_width=1200, quality=65):
    image = Image.open(file_obj)
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        image = image.convert("RGB")
    if image.width > max_width:
        ratio = max_width / image.width
        new_height = int(image.height * ratio)
        image = image.resize((max_width, new_height))
    output = BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    output.seek(0)
    return output


def compress_upload_image(file_storage, max_width=1200, quality=65):
    file_storage.stream.seek(0)
    return compress_image_from_filelike(file_storage.stream, max_width=max_width, quality=quality)


def path_parts(path):
    return [p for p in path.replace("\\", "/").split("/") if p]


def is_file_in_exact_folder(path, folder_name):
    # Matcht exact op een mapdeel in het volledige ZIP-pad.
    # Dus Projectdossier format/BAG orientatie/test.pdf werkt.
    parts = path_parts(path)
    return folder_name in parts[:-1]


def count_types(paths):
    pdfs = sum(1 for p in paths if extension(p) == "pdf")
    images = sum(1 for p in paths if is_image(p))
    others = max(0, len(paths) - pdfs - images)
    return pdfs, images, others


def get_check_results(zip_paths):
    results = []
    all_ok = True

    for check in DOSSIER_CHECKS:
        folder = check["folder"]
        allowed_extensions = check["extensions"]
        min_files = check["min_files"]

        all_in_folder = [p for p in zip_paths if is_file_in_exact_folder(p, folder)]

        if allowed_extensions is None:
            matching_files = all_in_folder
        else:
            matching_files = [p for p in all_in_folder if extension(p) in allowed_extensions]

        ok = len(matching_files) >= min_files
        all_ok = all_ok and ok

        pdfs, images, others = count_types(all_in_folder)

        results.append({
            "label": check["label"],
            "folder": folder,
            "ok": ok,
            "all_files": all_in_folder,
            "matching_files": matching_files,
            "total": len(all_in_folder),
            "pdfs": pdfs,
            "images": images,
            "others": others,
            "required": "alle bestanden" if allowed_extensions is None else ", ".join(allowed_extensions).upper(),
        })

    return results, all_ok


def build_check_report_text(zip_paths):
    results, all_ok = get_check_results(zip_paths)
    lines = ["DOSSIERCONTROLE", "================", ""]

    for result in results:
        answer = "JA" if result["ok"] else "NEE"
        mark = "✓" if result["ok"] else "✗"
        lines.append(f"{mark} {result['label']} - {answer}")
        lines.append(f"Mapnaam exact: {result['folder']}")
        lines.append(f"Aantal bestanden: {result['total']} | PDF: {result['pdfs']} | Afbeeldingen: {result['images']} | Overig: {result['others']}")

        if result["all_files"]:
            for item in result["all_files"][:10]:
                lines.append(f"  - {item}")
            if len(result["all_files"]) > 10:
                lines.append(f"  - ... en nog {len(result['all_files']) - 10} bestand(en)")
        else:
            lines.append("  - Geen bestanden gevonden in deze map")

        lines.append("")

    lines.append("EINDRESULTAAT")
    lines.append("============")
    lines.append("✓ COMPLEET" if all_ok else "✗ LET OP: dossier mogelijk niet compleet")
    lines.append("")
    return "\n".join(lines)


def build_check_table_html(zip_paths):
    results, all_ok = get_check_results(zip_paths)

    rows = []
    for result in results:
        answer = "JA" if result["ok"] else "NEE"
        cls = "ja" if result["ok"] else "nee"

        if result["all_files"]:
            items = "".join(f"<li>{html.escape(item)}</li>" for item in result["all_files"][:8])
            if len(result["all_files"]) > 8:
                items += f"<li>... en nog {len(result['all_files']) - 8} bestand(en)</li>"
            details = f"<ul>{items}</ul>"
        else:
            details = '<span class="muted">Geen bestanden gevonden in deze map.</span>'

        rows.append(f"""
        <tr>
          <td>{html.escape(result['label'])}<br><span class="muted">{html.escape(result['folder'])}</span></td>
          <td class="{cls}">{answer}</td>
          <td>{result['total']}</td>
          <td>{result['pdfs']}</td>
          <td>{result['images']}</td>
          <td>{result['others']}</td>
        </tr>
        <tr>
          <td colspan="6" class="details">{details}</td>
        </tr>
        """)

    eind_cls = "ja" if all_ok else "nee"
    eind_text = "COMPLEET" if all_ok else "LET OP: dossier mogelijk niet compleet"

    return f"""
    <table>
      <tr>
        <th>Controle</th>
        <th>Resultaat</th>
        <th>Totaal</th>
        <th>PDF</th>
        <th>Afbeeldingen</th>
        <th>Overig</th>
      </tr>
      {''.join(rows)}
    </table>
    <p class="result">Eindresultaat: <span class="{eind_cls}">{html.escape(eind_text)}</span></p>
    """


def build_check_report_html(zip_paths):
    table = build_check_table_html(zip_paths)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Dossiercontrole</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 32px; }}
table {{ border-collapse: collapse; width: 100%; max-width: 980px; }}
th {{ text-align: left; background: #f3f3f3; }}
th, td {{ border-bottom: 1px solid #ddd; padding: 10px 8px; vertical-align: top; }}
.ja {{ color: #0a7a32; font-weight: 800; }}
.nee {{ color: #c62828; font-weight: 800; }}
.details {{ color: #555; font-size: 13px; padding-top: 0; }}
.result {{ margin-top: 24px; font-size: 18px; }}
.muted {{ color: #666; font-size: 12px; }}
</style>
</head>
<body>
<h1>Dossiercontrole</h1>
{table}
</body>
</html>"""


def process_zip_bytes(zip_bytes, output_zip, used_names, written_paths, max_width=1200, quality=65):
    try:
        with ZipFile(zip_bytes, "r") as input_zip:
            for info in input_zip.infolist():
                raw_path = info.filename
                if should_skip_zip_entry(raw_path):
                    continue
                original_path = safe_filename(raw_path)
                if not original_path or info.is_dir():
                    continue

                data = input_zip.read(info.filename)

                if is_image(original_path):
                    try:
                        compressed = compress_image_from_filelike(BytesIO(data), max_width=max_width, quality=quality)
                        output_path = unique_zip_name(jpg_output_path(original_path), used_names)
                        output_zip.writestr(output_path, compressed.getvalue())
                        written_paths.append(output_path)
                    except Exception:
                        output_path = unique_zip_name(original_path, used_names)
                        output_zip.writestr(output_path, data)
                        written_paths.append(output_path)
                else:
                    output_path = unique_zip_name(original_path, used_names)
                    output_zip.writestr(output_path, data)
                    written_paths.append(output_path)
    except BadZipFile:
        output_zip.writestr("fout_zip.txt", "Dit bestand lijkt geen geldige ZIP te zijn.")
        written_paths.append("fout_zip.txt")


def get_original_name_from_url(url, fallback):
    parsed = urlparse(url)
    name = os.path.basename(parsed.path)
    name = safe_filename(name)
    if not name or "." not in name:
        return fallback
    return name


def download_url_to_bytes(url):
    headers = {"User-Agent": "EnergievakmanPhotoCompressor/1.0"}
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return BytesIO(response.content)


def extract_urls(value):
    urls = []
    if not value:
        return urls
    if isinstance(value, str):
        if value.startswith("http"):
            urls.append({"url": value, "name": ""})
        return urls
    if isinstance(value, list):
        for item in value:
            urls.extend(extract_urls(item))
        return urls
    if isinstance(value, dict):
        url = value.get("url") or value.get("fileUrl") or value.get("downloadUrl") or value.get("signedUrl")
        name = value.get("name") or value.get("filename") or value.get("fileName") or ""
        if isinstance(url, str) and url.startswith("http"):
            urls.append({"url": url, "name": name})
        else:
            for subvalue in value.values():
                if isinstance(subvalue, (dict, list, str)):
                    urls.extend(extract_urls(subvalue))
    return urls


def get_softr_record(record_id):
    if not SOFTR_API_KEY or not SOFTR_DATABASE_ID or not SOFTR_TABLE_ID:
        raise RuntimeError("Softr environment variables ontbreken")
    url = f"https://tables-api.softr.io/api/v1/databases/{SOFTR_DATABASE_ID}/tables/{SOFTR_TABLE_ID}/records/{record_id}"
    headers = {"Softr-Api-Key": SOFTR_API_KEY}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()["data"]


def urls_from_softr_record(record):
    fields = record.get("fields", {})
    if not SOFTR_FILE_FIELD:
        raise RuntimeError("SOFTR_FILE_FIELD ontbreekt in Render Environment")
    value = fields.get(SOFTR_FILE_FIELD)
    if value is None:
        available = ", ".join(fields.keys())
        raise RuntimeError(f"Veld '{SOFTR_FILE_FIELD}' niet gevonden. Beschikbare fields: {available}")
    return extract_urls(value)


def build_zip_from_sources(sources, max_width=1200, quality=65):
    zip_buffer = BytesIO()
    used_names = set()
    written_paths = []

    with ZipFile(zip_buffer, "w", ZIP_STORED) as zip_file:
        for index, source in enumerate(sources, start=1):
            try:
                name = source.get("name") or f"bestand_{index:03d}"
                original_name = safe_filename(name)

                if source.get("type") == "upload":
                    file = source["file"]
                    if is_zip(original_name):
                        file.stream.seek(0)
                        process_zip_bytes(BytesIO(file.stream.read()), zip_file, used_names, written_paths, max_width=max_width, quality=quality)
                    elif is_image(original_name):
                        base_name = os.path.splitext(os.path.basename(original_name))[0] or f"foto_{index:03d}"
                        compressed = compress_upload_image(file, max_width=max_width, quality=quality)
                        new_filename = unique_zip_name(f"gecomprimeerd_{index:03d}_{base_name}.jpg", used_names)
                        zip_file.writestr(new_filename, compressed.getvalue())
                        written_paths.append(new_filename)
                    else:
                        file.stream.seek(0)
                        new_filename = unique_zip_name(original_name, used_names)
                        zip_file.writestr(new_filename, file.stream.read())
                        written_paths.append(new_filename)
                else:
                    url = source["url"]
                    file_bytes = download_url_to_bytes(url)
                    url_name = safe_filename(source.get("name") or get_original_name_from_url(url, f"bestand_{index:03d}"))
                    if is_zip(url_name):
                        process_zip_bytes(file_bytes, zip_file, used_names, written_paths, max_width=max_width, quality=quality)
                    elif is_image(url_name) or not extension(url_name):
                        try:
                            base_name = os.path.splitext(os.path.basename(url_name))[0] or f"foto_{index:03d}"
                            compressed = compress_image_from_filelike(file_bytes, max_width=max_width, quality=quality)
                            new_filename = unique_zip_name(f"gecomprimeerd_{index:03d}_{base_name}.jpg", used_names)
                            zip_file.writestr(new_filename, compressed.getvalue())
                            written_paths.append(new_filename)
                        except Exception:
                            file_bytes.seek(0)
                            new_filename = unique_zip_name(url_name or f"bestand_{index:03d}", used_names)
                            zip_file.writestr(new_filename, file_bytes.read())
                            written_paths.append(new_filename)
                    else:
                        new_filename = unique_zip_name(url_name, used_names)
                        file_bytes.seek(0)
                        zip_file.writestr(new_filename, file_bytes.read())
                        written_paths.append(new_filename)
            except Exception as e:
                error_name = f"fout_{index:03d}.txt"
                zip_file.writestr(error_name, f"Kon bestand {index} niet verwerken: {str(e)}")
                written_paths.append(error_name)

        zip_file.writestr("dossier_controle.txt", build_check_report_text(written_paths))
        zip_file.writestr("dossier_controle.html", build_check_report_html(written_paths))

    zip_buffer.seek(0)
    return zip_buffer, written_paths


def save_temp_zip(zip_buffer):
    download_id = uuid.uuid4().hex
    path = os.path.join(TEMP_DIR, f"{download_id}.zip")
    with open(path, "wb") as f:
        f.write(zip_buffer.getvalue())
    return download_id


@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML)


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}


@app.route("/download/<download_id>", methods=["GET"])
def download(download_id):
    if not re.match(r"^[a-f0-9]{32}$", download_id):
        abort(404)
    path = os.path.join(TEMP_DIR, f"{download_id}.zip")
    if not os.path.exists(path):
        return "Download niet meer beschikbaar. Verwerk het dossier opnieuw.", 404
    return send_file(path, mimetype="application/zip", as_attachment=True, download_name=OUTPUT_ZIP_NAME)


@app.route("/process-photos", methods=["POST"])
def process_photos():
    files = request.files.getlist("photos")
    if not files:
        return "Geen bestanden ontvangen", 400

    try:
        max_width = int(request.form.get("max_width", 1200))
    except ValueError:
        max_width = 1200
    try:
        quality = int(request.form.get("quality", 65))
    except ValueError:
        quality = 65

    sources = []
    for file in files:
        if file.filename == "" or not allowed_upload(file.filename):
            continue
        sources.append({"type": "upload", "file": file, "name": file.filename})

    if not sources:
        return "Geen ondersteunde bestanden ontvangen. Upload afbeeldingen of ZIP-bestanden.", 400

    zip_buffer, written_paths = build_zip_from_sources(sources, max_width=max_width, quality=quality)
    download_id = save_temp_zip(zip_buffer)
    table_html = build_check_table_html(written_paths)
    return render_template_string(RESULT_HTML, download_id=download_id, table_html=table_html)


@app.route("/zip-from-softr/<record_id>", methods=["GET"])
def zip_from_softr(record_id):
    try:
        max_width = int(request.args.get("max_width", 1200))
    except ValueError:
        max_width = 1200
    try:
        quality = int(request.args.get("quality", 65))
    except ValueError:
        quality = 65

    try:
        record = get_softr_record(record_id)
        url_items = urls_from_softr_record(record)
        if not url_items:
            return "Geen bestand-URL's gevonden in dit Softr record", 404
        sources = [{"type": "url", "url": item["url"], "name": item.get("name") or ""} for item in url_items]
        zip_buffer, _ = build_zip_from_sources(sources, max_width=max_width, quality=quality)
        return send_file(zip_buffer, mimetype="application/zip", as_attachment=True, download_name=OUTPUT_ZIP_NAME)
    except Exception as e:
        return f"Fout bij Softr ZIP maken: {str(e)}", 500


if __name__ == "__main__":
    app.run(debug=True)
