"""Constants for the standalone Python application."""

from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
WEB_ROOT = PACKAGE_ROOT / "web"
STATIC_ROOT = WEB_ROOT / "static"
VIEW_ROOT = WEB_ROOT / "views"

try:
    VERSION = (PACKAGE_ROOT / "VERSION").read_text(encoding="utf-8").strip()
except OSError:
    VERSION = "dev"

APP_NAME = "JIO_tv"
PATH_PREFIX = ".JIO_tv"
MAX_RECOMMENDED_CHANNELS = 2000
UNSUPPORTED_CHANNELS_FORMAT = (
    "unsupported or invalid custom channels file format. Supported formats: "
    ".json, .yml, .yaml, or valid JSON/YAML content"
)

JIOTV_API_DOMAIN = "jiotvapi.media.jio.com"
TV_MEDIA_DOMAIN = "tv.media.jio.com"
JIOTV_CDN_DOMAIN = "jiotvapi.cdn.jio.com"
AUTH_MEDIA_DOMAIN = "auth.media.jio.com"
API_JIO_DOMAIN = "api.jio.com"
JIOTV_DATA_CDN_DOMAIN = "jiotv.data.cdn.jio.com"
JIOTV_CATCHUP_CDN_DOMAIN = "jiotv.catchup.cdn.jio.com"

REFRESH_TOKEN_URL = (
    "https://auth.media.jio.com/tokenservice/apis/v1/refreshtoken?langId=6"
)
REFRESH_SSO_TOKEN_URL = (
    "https://tv.media.jio.com/apis/v2.0/loginotp/refresh?langId=6"
)
CHANNELS_API_URL = (
    "https://jiotvapi.cdn.jio.com/apis/v3.0/getMobileChannelList/get/"
    "?langId=6&os=android&devicetype=phone&usertype=JIO&version=315&langId=6"
)
CHANNEL_URL = (
    "https://jiotv.data.cdn.jio.com/apis/v3.0/getMobileChannelList/get/"
    "?os=android&devicetype=phone&usertype=tvYR7NSNn7rymo3F"
)
EPG_URL = "https://jiotv.data.cdn.jio.com/apis/v1.3/getepg/get/?offset=%d&channel_id=%d"
EPG_POSTER_URL = "https://jiotv.catchup.cdn.jio.com/dare_images/shows"
EPG_POSTER_URL_SLASH = "https://jiotv.catchup.cdn.jio.com/dare_images/shows/"
PLAYBACK_API_PATH = "/playback/apis/v1.1/geturl?langId=6"

CONTENT_TYPE = "Content-Type"
ACCEPT = "Accept"
ACCEPT_ENCODING = "Accept-Encoding"
USER_AGENT = "User-Agent"
AUTHORIZATION = "Authorization"
HOST = "Host"
ACCESS_TOKEN = "accessToken"
DEVICE_TYPE = "devicetype"
VERSION_CODE = "versionCode"
OS = "os"
X_API_KEY = "x-api-key"

CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_JSON_UTF8 = "application/json; charset=utf-8"
ACCEPT_JSON = "application/json"
ACCEPT_ENCODING_GZIP = "gzip"
USER_AGENT_OKHTTP = "okhttp/4.2.2"
USER_AGENT_PLAY_TV = "plaYtv/7.1.7 (Linux;Android 8.1.0) ExoPlayerLib/2.11.7"
DEVICE_TYPE_PHONE = "phone"
OS_ANDROID = "android"
VERSION_CODE_315 = "315"
VERSION_CODE_389 = "389"
API_KEY_JIO = "l7xx75e822925f184370b2e25170c5d5820a"

EPG_TASK_ID = "epg-generation"

CATEGORY_MAP: dict[int, str] = {
    0: "All Categories",
    5: "Entertainment",
    6: "Movies",
    7: "Kids",
    8: "Sports",
    9: "Lifestyle",
    10: "Infotainment",
    12: "News",
    13: "Music",
    15: "Devotional",
    16: "Business",
    17: "Educational",
    18: "Shopping",
    19: "JioDarshan",
}

