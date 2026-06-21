from flask import Flask, request, send_file, render_template_string
from PIL import Image, ImageOps
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from openai import OpenAI
import os
import re
import base64
import json

app = Flask(__name__)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

# OpenAI key komt uit Render Environment Variable: OPENAI_API_KEY
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

EPA_TERMS = [
    "CV ketel",
    "CV typeplaatje",
    "Warmtepomp",
    "Warmtepomp typeplaatje",
    "Hybride warmtepomp",
    "Boiler",
    "Boiler typeplaatje",
    "Buffervat",
    "Expansievat",
    "Meterkast",
    "Elektriciteitsmeter",
    "Gasmeter",
    "Watermeter",
    "WTW unit",
    "WTW typeplaatje",
    "Mechanische ventilatie",
    "Ventilatiebox typeplaatje",
    "Ventilatierooster",
    "Afzuigventiel",
    "Toevoerventiel",
    "Thermostaat",
    "Radiator",
    "Vloerverwarming verdeler",
    "Zonnepanelen",
    "Omvormer",
    "Omvormer typeplaatje",
    "Voorgevel",
    "Achtergevel",
    "Linkergevel",
    "Rechtergevel",
    "Dak",
    "Plat dak",
    "Hellend dak",
    "Dakkapel",
    "Dakraam",
    "Raam",
    "Kozijn",
    "Glas detail",
    "Voordeur",
    "Achterdeur",
    "Deur",
    "Schuifpui",
    "Garagedeur",
    "Kruipruimte",
    "Vloerisolatie",
    "Dakisolatie",
    "Gevelisolatie",
    "Spouwmuur",
    "Kelder",
    "Zolder",
    "Onbekend"
]

# Deze categorieën krijgen automatisch namen als 1A.jpg, 1B.jpg, 2A.jpg enz.
OPENING_TERMS = {"Raam", "Kozijn", "Glas detail", "Voordeur", "Achterdeur", "Deur", "Schuifpui", "Dakraam"}

HTML = """
<!doctype html>
<html>
<head>
  <title>Energievakman Foto Verwerker</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="font-family: Arial; max-width: 760px; margin: 40px auto; padding: 0 16px;">
  <h1>Foto's verkleinen en hernoemen</h1>
  <p>Upload foto's. De app comprimeert ze, laat AI de inhoud herkennen en maakt een ZIP met nette bestandsnamen.</p>

  <form action="/process-photos" method="post" enctype="multipart/form-data">
    <input type="file" name="photos" multiple accept="image/*" required>
    <br><br>

    <label>
      <input type="checkbox" name="use_ai" value="1" checked>
      AI-hernoemen gebruiken
    </label>
    <br><br>

    <label>
      Start verdieping voor ramen/deuren:
      <input type="number" name="default_floor" value="1" min="1" max="9" style="width: 60px;">
    </label>
    <p style="color:#666; font-size: 14px;">
      Ramen/deuren worden automatisch 1A.jpg, 1B.jpg enz. Als AI een code op de foto ziet, zoals 2C, dan gebruikt hij die code.
    </p>

    <button type="submit" style="padding: 10px 16px;">Verwerk foto's</button>
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


def number_to_letters(number):
    """1 -> A, 2 -> B, 26 -> Z, 27 -> AA."""
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def next_opening_code(floor, counters):
    counters[floor] = counters.get(floor, 0) + 1
    return f"{floor}{number_to_letters(counters[floor])}"


def make_unique_filename(name, used_names):
    clean = safe_filename(name)
    if not clean.lower().endswith(".jpg"):
        clean = f"{clean}.jpg"

    if clean not in used_names:
        used_names.add(clean)
        return clean

    base = clean[:-4]
    counter = 2
    while True:
        candidate = f"{base} {counter}.jpg"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        counter += 1


def compress_image(file_storage, max_width=1600, quality=75):
    image = Image.open(file_storage)

    # Corrigeert iPhone/iPad rotatie
    image = ImageOps.exif_transpose(image)

    # Zet alles om naar RGB voor JPG-output
    if image.mode in ("RGBA", "P"):
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


def detect_photo_info(image_bytes):
    """Laat OpenAI Vision categorie, eventuele zichtbare code en verdieping bepalen."""
    if not os.environ.get("OPENAI_API_KEY"):
        return {"category": "Onbekend", "visible_code": "", "floor": None}

    image_bytes.seek(0)
    b64 = base64.b64encode(image_bytes.read()).decode("utf-8")
    image_bytes.seek(0)

    terms_text = "\n".join(f"- {term}" for term in EPA_TERMS)

    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"""
