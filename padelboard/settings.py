from pathlib import Path
import os
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

DEBUG = os.environ.get("DEBUG", "True") == "True"

ALLOWED_HOSTS = ["*"]

CSRF_TRUSTED_ORIGINS = [
    "https://gpadel26.onrender.com",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "channels",
    "sitio",
    "scoreboard",
    "accounts",
    "torneos",
    "reservas",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
]

SITE_ID = 1

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "padelboard.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.perfil_actual",
            ],
        },
    },
]

WSGI_APPLICATION = "padelboard.wsgi.application"
ASGI_APPLICATION = "padelboard.asgi.application"

# Channels
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# Base de datos
# En local usa SQLite.
# En Render usa PostgreSQL si existe DATABASE_URL.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = []

LOGIN_URL = "sitio:login"
LOGIN_REDIRECT_URL = "scoreboard:index"
LOGOUT_REDIRECT_URL = "sitio:inicio"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# --- Login con Google (django-allauth) ---
# El client_id/secret NO van hardcodeados: se toman de variables de entorno.
# Hay que crearlos en Google Cloud Console (OAuth consent screen + credenciales
# OAuth 2.0, tipo "Aplicación web") y setear GOOGLE_OAUTH_CLIENT_ID /
# GOOGLE_OAUTH_CLIENT_SECRET en el entorno (.env, variables de Render, etc.).
# URI de redirección autorizada a cargar en Google Cloud:
#   http://127.0.0.1:8000/accounts/google/login/callback/   (desarrollo local)
#   https://gpadel26.onrender.com/accounts/google/login/callback/  (producción)
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APPS": [
            {
                "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
                "secret": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
                "key": "",
            },
        ],
        "SCOPE": ["profile", "email"],
    }
}
# No auto-crear la cuenta con los datos de Google: primero mostramos nuestro
# propio formulario de "completar perfil" (celular + tipo de cuenta).
SOCIALACCOUNT_AUTO_SIGNUP = False
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_FORMS = {"signup": "accounts.forms.SocialSignupForm"}
ACCOUNT_ADAPTER = "accounts.adapters.GPadelAccountAdapter"
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_USER_MODEL_USERNAME_FIELD = "username"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]

LANGUAGE_CODE = "es-ar"
TIME_ZONE = "America/Argentina/Buenos_Aires"

USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = []
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"