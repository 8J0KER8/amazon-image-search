import os
import re
import statistics
import tempfile
import json
import ipaddress
import socket
from html import unescape
from html.parser import HTMLParser

from io import BytesIO
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps
from flask import Flask, jsonify, render_template, request


app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024


# =========================================================
# SERPAPI
# =========================================================

# المفتاح مش بيتكتب هنا.
# على Render هنضيف Environment Variable اسمه SERPAPI_KEY
SERPAPI_KEY = os.getenv(
    "SERPAPI_KEY",
    ""
).strip()


SERPAPI_IMAGE_URL = "https://serpapi.com/image"
SERPAPI_SEARCH_URL = "https://serpapi.com/search.json"


ALLOWED_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "webp"
}


# =========================================================
# IMAGE
# =========================================================

def allowed_file(filename):

    return (
        "."
        in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


def prepare_image(file_storage):

    image = Image.open(
        file_storage.stream
    )

    image = ImageOps.exif_transpose(
        image
    )

    if image.mode in ("RGBA", "LA"):

        background = Image.new(
            "RGB",
            image.size,
            "white"
        )

        alpha = image.getchannel("A")

        background.paste(
            image.convert("RGB"),
            mask=alpha
        )

        image = background

    else:

        image = image.convert(
            "RGB"
        )

    image.thumbnail(
        (1600, 1600),
        Image.Resampling.LANCZOS
    )

    quality = 90

    while True:

        buffer = BytesIO()

        image.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True
        )

        if buffer.tell() <= 480 * 1024:
            break

        if quality > 45:

            quality -= 10

        else:

            new_width = int(
                image.width * 0.85
            )

            new_height = int(
                image.height * 0.85
            )

            if (
                new_width < 300
                or new_height < 300
            ):
                break

            image = image.resize(
                (
                    new_width,
                    new_height
                ),
                Image.Resampling.LANCZOS
            )

            quality = 75

    temp_file = tempfile.NamedTemporaryFile(
        suffix=".jpg",
        delete=False
    )

    temp_file.write(
        buffer.getvalue()
    )

    temp_file.close()

    return temp_file.name


# =========================================================
# IMAGE UPLOAD
# =========================================================

def upload_image_to_serpapi(image_path):

    with open(
        image_path,
        "rb"
    ) as image_file:

        response = requests.post(
            SERPAPI_IMAGE_URL,

            files={
                "image": (
                    "image.jpg",
                    image_file,
                    "image/jpeg"
                )
            },

            data={
                "api_key": SERPAPI_KEY
            },

            timeout=60
        )

    response.raise_for_status()

    data = response.json()

    if data.get("error"):

        raise Exception(
            data["error"]
        )

    image_id = data.get(
        "image_id"
    )

    if not image_id:

        raise Exception(
            "حصلت مشكلة أثناء رفع الصورة."
        )

    return image_id


# =========================================================
# GOOGLE LENS
# =========================================================

def google_lens(
    image_id,
    search_type="visual_matches",
    country=None
):

    params = {
        "engine": "google_lens",
        "image_id": image_id,
        "type": search_type,
        "hl": "en",
        "safe": "active",
        "api_key": SERPAPI_KEY,
    }

    if country:

        params["country"] = country

    response = requests.get(
        SERPAPI_SEARCH_URL,
        params=params,
        timeout=90
    )

    response.raise_for_status()

    data = response.json()

    if data.get("error"):

        raise Exception(
            data["error"]
        )

    return data


# =========================================================
# TEXT
# =========================================================

def clean_text(text):

    if text is None:
        return ""

    text = str(text)

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def clean_product_title(title):

    title = clean_text(
        title
    )

    title = re.sub(
        r"\s*[-|–—]\s*(amazon|walmart|ebay|jumia|noon).*?$",
        "",
        title,
        flags=re.IGNORECASE
    )

    return title.strip()


# =========================================================
# DOMAIN
# =========================================================

def get_domain(link):

    if not link:
        return ""

    try:

        domain = urlparse(
            link
        ).netloc.lower()

        return domain.replace(
            "www.",
            ""
        )

    except Exception:

        return ""


def is_amazon_egypt(link):

    domain = get_domain(
        link
    )

    return (
        domain == "amazon.eg"
        or domain.endswith(
            ".amazon.eg"
        )
    )