LANGUAGE_MAP: dict[int, str] = {
    0: "All Languages",
    1: "Hindi",
    2: "Marathi",
    3: "Punjabi",
    4: "Urdu",
    5: "Bengali",
    6: "English",
    7: "Malayalam",
    8: "Tamil",
    9: "Gujarati",
    10: "Odia",
    11: "Telugu",
    12: "Bhojpuri",
    13: "Kannada",
    14: "Assamese",
    15: "Nepali",
    16: "French",
    18: "Other",
}

SONY_LIST = {
    "154",
    "155",
    "162",
    "289",
    "291",
    "471",
    "474",
    "476",
    "483",
    "514",
    "524",
    "525",
    "697",
    "872",
    "873",
    "874",
    "891",
    "892",
    "1146",
    "1393",
    "1772",
    "1773",
    "1774",
    "1775",
}

SONY_CHANNELS = {
    "sonyhd": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50L2RCZHdPaUdhUX"
        "Z5MFRBMXpPc2pWNncvbWFzdGVyLm0zdTg="
    ),
    "sonysabhd": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50L0NyVGl2a0"
        "RFU1dxd3ZVajN6RkVZRUEvbWFzdGVyLm0zdTg="
    ),
    "sonypal": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50L2RoUHJHUn"
        "dEUnZ1TVF0bWx6cHB6UVEvbWFzdGVyLm0zdTg="
    ),
    "sonypixhd": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50L3g3clhXZD"
        "JFUloydHZ5UVdQbU8xSEEvbWFzdGVyLm0zdTg="
    ),
    "sonymaxhd": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50L1VjakhO"
        "Sm1DUTFXUmxHS2xabTczUUEvbWFzdGVyLm0zdTg="
    ),
    "sonymax2": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50L01kUTV"
        "aeS1QU3JhT2NjWHU4amZsQ2cvbWFzdGVyLm0zdTg="
    ),
    "sonywah": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50L2dYNXJDQm"
        "Y2UTctRDVBV1ktc292elEvbWFzdGVyLm0zdTg="
    ),
    "sonyten1hd": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50L3dHNzVu"
        "NVU4UnJPS2lGemFXT2JYYkEvbWFzdGVyLm0zdTg="
    ),
    "sonyten2hd": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50L1Y5aC1p"
        "eU94UmlHcDQxcHBRU2NEU1EvbWFzdGVyLm0zdTg="
    ),
    "sonyten3hd": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50L2x0c0"
        "NHN1RCU0NTRG15cTByUXR2U0EvbWFzdGVyLm0zdTg="
    ),
    "sonyten4hd": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50L3NtWXli"
        "SV9KVG9XYUh6d294U0U5cUEvbWFzdGVyLm0zdTg="
    ),
    "sonyten5hd": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50L1NsZV9U"
        "UjhyUUl1WkhXenNoRVhZalEvbWFzdGVyLm0zdTg="
    ),
    "sonybbcearthhd": (
        "aHR0cHM6Ly9kYWkuZ29vZ2xlLmNvbS9saW5lYXIvaGxzL2V2ZW50LzZiVldZ"
        "SUtHUzBDSWEtY09wWlpKUFEvbWFzdGVyLm0zdTg="
    ),
}

SONY_JIO_MAP = {
    "sl291": "sonyhd",
    "sl154": "sonysabhd",
    "sl474": "sonypal",
    "sl762": "sonypixhd",
    "sl476": "sonymaxhd",
    "sl483": "sonymax2",
    "sl1393": "sonywah",
    "sl162": "sonyten1hd",
    "sl891": "sonyten2hd",
    "sl892": "sonyten3hd",
    "sl1772": "sonyten4hd",
    "sl155": "sonyten5hd",
    "sl852": "sonybbcearthhd",
}