Je bent een EPA-W foto-assistent.

Bekijk de foto en geef alleen geldige JSON terug.

Kies exact één category uit deze lijst:
{terms_text}

Extra regels voor ramen/deuren/kozijnen/glas/schuifpuien/dakramen:
- Als er een code zichtbaar op de foto staat zoals 1A, 1B, 1C, 2A, 2B enz., zet die in visible_code.
- Als je aan de foto duidelijk ziet dat het om een verdieping gaat, zet floor op 1, 2, 3 enz.
- Begane grond/eerste bouwlaag = floor 1.
- Volgende verdieping = floor 2.
- Als je het niet zeker weet, zet floor op null.

JSON-formaat:
{{
  "category": "Raam",
  "visible_code": "",
  "floor": null
}}

Geen uitleg. Geen markdown. Alleen JSON.
"""
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/jpeg;base64,{b64}"
                        }
                    ]
                }
            ]
        )

        raw = response.output_text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)

        category = str(data.get("category", "Onbekend")).strip()
        visible_code = str(data.get("visible_code", "")).upper().strip().replace(" ", "")
        floor = data.get("floor", None)

        # Alleen vaste termen toestaan
        valid_category = "Onbekend"
        for term in EPA_TERMS:
            if category.lower() == term.lower():
                valid_category = term
                break

        # Alleen codes zoals 1A, 1B, 2C, 3AA toestaan
        if not re.fullmatch(r"[1-9][A-Z]{1,2}", visible_code):
            visible_code = ""

        try:
            floor = int(floor) if floor is not None else None
        except Exception:
            floor = None

        if floor is not None and not (1 <= floor <= 9):
            floor = None

        return {"category": valid_category, "visible_code": visible_code, "floor": floor}

    except Exception as e:
        print(f"AI fout: {e}")
        return {"category": "Onbekend", "visible_code": "", "floor": None}


def filename_from_ai_info(info, default_floor, opening_counters):
    category = info.get("category", "Onbekend")
    visible_code = info.get("visible_code", "")
    floor = info.get("floor") or default_floor

    if category in OPENING_TERMS:
        if visible_code:
            return f"{visible_code}.jpg"
        code = next_opening_code(floor, opening_counters)
        return f"{code}.jpg"

    return f"{category}.jpg"


@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML)


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}


@app.route("/process-photos", methods=["POST"])
def process_photos():
    files = request.files.getlist("photos")
    use_ai = request.form.get("use_ai") == "1"

    try:
        default_floor = int(request.form.get("default_floor", "1"))
    except Exception:
        default_floor = 1

    if default_floor < 1 or default_floor > 9:
        default_floor = 1

    if not files:
        return "Geen foto's ontvangen", 400

    zip_buffer = BytesIO()
    used_names = set()
    opening_counters = {}

    with ZipFile(zip_buffer, "w", ZIP_DEFLATED) as zip_file:
        for index, file in enumerate(files, start=1):
            if file.filename == "" or not allowed_file(file.filename):
                continue

            original_name = safe_filename(file.filename)
            base_name = os.path.splitext(original_name)[0]

            compressed = compress_image(file)

            if use_ai:
                info = detect_photo_info(compressed)
                suggested_filename = filename_from_ai_info(info, default_floor, opening_counters)
                new_filename = make_unique_filename(suggested_filename, used_names)
            else:
                new_filename = make_unique_filename(f"gecomprimeerd_{index:03d}_{base_name}.jpg", used_names)

            compressed.seek(0)
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