# =========================================================
# PRICE
# =========================================================

def convert_arabic_numbers(text):

    translation = str.maketrans({
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
        "٫": ".",
        "٬": ",",
    })

    return str(text).translate(
        translation
    )


def price_to_text(value):

    if value is None:
        return ""

    if isinstance(
        value,
        str
    ):
        return value

    if isinstance(
        value,
        (int, float)
    ):
        return str(value)

    if isinstance(
        value,
        dict
    ):

        for key in [
            "value",
            "raw",
            "price",
            "amount",
            "extracted_value",
        ]:

            if key in value:

                result = price_to_text(
                    value[key]
                )

                if result:
                    return result

    return ""


def parse_price(value):

    if isinstance(
        value,
        dict
    ):

        extracted = value.get(
            "extracted_value"
        )

        if isinstance(
            extracted,
            (int, float)
        ):

            return float(
                extracted
            )

    text = price_to_text(
        value
    )

    if not text:
        return None

    text = convert_arabic_numbers(
        text
    )

    matches = re.findall(
        r"\d+(?:,\d{3})*(?:\.\d+)?",
        text
    )

    if not matches:
        return None

    try:

        return float(
            matches[0].replace(
                ",",
                ""
            )
        )

    except ValueError:

        return None


def extract_amazon_price(item):

    for field in [
        "extracted_price",
        "extracted_primary_price",
        "extracted_secondary_price",
    ]:

        value = item.get(
            field
        )

        if isinstance(
            value,
            (int, float)
        ):

            if value > 0:

                return float(
                    value
                )

    for field in [
        "price",
        "primary_price",
        "secondary_price",
    ]:

        value = parse_price(
            item.get(
                field
            )
        )

        if value and value > 0:

            return value

    return None


def format_egp(price):

    if price is None:

        return "السعر غير متاح"

    price = float(
        price
    )

    if price.is_integer():

        return (
            f"{price:,.0f} ج.م"
        )

    return (
        f"{price:,.2f} ج.م"
    )


# =========================================================
# IMAGE SEARCH PAGE
# =========================================================

def get_visual_results(
    lens_data
):

    visual_matches = lens_data.get(
        "visual_matches",
        []
    )

    if not isinstance(
        visual_matches,
        list
    ):

        return []

    results = []

    seen_links = set()

    for item in visual_matches:

        link = clean_text(
            item.get(
                "link",
                ""
            )
        )

        thumbnail = (
            item.get(
                "thumbnail"
            )
            or item.get(
                "image"
            )
            or ""
        )

        # البحث بالصورة محتاج صورة فعلًا
        if not thumbnail:

            continue

        clean_link = (
            link.split("?")[0]
            if link
            else ""
        )

        if (
            clean_link
            and clean_link in seen_links
        ):

            continue

        if clean_link:

            seen_links.add(
                clean_link
            )

        raw_price = item.get(
            "price"
        )

        price_value = parse_price(
            raw_price
        )

        currency = ""

        if isinstance(
            raw_price,
            dict
        ):

            currency = clean_text(
                raw_price.get(
                    "currency",
                    ""
                )
            )

        results.append({

            "rank": (
                item.get(
                    "position"
                )
                or len(results) + 1
            ),

            "title": (
                clean_text(
                    item.get(
                        "title"
                    )
                )
                or "نتيجة مشابهة للصورة"
            ),

            "link": link,

            "thumbnail": thumbnail,

            "image": (
                item.get(
                    "image"
                )
                or thumbnail
            ),

            "source": (
                clean_text(
                    item.get(
                        "source"
                    )
                )
                or get_domain(
                    link
                )
                or "موقع خارجي"
            ),

            "domain": get_domain(
                link
            ),

            "price_value":
                price_value,

            "price": (
                price_to_text(
                    raw_price
                )
                if raw_price
                else ""
            ),

            "currency":
                currency,

            "rating":
                item.get(
                    "rating"
                ),

            "reviews":
                item.get(
                    "reviews"
                ),

        })

    return results


# =========================================================
# GET PRODUCT NAME FOR PRICE PAGE
# =========================================================

def find_product_query(
    lens_data
):

    visual_matches = lens_data.get(
        "visual_matches",
        []
    )

    if isinstance(
        visual_matches,
        list
    ):

        for item in visual_matches:

            title = clean_product_title(
                item.get(
                    "title",
                    ""
                )
            )

            if len(title) >= 4:

                return title


    related = lens_data.get(
        "related_content",
        []
    )

    if isinstance(
        related,
        list
    ):

        for item in related:

            query = clean_text(
                item.get(
                    "query",
                    ""
                )
            )

            if len(query) >= 4:

                return query


    return ""


# =========================================================
# AMAZON EGYPT SEARCH
# =========================================================

def amazon_egypt_search(
    query,
    page=1
):

    response = requests.get(
        SERPAPI_SEARCH_URL,

        params={
            "engine":
                "amazon",

            "amazon_domain":
                "amazon.eg",

            "k":
                query,

            "page":
                page,

            "device":
                "desktop",

            "api_key":
                SERPAPI_KEY,
        },

        timeout=90
    )

    response.raise_for_status()

    data = response.json()

    if data.get(
        "error"
    ):

        raise Exception(
            data["error"]
        )

    return data


def parse_amazon_price_results(
    amazon_data
):

    results = []

    organic_results = amazon_data.get(
        "organic_results",
        []
    )

    if not isinstance(
        organic_results,
        list
    ):

        return []

    for item in organic_results:

        title = clean_text(
            item.get(
                "title",
                ""
            )
        )

        if not title:
            continue

        asin = clean_text(
            item.get(
                "asin",
                ""
            )
        )

        link = (
            item.get(
                "link_clean"
            )
            or item.get(
                "link"
            )
            or ""
        )

        if (
            not link
            and asin
        ):

            link = (
                f"https://www.amazon.eg/dp/{asin}"
            )

        # صفحة تحليل الأسعار:
        # Amazon Egypt فقط
        if not is_amazon_egypt(
            link
        ):

            continue

        price_value = (
            extract_amazon_price(
                item
            )
        )

        # أي نتيجة بدون سعر
        # مش هتدخل في التحليل
        if (
            price_value is None
            or price_value <= 0
        ):

            continue

        old_price = (
            item.get(
                "extracted_old_price"
            )
            or item.get(
                "extracted_old_primary_price"
            )
        )

        if not isinstance(
            old_price,
            (int, float)
        ):

            old_price = None

        results.append({

            "asin":
                asin,

            "title":
                title,

            "link":
                link,

            "thumbnail":
                item.get(
                    "thumbnail",
                    ""
                ),

            "price_value":
                float(
                    price_value
                ),

            "price":
                format_egp(
                    price_value
                ),

            "old_price": (
                format_egp(
                    old_price
                )
                if old_price
                else ""
            ),

            "rating":
                item.get(
                    "rating"
                ),

            "reviews":
                item.get(
                    "reviews"
                ),

            "prime":
                bool(
                    item.get(
                        "prime"
                    )
                ),

            "source":
                "Amazon مصر",

        })

    return results


def remove_amazon_duplicates(
    products
):

    final = []

    seen = set()

    for product in products:

        key = (
            product.get(
                "asin"
            )
            or product.get(
                "link"
            )
        )

        if not key:
            continue

        if key in seen:
            continue

        seen.add(
            key
        )

        final.append(
            product
        )

    return final


# =========================================================
# VALIDATION
# =========================================================

def validate_request():

    if not SERPAPI_KEY:

        return jsonify({

            "success":
                False,

            "error":
                "مفتاح SerpAPI مش متسجل على السيرفر."

        }), 500


    if "image" not in request.files:

        return jsonify({

            "success":
                False,

            "error":
                "اختار صورة الأول."

        }), 400


    image_file = request.files[
        "image"
    ]


    if not image_file.filename:

        return jsonify({

            "success":
                False,

            "error":
                "اختار صورة الأول."

        }), 400


    if not allowed_file(
        image_file.filename
    ):

        return jsonify({

            "success":
                False,

            "error":
                "الصيغ المسموحة JPG و PNG و WEBP."

        }), 400


    return None


# =========================================================
# PAGES
# =========================================================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


@app.route("/analysis")
def analysis_page():

    return render_template(
        "analysis.html"
    )


# =========================================================
# IMAGE SEARCH API
# =========================================================

@app.route(
    "/api/search",
    methods=["POST"]
)
def api_search():

    validation = validate_request()

    if validation:
        return validation


    image_file = request.files[
        "image"
    ]

    temp_path = None

    try:

        temp_path = prepare_image(
            image_file
        )

        image_id = (
            upload_image_to_serpapi(
                temp_path
            )
        )

        # الأولوية للصورة
        lens_data = google_lens(
            image_id,
            search_type="visual_matches"
        )

        results = get_visual_results(
            lens_data
        )

        if not results:

            return jsonify({

                "success":
                    True,

                "count":
                    0,

                "message":
                    "دورنا على الصورة وملاقيناش صور للمنتج ده في أي متجر.",

                "results":
                    []

            })

        return jsonify({

            "success":
                True,

            "count":
                len(
                    results
                ),

            "results":
                results

        })


    except requests.Timeout:

        return jsonify({

            "success":
                False,

            "error":
                "البحث أخد وقت أطول من المتوقع. جرّب تاني."

        }), 504


    except requests.RequestException as error:

        return jsonify({

            "success":
                False,

            "error":
                f"حصلت مشكلة أثناء الاتصال: {error}"

        }), 500


    except Exception as error:

        return jsonify({

            "success":
                False,

            "error":
                str(error)

        }), 500


    finally:

        if (
            temp_path
            and os.path.exists(
                temp_path
            )
        ):

            try:

                os.remove(
                    temp_path
                )

            except OSError:

                pass


# =========================================================
# PRICE ANALYSIS API
# =========================================================

@app.route(
    "/api/analyze",
    methods=["POST"]
)
def api_analyze():

    validation = validate_request()

    if validation:
        return validation


    image_file = request.files[
        "image"
    ]

    temp_path = None

    try:

        temp_path = prepare_image(
            image_file
        )

        image_id = (
            upload_image_to_serpapi(
                temp_path
            )
        )

        # Lens هنا بيحدد المنتج
        lens_data = google_lens(
            image_id,
            search_type="products",
            country="eg"
        )

        product_query = (
            find_product_query(
                lens_data
            )
        )

        if not product_query:

            return jsonify({

                "success":
                    True,

                "analysis_available":
                    False,

                "count":
                    0,

                "message":
                    "دورنا ومقدرناش نحدد المنتج بشكل كافي علشان نبحث عن أسعاره على Amazon مصر.",

                "results":
                    []

            })


        all_products = []


        # صفحتين من Amazon مصر
        for page in [1, 2]:

            amazon_data = (
                amazon_egypt_search(
                    product_query,
                    page
                )
            )

            page_results = (
                parse_amazon_price_results(
                    amazon_data
                )
            )

            all_products.extend(
                page_results
            )


        all_products = (
            remove_amazon_duplicates(
                all_products
            )
        )


        if not all_products:

            return jsonify({

                "success":
                    True,

                "analysis_available":
                    False,

                "count":
                    0,

                "query":
                    product_query,

                "message":
                    "دورنا على Amazon مصر وملاقيناش أي أسعار ظاهرة للمنتج ده.",

                "results":
                    []

            })


        # ترتيب الأسعار من الأقل للأعلى
        products_by_price = sorted(

            all_products,

            key=lambda product:
                product[
                    "price_value"
                ]

        )


        prices = [

            product[
                "price_value"
            ]

            for product
            in products_by_price

        ]


        minimum = min(
            prices
        )


        maximum = max(
            prices
        )


        average = statistics.mean(
            prices
        )


        cheapest = (
            products_by_price[0]
        )


        return jsonify({

            "success":
                True,

            "analysis_available":
                True,

            "query":
                product_query,

            "count":
                len(
                    products_by_price
                ),

            "stats": {

                "minimum":
                    format_egp(
                        minimum
                    ),

                "average":
                    format_egp(
                        average
                    ),

                "maximum":
                    format_egp(
                        maximum
                    ),

            },

            "closest":
                cheapest,

            "results":
                products_by_price

        })


    except requests.Timeout:

        return jsonify({

            "success":
                False,

            "error":
                "تحليل الأسعار أخد وقت أطول من المتوقع. جرّب تاني."

        }), 504


    except requests.RequestException as error:

        return jsonify({

            "success":
                False,

            "error":
                f"حصلت مشكلة أثناء الاتصال: {error}"

        }), 500


    except Exception as error:

        return jsonify({

            "success":
                False,

            "error":
                str(error)

        }), 500


    finally:

        if (
            temp_path
            and os.path.exists(
                temp_path
            )
        ):

            try:

                os.remove(
                    temp_path
                )

            except OSError:

                pass


# =========================================================
# ERRORS
# =========================================================

@app.errorhandler(413)
def too_large(error):

    return jsonify({

        "success":
            False,

        "error":
            "حجم الصورة كبير. أقصى حجم 10 ميجا."

    }), 413


@app.route("/creation")
def creation_page():

    return render_template(
        "creation.html"
    )


@app.route(
    "/api/creation/search",
    methods=["POST"]
)
def api_creation_search():

    product_code = request.form.get("product_code", "")

    if not product_code.strip():

        return jsonify({
            "success": False,
            "error": "من فضلك اكتب كود المنتج."
        }), 400

    if "image" not in request.files or not request.files["image"].filename:

        return jsonify({
            "success": False,
            "error": "من فضلك اختر صورة المنتج."
        }), 400

    image_file = request.files["image"]

    if not allowed_file(image_file.filename):

        return jsonify({
            "success": False,
            "error": "حصل خطأ أثناء البحث. حاول مرة أخرى."
        }), 400

    temp_path = None

    try:

        temp_path = prepare_image(image_file)
        image_id = upload_image_to_serpapi(temp_path)
        lens_data = google_lens(
            image_id,
            search_type="visual_matches"
        )
        results = get_visual_results(lens_data)

        readable_results = []
        for result in results:
            link = result.get("link", "")
            if not safe_external_url(link):
                continue
            try:
                if extract_product_facts(link) or is_valid_product_value("اسم المنتج", result.get("title", "")):
                    readable_results.append(result)
            except Exception:
                continue
        results = readable_results

        return jsonify({
            "success": True,
            "product_code": product_code,
            "count": len(results),
            "results": results,
            "message": "ملقيناش عروض مشابهة للصورة." if not results else ""
        })

    except Exception:

        return jsonify({
            "success": False,
            "error": "حصل خطأ أثناء البحث. حاول مرة أخرى."
        }), 500

    finally:

        if temp_path and os.path.exists(temp_path):

            try:
                os.remove(temp_path)
            except OSError:
                pass


class ProductPageParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.jsonld = []
        self._script = False
        self._script_text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self._script = True
            self._script_text = []

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._script:
            self.jsonld.append("".join(self._script_text))
            self._script = False

    def handle_data(self, data):
        if self._script:
            self._script_text.append(data)
        elif data.strip():
            self.text_parts.append(data.strip())


def safe_external_url(value):

    parsed = urlparse(clean_text(value))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, None)
        return all(not ipaddress.ip_address(item[4][0]).is_private for item in addresses)
    except (socket.gaierror, ValueError):
        return False


def normalize_fact(value):

    value = clean_text(unescape(str(value)))
    value = re.sub(r"[،,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip().lower()


GARBAGE_TOKENS = ("font-", "padding", "margin", "display:", "color:", "background", "var(", "url(", "px", "rem", "line-height", "letter-spacing", "vertical-align", "text-decoration", "overflow", "border", "position:", "!important", "<script", "<style", "</", "/> ", "class=", "style=", "aria-", "data-", "section-id", "navbar", "widget", "badge-", "schema\":", "machine_identifier", "__stripe", "function", "javascript", "css", "html", "selector", "\\u003c", "\\u003e", "&lt;", "&gt;")


def is_valid_product_value(label, value):

    value = clean_text(value)
    lowered = normalize_fact(value)
    if not value or "/>" in lowered or any(token in lowered for token in GARBAGE_TOKENS) or re.search(r"<[^>]+>|\b(?:class|style|aria|data)-[\w-]+\s*=", lowered):
        return False
    if re.fullmatch(r"\d+(?:[.,]\d+)?", lowered) or re.fullmatch(r"(?:random\s+standalone|random|unknown|standalone)\s+\d+", lowered):
        return False
    if label in ("الوزن", "السعة", "الكمية") and not re.search(r"(?:\d+[\s]*(?:g|kg|gram|grams|كجم|جم|ml|l|مل|ل|قطعة|pcs|piece))", lowered, re.IGNORECASE):
        return False
    if label in ("المقاس / الأبعاد",) and not re.search(r"\d+\s*[x×]\s*\d+", lowered):
        return False
    if label == "اللون" and re.fullmatch(r"#?[0-9a-f]{3,8}", lowered):
        return False
    return True


valid_product_value = is_valid_product_value


def clean_product_fact(label, value):

    value = clean_text(value).replace(r"\u003C", "<").replace(r"\u003E", ">")
    wrapped = re.fullmatch(r"<([a-z][a-z0-9]*)>\s*([^<>]+?)\s*</\1>", value, re.IGNORECASE)
    if wrapped:
        value = clean_text(unescape(wrapped.group(2)))
    return value if is_valid_product_value(label, value) else ""


def semantic_title_facts(title):

    title = clean_text(unescape(str(title)))
    lowered = normalize_fact(title)
    noise = ("buy online", "best price", "free shipping", "items shipped", "business supplies", "amazon", "walmart", "temu", "alibaba", "aliexpress", "souq")
    for phrase in noise:
        lowered = lowered.replace(phrase, " ")
    facts = {}
    if any(word in lowered for word in ("cat muzzle", "cat mask", "cat ball mask", "cat head cover", "كمامة قط", "قناع قط")):
        facts["اسم المنتج"] = "كمامة حماية للقطط"
        facts["نوع المنتج"] = "كمامة حماية للقطط"
    if "transparent" in lowered or "شفاف" in lowered:
        facts["التصميم"] = "شفاف"
    if "breathable" in lowered:
        facts["التهوية"] = "قابل للتنفس"
    if "double-lock" in lowered or "double lock" in lowered:
        facts["نوع الإغلاق"] = "قفل مزدوج"
    uses = []
    if "bath" in lowered:
        uses.append("الاستحمام")
    if "groom" in lowered:
        uses.append("العناية بالحيوان")
    if "nail trim" in lowered:
        uses.append("قص الأظافر")
    if "vet" in lowered:
        uses.append("الزيارات البيطرية")
    if uses:
        facts["الاستخدام"] = "، ".join(dict.fromkeys(uses))
    return facts


def semanticize_facts(title, facts):

    normalized = semantic_title_facts(title)
    for key, value in facts.items():
        if key == "اسم المنتج":
            normalized.setdefault("اسم المنتج", semantic_title_facts(value).get("اسم المنتج", ""))
            continue
        cleaned = clean_product_fact(key, value)
        if cleaned:
            normalized[key] = cleaned
    return {key: value for key, value in normalized.items() if value and is_valid_product_value(key, value)}


def extract_product_facts(url):

    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
        stream=True
    )
    response.raise_for_status()
    body = b""
    for chunk in response.iter_content(65536):
        body += chunk
        if len(body) > 2 * 1024 * 1024:
            break
    parser = ProductPageParser()
    parser.feed(body.decode(response.encoding or "utf-8", errors="ignore"))
    facts = {}
    for raw in parser.jsonld:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for node in nodes:
            if isinstance(node, dict) and str(node.get("@type", "")).lower() == "product":
                for key, label in (("name", "اسم المنتج"), ("color", "اللون"), ("material", "الخامة"), ("model", "الموديل"), ("weight", "الوزن"), ("category", "نوع المنتج"), ("description", "الوصف factual")):
                    value = node.get(key)
                    if isinstance(value, (str, int, float)) and label != "الوصف factual":
                        cleaned = clean_product_fact(label, value)
                        if cleaned:
                            facts[label] = cleaned
    blocked = ("brand", "manufacturer", "seller", "store", "company", "ماركة", "الشركة", "المصنع")
    return {key: value for key, value in facts.items() if is_valid_product_value(key, value) and not any(word in normalize_fact(value) for word in blocked)}


@app.route("/api/creation/extract", methods=["POST"])
def api_creation_extract():

    payload = request.get_json(silent=True) or {}
    product_code = payload.get("product_code", "")
    selected = payload.get("selected_sources", [])
    if not product_code or not isinstance(selected, list) or not selected:
        return jsonify({"success": False, "error": "تعذر استخراج بيانات كافية من العروض المختارة. جرب اختيار عروض أخرى."}), 400
    if len(selected) > 5:
        return jsonify({"success": False, "error": "يمكنك اختيار 5 عروض كحد أقصى."}), 400
    attributes = {}
    statuses = []
    for source in selected:
        url = source.get("link", "") if isinstance(source, dict) else ""
        entry = {"title": clean_text(source.get("title", "")), "source": clean_text(source.get("source", "") or get_domain(url)), "url": url, "status": "تعذر قراءة هذا العرض", "attributes": {}}
        if safe_external_url(url):
            try:
                entry["attributes"] = semanticize_facts(entry["title"], extract_product_facts(url))
                entry["status"] = "تم استخراج البيانات" if entry["attributes"] else "تعذر قراءة هذا العرض"
            except Exception:
                pass
        if not entry["attributes"]:
            title_facts = semantic_title_facts(entry["title"])
            if title_facts:
                entry["attributes"] = title_facts
                entry["status"] = "تم استخراج البيانات"
        statuses.append(entry)
        for key, value in entry["attributes"].items():
            if key == "الاستخدام":
                merged = attributes.setdefault(key, {}).setdefault("__merged__", {"value": "", "sources": []})
                parts = [part.strip() for part in value.split("،") if part.strip()]
                existing = [part.strip() for part in merged["value"].split("،") if part.strip()]
                merged["value"] = "، ".join(dict.fromkeys(existing + parts))
                merged["sources"].append(entry["source"])
                continue
            attributes.setdefault(key, {}).setdefault(normalize_fact(value), {"value": value, "sources": []})["sources"].append(entry["source"])
    agreed, conflicts = {}, {}
    for key, values in attributes.items():
        if len(values) == 1:
            agreed[key] = next(iter(values.values()))
        else:
            conflicts[key] = list(values.values())
    if not any(item["attributes"] for item in statuses):
        return jsonify({"success": True, "available": False, "statuses": statuses, "message": "تعذر استخراج بيانات كافية من العروض المختارة. جرب اختيار عروض أخرى."})
    return jsonify({"success": True, "available": True, "statuses": statuses, "agreed": agreed, "conflicts": conflicts, "missing": [], "extracted_count": sum(len(values) for values in attributes.values()), "offer_count": len(selected)})


@app.route("/api/creation/generate-content", methods=["POST"])
def api_creation_generate_content():

    payload = request.get_json(silent=True) or {}
    code = payload.get("product_code", "")
    attributes = payload.get("confirmed_attributes", {})
    if not isinstance(code, str) or not isinstance(attributes, dict):
        return jsonify({"success": False, "error": "تعذر تجهيز المحتوى."}), 400
    blocked = ("brand", "manufacturer", "seller", "store", "company", "ماركة", "المصنع", "الشركة")
    clean_attributes = {}
    for key, item in attributes.items():
        value = item.get("value", "") if isinstance(item, dict) else item
        if key and isinstance(value, str) and is_valid_product_value(key, value) and not any(word in normalize_fact(value) for word in blocked):
            clean_attributes[clean_text(key)] = clean_text(value)
    if not clean_attributes:
        return jsonify({"success": False, "error": "البيانات المستخرجة غير كافية لإنشاء محتوى موثوق. ارجع واختر عروضًا أوضح أو أدخل البيانات الناقصة."}), 400
    title = clean_attributes.get("اسم المنتج") or clean_attributes.get("نوع المنتج") or "منتج عام"
    title = " ".join([title] + [value for key, value in clean_attributes.items() if key not in ("اسم المنتج", "نوع المنتج")][:4])
    bullets = [f"{key}: {value}" for key, value in clean_attributes.items() if key not in ("اسم المنتج", "نوع المنتج")][:5]
    description = "منتج عام بالمواصفات التالية: " + "، ".join(f"{key}: {value}" for key, value in clean_attributes.items()) if clean_attributes else "منتج عام."
    return jsonify({"success": True, "product_code": code, "title": title, "bullets": bullets, "description": description, "specifications": clean_attributes, "category": payload.get("category"), "missing": ["الوزن", "الأبعاد"] if not any(key in clean_attributes for key in ("الوزن", "المقاس / الأبعاد")) else []})


# =========================================================
# LOCAL RUN
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
