import os
import pickle
import datetime
import secrets
import hmac
import hashlib
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from contextlib import contextmanager
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row

import razorpay
import resend

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_from_directory,
    Response,
    jsonify
)

from werkzeug.utils import secure_filename
from markupsafe import escape

from dotenv import load_dotenv

from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField
from wtforms.validators import DataRequired, Email

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build


# ============================================================
# ENVIRONMENT & APP CONFIG
# ============================================================

os.environ.setdefault(
    "OAUTHLIB_INSECURE_TRANSPORT",
    "1"
)

load_dotenv()

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-key"
)


# ============================================================
# GOOGLE CONFIG
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events"
]

CREDENTIALS_FILE = "credentials.json"


# ============================================================
# UPLOAD CONFIG
# ============================================================

UPLOAD_FOLDER = "uploads"

ALLOWED_UPLOAD_EXTENSIONS = {
    "pdf"
}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

app.config["MAX_CONTENT_LENGTH"] = (
    10 * 1024 * 1024
)

os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)


# ============================================================
# DATABASE
# ============================================================

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    ""
)

BOOKING_ADMIN_USERNAME = os.environ.get(
    "BOOKING_ADMIN_USERNAME",
    ""
)

BOOKING_ADMIN_PASSWORD = os.environ.get(
    "BOOKING_ADMIN_PASSWORD",
    ""
)


# ============================================================
# UPI CONFIGURATION
# ============================================================

UPI_ID = os.environ.get(
    "UPI_ID",
    "kodandaram66661@ybl"
)

UPI_NAME = os.environ.get(
    "UPI_NAME",
    "Home Entertainments"
)


# ============================================================
# BOOKING CONFIGURATION
# ============================================================

# A PENDING booking reserves seats for this many minutes.
PENDING_BOOKING_MINUTES = 10


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_booking_db():
    """
    Open a PostgreSQL database connection.

    Render provides DATABASE_URL through environment variables.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured.")

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
    )


@contextmanager
def get_db():
    """Provide a database connection and always close it after use."""
    db = get_booking_db()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_booking_db():
    """
    Create booking tables and indexes if they do not exist.
    """

    db = get_booking_db()

    try:

        db.execute("""
            CREATE TABLE IF NOT EXISTS shows (
                id BIGSERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                theatre TEXT NOT NULL,
                location TEXT NOT NULL,
                show_date TEXT NOT NULL,
                show_time TEXT NOT NULL,
                ticket_price NUMERIC(10, 2) NOT NULL DEFAULT 0,
                capacity INTEGER NOT NULL DEFAULT 0,
                payment_link TEXT DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id BIGSERIAL PRIMARY KEY,
                booking_code TEXT UNIQUE NOT NULL,
                show_id BIGINT NOT NULL,
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                total_amount NUMERIC(10, 2) NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                utr TEXT DEFAULT '',
                payment_submitted_at TEXT DEFAULT '',
                created_at TEXT NOT NULL,

                CONSTRAINT fk_booking_show
                    FOREIGN KEY (show_id)
                    REFERENCES shows(id)
                    ON DELETE CASCADE,

                CONSTRAINT booking_quantity_positive
                    CHECK (quantity > 0),

                CONSTRAINT booking_total_nonnegative
                    CHECK (total_amount >= 0)
            )
        """)

        # Useful indexes for booking queries.
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_bookings_show_id
            ON bookings(show_id)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_bookings_status
            ON bookings(status)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_bookings_show_status
            ON bookings(show_id, status)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_bookings_created_at
            ON bookings(created_at)
        """)

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# ============================================================
# ADMIN
# ============================================================

BOOKING_ADMIN_USERNAME = os.environ.get(
    "BOOKING_ADMIN_USERNAME",
    "admin"
)

BOOKING_ADMIN_PASSWORD = os.environ.get(
    "BOOKING_ADMIN_PASSWORD",
    "admin"
)


# ============================================================
# EMAIL
# ============================================================

RESEND_API_KEY = os.environ.get(
    "RESEND_API_KEY",
    ""
)

RESEND_FROM_EMAIL = os.environ.get(
    "RESEND_FROM_EMAIL",
    ""
)

MAIL_RECEIVER = os.environ.get(
    "MAIL_RECEIVER",
    ""
)

if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY


# ============================================================
# RAZORPAY
# ============================================================

RAZORPAY_KEY_ID = os.environ.get(
    "RAZORPAY_KEY_ID",
    ""
)

RAZORPAY_KEY_SECRET = os.environ.get(
    "RAZORPAY_KEY_SECRET",
    ""
)

RAZORPAY_WEBHOOK_SECRET = os.environ.get(
    "RAZORPAY_WEBHOOK_SECRET",
    ""
)


def get_razorpay_client():
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise RuntimeError(
            "Razorpay is not configured. "
            "Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET."
        )

    return razorpay.Client(
        auth=(
            RAZORPAY_KEY_ID,
            RAZORPAY_KEY_SECRET
        )
    )


# ============================================================
# UPI CONFIGURATION
# ============================================================

UPI_ID = os.environ.get(
    "UPI_ID",
    "kodandaram66661@ybl"
)

UPI_NAME = os.environ.get(
    "UPI_NAME",
    "Home Entertainments"
)


# ============================================================
# BOOKING SETTINGS
# ============================================================

PENDING_BOOKING_MINUTES = 10


# ============================================================
# WEBSITE SEO INFORMATION & DATA
# ============================================================

SITE_NAME = "Home Entertainments"

SITE_DESCRIPTION = (
    "Home Entertainments is a Kannada entertainment "
    "production house creating movies, web series, "
    "music and original entertainment content."
)

SITE_URL = (
    "https://homeentertainments.in"
)

SEO_DATA = {
    "home": {
        "title": (
            "Home Entertainments | Kannada Entertainment, "
            "Movies, Web Series & Music"
        ),
        "description": (
            "Home Entertainments is a Kannada entertainment "
            "production house creating movies, web series, "
            "music and original entertainment content."
        )
    },
    "about": {
        "title": (
            "About Home Entertainments | Kannada "
            "Entertainment Production House"
        ),
        "description": (
            "Learn about Home Entertainments, a Kannada "
            "entertainment production house focused on "
            "movies, web series, music and creative storytelling."
        )
    },
    "movies": {
        "title": (
            "Movies & Web Series | Home Entertainments"
        ),
        "description": (
            "Explore movies, web series and original "
            "cinema projects from Home Entertainments."
        )
    },
    "music": {
        "title": (
            "Music | Kannada Songs & Original Music | "
            "Home Entertainments"
        ),
        "description": (
            "Listen to original Kannada songs, lyrical videos "
            "and music releases from Home Entertainments."
        )
    },
    "collaboration": {
        "title": (
            "Collaboration | Partner With Home Entertainments"
        ),
        "description": (
            "Collaborate with Home Entertainments on films, "
            "music, web series, events and creative entertainment projects."
        )
    },
    "join": {
        "title": (
            "Join Us | Careers & Opportunities | "
            "Home Entertainments"
        ),
        "description": (
            "Join Home Entertainments and explore opportunities "
            "in acting, filmmaking, production, creative work and entertainment."
        )
    },
    "contact": {
        "title": (
            "Contact Home Entertainments | Get In Touch"
        ),
        "description": (
            "Contact Home Entertainments for collaborations, "
            "projects, entertainment enquiries and business opportunities."
        )
    },
    "team": {
        "title": (
            "Our Team | Home Entertainments"
        ),
        "description": (
            "Meet the creative team behind Home Entertainments "
            "and its entertainment projects."
        )
    },
    "booking": {
        "title": (
            "Book Tickets | C&C Chiru & Charu | "
            "Home Entertainments"
        ),
        "description": (
            "Book theatre tickets for C&C (Chiru & Charu), "
            "a Kannada original web series from Home Entertainments."
        )
    }
}


def render_seo_template(
    template_name,
    seo_key,
    **context
):
    seo = SEO_DATA.get(seo_key, {})
    context["title"] = seo.get("title", f"{SITE_NAME} | Kannada Entertainment")
    context["description"] = seo.get("description", SITE_DESCRIPTION)
    return render_template(template_name, **context)


# ============================================================
# GENERAL HELPERS
# ============================================================

def allowed_file(filename):
    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_UPLOAD_EXTENSIONS
    )


def get_form_data():
    data = {}
    for key, value in request.form.items():
        if key.lower() in {"csrf_token", "submit"}:
            continue
        value = (value or "").strip()
        if value:
            data[key.replace("_", " ").title()] = value
    return data


def current_datetime():
    return datetime.datetime.now(
        datetime.timezone.utc
    ).replace(tzinfo=None)


def current_timestamp_string():
    return current_datetime().isoformat(
        timespec="seconds"
    )


def parse_booking_datetime(value):
    if not value:
        return None

    try:
        parsed = datetime.datetime.fromisoformat(str(value))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return parsed
    except (ValueError, TypeError):
        return None


def is_booking_expired(created_at):
    created = parse_booking_datetime(created_at)

    if not created:
        return False

    return (
        current_datetime() - created
    ).total_seconds() > PENDING_BOOKING_MINUTES * 60


def money_to_paise(amount):
    """Convert a rupee amount to integer paise using decimal arithmetic."""
    amount_decimal = Decimal(str(amount)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )
    if not amount_decimal.is_finite():
        raise ValueError(f"Invalid monetary amount: {amount!r}")
    return int(amount_decimal * 100)


def generate_booking_code():
    return (
        "CC-"
        + secrets.token_hex(4).upper()
    )


def generate_upi_payment_url(amount):
    payee_name = quote(UPI_NAME, safe="")
    upi_id = quote(UPI_ID, safe="@")
    amount_value = f"{float(amount):.2f}"
    return (
        "upi://pay?"
        f"pa={upi_id}"
        f"&pn={payee_name}"
        f"&am={amount_value}"
        "&cu=INR"
    )


# ============================================================
# ADMIN LOGIN REQUIRED DECORATORS
# ============================================================

def admin_required(view_func):

    @wraps(view_func)
    def wrapped(*args, **kwargs):

        if not session.get("booking_admin_logged_in") and not session.get("booking_admin"):
            return redirect(
                url_for("booking_admin_login")
            )

        return view_func(*args, **kwargs)

    return wrapped


def booking_admin_required():
    if not session.get("booking_admin") and not session.get("booking_admin_logged_in"):
        return redirect(
            url_for("booking_admin_login")
        )
    return None


# ============================================================
# EMAIL SUBMISSION HELPERS
# ============================================================

def send_submission_email(
    subject,
    form_data,
    uploaded_file=None,
    uploaded_filename=None
):
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not configured.")
    if not RESEND_FROM_EMAIL:
        raise RuntimeError("RESEND_FROM_EMAIL is not configured.")
    if not MAIL_RECEIVER:
        raise RuntimeError("MAIL_RECEIVER is not configured.")

    visitor_email = (
        form_data.get("Email")
        or form_data.get("Email Address")
        or form_data.get("E-Mail")
    )

    text_lines = [
        "NEW WEBSITE SUBMISSION",
        "",
        f"Form: {subject}",
        "",
        "-----------------------------------"
    ]

    for field, value in form_data.items():
        text_lines.append(f"{field}: {value}")

    text_lines.extend([
        "-----------------------------------",
        "",
        "Submitted from the Home Entertainments website."
    ])

    plain_text_body = "\n".join(text_lines)

    rows = ""
    for field, value in form_data.items():
        rows += f"""
        <tr>
            <td style="
                padding: 14px 16px;
                font-weight: 600;
                color: #555555;
                background-color: #f8f9fa;
                border-bottom: 1px solid #e5e7eb;
                width: 30%;
                vertical-align: top;
            ">
                {escape(field)}
            </td>
            <td style="
                padding: 14px 16px;
                color: #222222;
                border-bottom: 1px solid #e5e7eb;
                vertical-align: top;
                white-space: pre-wrap;
                line-height: 1.6;
            ">
                {escape(value)}
            </td>
        </tr>
        """

    reply_information = ""
    if visitor_email:
        reply_information = f"""
        <div style="
            margin-top: 22px;
            padding: 15px 17px;
            background-color: #eff6ff;
            border-left: 4px solid #2563eb;
            border-radius: 6px;
            color: #1e3a8a;
            font-size: 13px;
            line-height: 1.6;
        ">
            <strong>Quick Reply</strong><br>
            Reply directly to this email to contact:
            <strong>{escape(visitor_email)}</strong>
        </div>
        """

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{escape(subject)}</title>
    </head>
    <body style="
        margin: 0;
        padding: 30px 15px;
        background-color: #f3f4f6;
        font-family: Arial, Helvetica, sans-serif;
        color: #222222;
    ">
        <div style="
            max-width: 680px;
            margin: 0 auto;
            background-color: #ffffff;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 15px rgba(0,0,0,0.08);
        ">
            <div style="
                background: linear-gradient(135deg, #111827, #374151);
                padding: 28px 30px;
                color: #ffffff;
            ">
                <div style="
                    font-size: 12px;
                    text-transform: uppercase;
                    letter-spacing: 1.5px;
                    color: #d1d5db;
                    margin-bottom: 8px;
                ">
                    HOME ENTERTAINMENTS
                </div>
                <h1 style="margin: 0; font-size: 25px; font-weight: 600;">
                    New Form Submission
                </h1>
                <p style="margin: 8px 0 0; font-size: 14px; color: #d1d5db;">
                    {escape(subject)}
                </p>
            </div>
            <div style="padding: 30px;">
                <h2 style="margin: 0 0 18px; font-size: 18px; color: #111827;">
                    Submission Details
                </h2>
                <table width="100%" cellpadding="0" cellspacing="0" style="
                    border: 1px solid #e5e7eb;
                    border-radius: 8px;
                    border-spacing: 0;
                    overflow: hidden;
                    font-size: 14px;
                ">
                    {rows}
                </table>
                {reply_information}
                <div style="
                    margin-top: 25px;
                    padding: 16px;
                    background-color: #f9fafb;
                    border-radius: 8px;
                    font-size: 13px;
                    color: #6b7280;
                    line-height: 1.5;
                ">
                    This message was submitted through the <strong>Home Entertainments</strong> website.
                </div>
            </div>
            <div style="
                padding: 18px 30px;
                background-color: #f9fafb;
                border-top: 1px solid #e5e7eb;
                text-align: center;
                font-size: 12px;
                color: #9ca3af;
            ">
                Home Entertainments &bull; Website Submission
            </div>
        </div>
    </body>
    </html>
    """

    params = {
        "from": RESEND_FROM_EMAIL,
        "to": [MAIL_RECEIVER],
        "subject": subject,
        "html": html_body,
        "text": plain_text_body
    }

    if visitor_email:
        params["reply_to"] = [visitor_email]

    if uploaded_file and uploaded_filename:
        uploaded_file.stream.seek(0)
        file_data = list(uploaded_file.read())
        params["attachments"] = [
            {
                "filename": secure_filename(uploaded_filename),
                "content": file_data
            }
        ]

    response = resend.Emails.send(params)
    app.logger.info("Email sent successfully through Resend: %s", response)
    return response


def send_booking_confirmation_email(booking, show):
    if not RESEND_API_KEY or not RESEND_FROM_EMAIL:
        return

    customer_email = booking["email"]
    status = booking["status"]

    if status == "CONFIRMED":
        email_subject = f"C&C Booking Confirmed - {booking['booking_code']}"
        main_message = "Your payment has been verified and your booking is confirmed."
    elif status == "PAYMENT SUBMITTED":
        email_subject = f"C&C Payment Submitted - {booking['booking_code']}"
        main_message = "Your payment details have been submitted successfully. Our team will verify the payment and confirm your booking."
    elif status == "CANCELLED":
        email_subject = f"C&C Booking Cancelled - {booking['booking_code']}"
        main_message = "Your booking has been cancelled. Please contact Home Entertainments if you need assistance."
    elif status == "EXPIRED":
        email_subject = f"C&C Booking Expired - {booking['booking_code']}"
        main_message = "Your pending booking expired because payment details were not submitted within the reservation period."
    else:
        email_subject = f"C&C Booking Received - {booking['booking_code']}"
        main_message = "Your theatre booking has been received successfully. Please complete the payment process."

    utr_value = booking["utr"] if booking.get("utr") else "Not submitted"

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:650px;margin:auto;">
        <h2 style="color:#b89020;">Home Entertainments</h2>
        <h3>C&C – Chiru & Charu | Booking</h3>
        <p>Hi {escape(booking["customer_name"])},</p>
        <p>{escape(main_message)}</p>
        <table cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;">
            <tr><td><strong>Booking ID</strong></td><td>{escape(booking["booking_code"])}</td></tr>
            <tr><td><strong>Show</strong></td><td>{escape(show["title"])}</td></tr>
            <tr><td><strong>Theatre</strong></td><td>{escape(show["theatre"])}</td></tr>
            <tr><td><strong>Location</strong></td><td>{escape(show["location"])}</td></tr>
            <tr><td><strong>Date</strong></td><td>{escape(show["show_date"])}</td></tr>
            <tr><td><strong>Time</strong></td><td>{escape(show["show_time"])}</td></tr>
            <tr><td><strong>Tickets</strong></td><td>{booking["quantity"]}</td></tr>
            <tr><td><strong>Total</strong></td><td>₹{float(booking["total_amount"]):.2f}</td></tr>
            <tr><td><strong>Status</strong></td><td>{escape(status)}</td></tr>
            <tr><td><strong>UTR / Transaction ID</strong></td><td>{escape(utr_value)}</td></tr>
        </table>
        <p>Please keep your Booking ID for reference.</p>
        <p>Regards,<br><strong>Home Entertainments</strong></p>
    </div>
    """

    resend.Emails.send({
        "from": RESEND_FROM_EMAIL,
        "to": [customer_email],
        "subject": email_subject,
        "html": html
    })


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_booking_db():
    with get_db() as db:

        # ----------------------------------------------------
        # SHOWS
        # ----------------------------------------------------
        db.execute("""
            CREATE TABLE IF NOT EXISTS shows (
                id BIGSERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                theatre TEXT NOT NULL,
                location TEXT NOT NULL,
                show_date TEXT NOT NULL,
                show_time TEXT NOT NULL,
                ticket_price NUMERIC(10,2) NOT NULL DEFAULT 0,
                capacity INTEGER NOT NULL DEFAULT 0,
                payment_link TEXT DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)

        # ----------------------------------------------------
        # BOOKINGS
        # ----------------------------------------------------
        db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id BIGSERIAL PRIMARY KEY,
                booking_code TEXT UNIQUE NOT NULL,
                show_id BIGINT NOT NULL,
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                total_amount NUMERIC(10,2) NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                utr TEXT DEFAULT '',
                payment_submitted_at TEXT DEFAULT '',
                created_at TEXT NOT NULL,

                CONSTRAINT fk_booking_show
                    FOREIGN KEY (show_id)
                    REFERENCES shows(id)
                    ON DELETE CASCADE,

                CONSTRAINT booking_quantity_positive
                    CHECK (quantity > 0),

                CONSTRAINT booking_total_nonnegative
                    CHECK (total_amount >= 0)
            )
        """)

        # ----------------------------------------------------
        # RAZORPAY MIGRATION
        # ----------------------------------------------------
        db.execute("""
            ALTER TABLE bookings
            ADD COLUMN IF NOT EXISTS razorpay_order_id TEXT DEFAULT ''
        """)

        db.execute("""
            ALTER TABLE bookings
            ADD COLUMN IF NOT EXISTS razorpay_payment_id TEXT DEFAULT ''
        """)

        db.execute("""
            ALTER TABLE bookings
            ADD COLUMN IF NOT EXISTS razorpay_signature TEXT DEFAULT ''
        """)

        db.execute("""
            ALTER TABLE bookings
            ADD COLUMN IF NOT EXISTS payment_verified_at TEXT DEFAULT ''
        """)

        db.execute("""
            ALTER TABLE bookings
            ADD COLUMN IF NOT EXISTS payment_method TEXT DEFAULT ''
        """)

        # ----------------------------------------------------
        # INDEXES
        # ----------------------------------------------------
        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_razorpay_order_id
            ON bookings(razorpay_order_id)
            WHERE razorpay_order_id IS NOT NULL AND razorpay_order_id <> ''
        """)

        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_razorpay_payment_id
            ON bookings(razorpay_payment_id)
            WHERE razorpay_payment_id IS NOT NULL AND razorpay_payment_id <> ''
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_bookings_show_id
            ON bookings(show_id)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_bookings_status
            ON bookings(status)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_bookings_show_status
            ON bookings(show_id, status)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_bookings_created_at
            ON bookings(created_at)
        """)

        db.commit()


# ============================================================
# EXPIRE OLD BOOKINGS
# ============================================================

def expire_old_pending_bookings(db):
    cutoff = current_datetime() - datetime.timedelta(minutes=PENDING_BOOKING_MINUTES)
    cutoff_string = cutoff.isoformat(timespec="seconds")

    db.execute("""
        UPDATE bookings
        SET status = 'EXPIRED'
        WHERE status = 'PENDING'
          AND created_at < %s
    """, (cutoff_string,))


# ============================================================
# GET AVAILABLE SEATS
# ============================================================

def get_available_seats(db, show_id, capacity):
    expire_old_pending_bookings(db)

    result = db.execute("""
        SELECT COALESCE(SUM(quantity), 0) AS reserved
        FROM bookings
        WHERE show_id = %s
        AND status IN ('PENDING', 'PAYMENT SUBMITTED', 'CONFIRMED')
    """, (show_id,)).fetchone()

    reserved = int(result["reserved"] or 0)
    return max(0, int(capacity) - reserved)


def booking_available_seats(db, show_id, capacity):
    return get_available_seats(db, show_id, capacity)


# ============================================================
# RAZORPAY ORDER CREATION
# ============================================================

def create_razorpay_order(booking, show):
    """Create a Razorpay order after validating the booking amount.

    Razorpay expects the amount in paise. This application requires at least
    ₹1.00 (100 paise); reject zero/negative or below-minimum totals before
    calling the Razorpay API.
    """
    client = get_razorpay_client()
    amount_paise = money_to_paise(booking["total_amount"])

    if amount_paise < 100:
        raise ValueError(
            "Booking total must be at least ₹1.00 for Razorpay. "
            f"Booking {booking.get('id')} has total_amount="
            f"{booking.get('total_amount')} (amount_paise={amount_paise})."
        )

    razorpay_order = client.order.create({
        "amount": amount_paise,
        "currency": "INR",
        "receipt": booking["booking_code"],
        "notes": {
            "booking_code": booking["booking_code"],
            "show_id": str(show["id"]),
            "customer_name": booking["customer_name"]
        }
    })

    return razorpay_order


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():
    return render_seo_template("home.html", "home")


# ============================================================
# TEAM
# ============================================================

@app.route("/team")
def team():
    team_members = [
        {
            "name": "Kodanda Ram",
            "role": "Actor",
            "image": "image.png",
            "bio": "Award-winning actor with 10+ years of experience in cinema.",
            "social": {"instagram": "#", "twitter": "#", "linkedin": "#"}
        },
        {
            "name": "Jane Smith",
            "role": "Director",
            "image": "jane.jpg",
            "bio": "Creative director shaping unique storytelling experiences.",
            "social": {"instagram": "#", "twitter": "#", "linkedin": "#"}
        },
        {
            "name": "John Doe",
            "role": "Actor",
            "image": "john.jpg",
            "bio": "Award-winning actor with 10+ years of experience in cinema.",
            "social": {"instagram": "#", "twitter": "#", "linkedin": "#"}
        },
        {
            "name": "John Doe",
            "role": "Actor",
            "image": "john.jpg",
            "bio": "Award-winning actor with 10+ years of experience in cinema.",
            "social": {"instagram": "#", "twitter": "#", "linkedin": "#"}
        },
        {
            "name": "John Doe",
            "role": "Actor",
            "image": "john.jpg",
            "bio": "Award-winning actor with 10+ years of experience in cinema.",
            "social": {"instagram": "#", "twitter": "#", "linkedin": "#"}
        }
    ]
    return render_seo_template("team.html", "team", team_members=team_members)


# ============================================================
# ABOUT
# ============================================================

@app.route("/about")
def about():
    team_members = [
        {
            "name": "Kodanda Ram",
            "role": "Actor",
            "image": "KodandaRam.jpeg",
            "bio": (
                "An actor and the Founder of Home Entertainments, "
                "passionate about cinema, storytelling, and bringing "
                "characters to life."
                "Beginning his acting journey with dedication, "
                "creativity, and a vision to make a meaningful mark "
                "in the industry."
            ),
            "social": {
                "instagram": "#",
                "twitter": "#",
                "linkedin": "https://www.linkedin.com/in/agraharam-kodanda-ram-0a4991297/"
            }
        },
        {
            "name": "Bhargavi S Babu",
            "role": "Actress",
            "image": "BhargaviBabu.jpeg",
            "bio": (
                "An emerging actress with experience in web series, "
                "passionate about storytelling and bringing characters "
                "to life with authenticity"
                "With a growing interest in diverse roles, she continues "
                "to develop her craft and build her journey in the world "
                "of cinema."
            ),
            "social": {
                "instagram": "https://www.instagram.com/bhargavi.s.babu/",
                "facebook": "https://www.facebook.com/bhargavi.babu.3/",
                "linkedin": "https://www.linkedin.com/in/bhargavi-s-babu-85188b173/"
            }
        },
        {
            "name": "Harshith B. Gowda",
            "role": "DOP,Colorist",
            "image": "HarshitGowda.jpeg",
            "bio": (
                "A passionate Cinematographer and Colorist with 10+ "
                "years experience, dedicated to crafting visually "
                "compelling frames through creative camera work, "
                "lighting, and color."
            ),
            "social": {
                "instagram": "https://www.instagram.com/harshith_b_gowda/",
                "facebook": "https://www.facebook.com/harshith.bgowda.9/",
                "linkedin": "https://www.linkedin.com/in/harshith-b-gowda-b10670206/"
            }
        },
        {
            "name": "Madhu Sagar",
            "role": "Cinematographer",
            "image": "MadhuSagar.jpeg",
            "bio": (
                "A passionate cinematographer with a strong eye for "
                "composition, lighting, and visual storytelling."
                "Dedicated to creating immersive and cinematic visuals "
                "that bring every story and character to life."
            ),
            "social": {
                "instagram": "#",
                "twitter": "#",
                "linkedin": "#"
            }
        }
    ]
    return render_seo_template("about.html", "about", team_members=team_members)


# ============================================================
# CONTACT
# ============================================================

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        try:
            form_data = get_form_data()
            send_submission_email(
                subject="New Contact Form Submission",
                form_data=form_data
            )
            flash("Thank you! Your message has been sent successfully.", "success")
        except Exception as e:
            app.logger.exception("Contact email failed: %s", e)
            flash("There was an issue sending your message. Please try again.", "danger")

        return redirect(url_for("contact"))

    return render_seo_template("contact.html", "contact")


# ============================================================
# JOIN
# ============================================================

@app.route("/join", methods=["GET", "POST"])
def join():
    if request.method == "POST":
        try:
            form_data = get_form_data()
            resume = request.files.get("resume")

            if resume and resume.filename:
                if not allowed_file(resume.filename):
                    flash("Only PDF files are allowed for the resume.", "danger")
                    return redirect(url_for("join"))

                send_submission_email(
                    subject="New Join Us Submission",
                    form_data=form_data,
                    uploaded_file=resume,
                    uploaded_filename=resume.filename
                )
            else:
                send_submission_email(
                    subject="New Join Us Submission",
                    form_data=form_data
                )

            flash("Your submission has been sent successfully!", "success")

        except Exception as e:
            app.logger.exception("Join Us email failed: %s", e)
            flash("There was an issue sending your submission.", "danger")

        return redirect(url_for("join"))

    return render_seo_template("join.html", "join")


# ============================================================
# MOVIES & MUSIC
# ============================================================

@app.route("/movies")
def movies():
    MOVIES = []
    return render_seo_template("movies.html", "movies", movies=MOVIES)


@app.route("/music")
def music():
    return render_seo_template("music.html", "music")


# ============================================================
# COLLABORATION
# ============================================================

class CollaborationForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    email = StringField("Email", validators=[DataRequired(), Email()])
    organization = StringField("Organization", validators=[DataRequired()])
    message = TextAreaField("Message", validators=[DataRequired()])


@app.route("/collaboration", methods=["GET", "POST"])
def collaboration():
    form = CollaborationForm()

    partners = [
        {"name": "Partner 1", "description": "Film Production House", "logo": "partner1.png"},
        {"name": "Partner 2", "description": "Event Management", "logo": "partner2.png"},
        {"name": "Partner 3", "description": "Cultural Foundation", "logo": "partner3.png"}
    ]

    if form.validate_on_submit():
        try:
            form_data = get_form_data()
            collaboration_file = request.files.get("file")

            if collaboration_file and collaboration_file.filename:
                if not allowed_file(collaboration_file.filename):
                    flash("Only PDF files are allowed for the collaboration attachment.", "danger")
                    return redirect(url_for("collaboration"))

                send_submission_email(
                    subject="New Collaboration Request",
                    form_data=form_data,
                    uploaded_file=collaboration_file,
                    uploaded_filename=collaboration_file.filename
                )
            else:
                if RESEND_API_KEY and RESEND_FROM_EMAIL:
                    resend.Emails.send({
                        "from": RESEND_FROM_EMAIL,
                        "to": [MAIL_RECEIVER],
                        "subject": "New Collaboration Request",
                        "html": f"""
                            <h2>New Collaboration Request</h2>
                            <p><strong>Name:</strong> {escape(form.name.data)}</p>
                            <p><strong>Email:</strong> {escape(form.email.data)}</p>
                            <p><strong>Organization:</strong> {escape(form.organization.data)}</p>
                            <p><strong>Message:</strong> {escape(form.message.data)}</p>
                        """
                    })

            flash("Your collaboration request has been sent.", "success")
            return redirect(url_for("collaboration"))

        except Exception as e:
            app.logger.exception("Collaboration email error: %s", e)
            flash("Unable to send your request right now.", "danger")

    return render_seo_template("collab.html", "collaboration", form=form, partners=partners)


# ============================================================
# BOOKING PAGE
# ============================================================

@app.route("/book")
def booking_page():
    with get_db() as db:
        expire_old_pending_bookings(db)
        shows = db.execute("""
            SELECT *
            FROM shows
            WHERE active = 1
            ORDER BY show_date, show_time
        """).fetchall()

        for show in shows:
            show["available_seats"] = get_available_seats(
                db,
                show["id"],
                show["capacity"]
            )
            show["available"] = show["available_seats"]

        db.commit()

    return render_seo_template("booking.html", "booking", shows=shows)


@app.route("/booking")
def booking():
    return booking_page()


# ============================================================
# BOOK SHOW
# ============================================================

@app.route("/book/<int:show_id>", methods=["GET", "POST"])
def book_show(show_id):

    with get_db() as db:

        show = db.execute("""
            SELECT *
            FROM shows
            WHERE id = %s
            AND active = 1
        """, (show_id,)).fetchone()

        if not show:
            flash("Show not found.", "danger")
            return redirect(url_for("booking_page"))

        if request.method == "GET":
            available_seats = get_available_seats(
                db,
                show["id"],
                show["capacity"]
            )
            db.commit()

            # FIX: Add available seats to the show dictionary
            # show["available_seats"] = available_seats
            # show["available"] = available_seats


            return render_template(
                "book_show.html",
                # shows=[show],
                show=show,
                available_seats=available_seats,
                available=available_seats
            )

        customer_name = request.form.get("customer_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        quantity_raw = request.form.get("quantity", "0")

        if not customer_name:
            flash("Please enter your name.", "danger")
            return redirect(url_for("book_show", show_id=show_id))

        if not phone:
            flash("Please enter your phone number.", "danger")
            return redirect(url_for("book_show", show_id=show_id))

        if not email:
            flash("Please enter your email.", "danger")
            return redirect(url_for("book_show", show_id=show_id))

        try:
            quantity = int(quantity_raw)
        except ValueError:
            flash("Invalid ticket quantity.", "danger")
            return redirect(url_for("book_show", show_id=show_id))

        if quantity <= 0:
            flash("Please select at least one ticket.", "danger")
            return redirect(url_for("book_show", show_id=show_id))

        show = db.execute("""
            SELECT *
            FROM shows
            WHERE id = %s
            AND active = 1
            FOR UPDATE
        """, (show_id,)).fetchone()

        if not show:
            flash("Show is no longer available.", "danger")
            return redirect(url_for("booking_page"))

        expire_old_pending_bookings(db)

        result = db.execute("""
            SELECT COALESCE(SUM(quantity), 0) AS reserved
            FROM bookings
            WHERE show_id = %s
            AND status IN ('PENDING', 'PAYMENT SUBMITTED', 'CONFIRMED')
        """, (show_id,)).fetchone()

        reserved = int(result["reserved"] or 0)
        available_seats = int(show["capacity"]) - reserved

        if quantity > available_seats:
            db.commit()
            flash(f"Only {available_seats} seat(s) are available.", "danger")
            return redirect(url_for("book_show", show_id=show_id))

        try:
            ticket_price = Decimal(str(show["ticket_price"])).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except Exception:
            app.logger.exception("Invalid ticket price for show %s", show_id)
            db.rollback()
            flash("This show's ticket price is invalid. Please contact the organiser.", "danger")
            return redirect(url_for("booking_page"))

        if not ticket_price.is_finite() or ticket_price <= 0:
            db.rollback()
            app.logger.error(
                "Cannot create booking for show %s: invalid ticket price %s",
                show_id, show.get("ticket_price")
            )
            flash("Online booking is unavailable for this show because its ticket price is invalid.", "danger")
            return redirect(url_for("booking_page"))

        total_amount = (ticket_price * quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        amount_paise = money_to_paise(total_amount)
        if amount_paise < 100:
            db.rollback()
            flash("The total booking amount must be at least ₹1.00 to pay online. Please select more tickets or contact the organiser.", "danger")
            return redirect(url_for("book_show", show_id=show_id))

        booking_code = generate_booking_code()
        created_at = current_timestamp_string()

        booking_row = db.execute("""
            INSERT INTO bookings (
                booking_code,
                show_id,
                customer_name,
                phone,
                email,
                quantity,
                total_amount,
                status,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING', %s)
            RETURNING *
        """, (
            booking_code,
            show_id,
            customer_name,
            phone,
            email,
            quantity,
            total_amount,
            created_at
        )).fetchone()

        db.commit()

    try:
        razorpay_order = create_razorpay_order(booking_row, show)
    except Exception as e:
        app.logger.exception(
            "Razorpay order creation failed for booking %s (show %s, total ₹%s): %s",
            booking_row.get("id"), show_id, total_amount, e
        )
        with get_db() as db:
            db.execute("""
                UPDATE bookings
                SET status = 'EXPIRED'
                WHERE id = %s
                  AND status = 'PENDING'
            """, (booking_row["id"],))
            db.commit()

        if isinstance(e, ValueError) and "at least ₹1.00" in str(e):
            flash("The booking total is below Razorpay's minimum amount. Please check the ticket price or quantity.", "danger")
        else:
            flash("Unable to start online payment right now. Please try again or contact Home Entertainments if money was deducted.", "danger")
        return redirect(url_for("book_show", show_id=show_id))

    with get_db() as db:
        db.execute("""
            UPDATE bookings
            SET razorpay_order_id = %s
            WHERE id = %s
        """, (
            razorpay_order["id"],
            booking_row["id"]
        ))
        db.commit()

        booking = db.execute("""
            SELECT *
            FROM bookings
            WHERE id = %s
        """, (booking_row["id"],)).fetchone()

    upi_url = generate_upi_payment_url(total_amount)

    razorpay_order_id = ""
    payment_error = ""
    if booking["status"] == "PENDING":
        try:
            razorpay_order_id = booking.get("razorpay_order_id", "")
            if not razorpay_order_id:
                raise RuntimeError("Razorpay order ID is missing for this booking.")
            booking = dict(booking)
            booking["razorpay_order_id"] = razorpay_order_id
        except Exception:
            app.logger.exception("Could not create Razorpay order for booking %s", booking["id"])
            payment_error = "Online payment is temporarily unavailable. Please try again shortly."

    return render_template(
        "booking_payment.html",
        booking=booking,
        show=show,
        razorpay_amount_paise=money_to_paise(total_amount),
        upi_url=upi_url,
        upi_id=UPI_ID,
        pending_minutes=PENDING_BOOKING_MINUTES,
        razorpay_key_id=RAZORPAY_KEY_ID,
        razorpay_order_id=razorpay_order_id,
        payment_error=payment_error
    )


# ============================================================
# PAYMENT PAGE (FIXED TO INCLUDE RAZORPAY KEYS)
# ============================================================

@app.route("/book/payment/<int:booking_id>", methods=["GET"])
def booking_payment(booking_id):
    with get_db() as db:
        booking = db.execute("""
            SELECT *
            FROM bookings
            WHERE id = %s
        """, (booking_id,)).fetchone()

        if not booking:
            flash("Booking not found.", "danger")
            return redirect(url_for("booking_page"))

        if booking["status"] == "CONFIRMED":
            return redirect(url_for("booking_success", booking_id=booking_id))

        if (
            booking["status"] == "EXPIRED"
            or (booking["status"] == "PENDING" and is_booking_expired(booking["created_at"]))
        ):
            db.execute("""
                UPDATE bookings
                SET status = 'EXPIRED'
                WHERE id = %s
                AND status = 'PENDING'
            """, (booking_id,))
            db.commit()

            flash("This booking has expired. Please create a new booking.", "danger")
            return redirect(url_for("booking_page"))

        show = db.execute("""
            SELECT *
            FROM shows
            WHERE id = %s
        """, (booking["show_id"],)).fetchone()

    upi_url = generate_upi_payment_url(booking["total_amount"])

    return render_template(
        "booking_payment.html",
        booking=booking,
        show=show,
        upi_url=upi_url,
        upi_id=UPI_ID,
        pending_minutes=PENDING_BOOKING_MINUTES,
        razorpay_key_id=RAZORPAY_KEY_ID,
        razorpay_order_id=booking.get("razorpay_order_id", ""),
        razorpay_amount_paise=money_to_paise(booking["total_amount"]),
        payment_error=""
    )



# ============================================================
# SUBMIT UTR / PAYMENT
# ============================================================

@app.route("/book/payment/<int:booking_id>", methods=["POST"])
def submit_booking_payment(booking_id):
    utr = request.form.get("utr", "").strip()

    if not utr or len(utr) < 6:
        flash("Please enter a valid UTR / Transaction ID.", "danger")
        return redirect(url_for("booking_payment", booking_id=booking_id))

    with get_db() as db:
        booking = db.execute("""
            SELECT *
            FROM bookings
            WHERE id = %s
            FOR UPDATE
        """, (booking_id,)).fetchone()

        if not booking:
            flash("Booking not found.", "danger")
            return redirect(url_for("booking_page"))

        if booking["status"] == "PENDING" and is_booking_expired(booking["created_at"]):
            db.execute("""
                UPDATE bookings
                SET status = 'EXPIRED'
                WHERE id = %s
            """, (booking_id,))
            db.commit()

            flash("Your booking reservation has expired. Please start a new booking.", "danger")
            return redirect(url_for("booking_page"))

        if booking["status"] == "PAYMENT SUBMITTED":
            flash("Payment details have already been submitted.", "warning")
            return redirect(url_for("booking_payment", booking_id=booking_id))

        if booking["status"] in {"CONFIRMED", "CANCELLED", "EXPIRED"}:
            flash("This booking can no longer be modified.", "danger")
            return redirect(url_for("booking_page"))

        payment_submitted_at = current_timestamp_string()

        db.execute("""
            UPDATE bookings
            SET
                utr = %s,
                payment_submitted_at = %s,
                status = 'PAYMENT SUBMITTED'
            WHERE id = %s
        """, (utr, payment_submitted_at, booking_id))
        db.commit()

        booking = db.execute("SELECT * FROM bookings WHERE id = %s", (booking_id,)).fetchone()
        show = db.execute("SELECT * FROM shows WHERE id = %s", (booking["show_id"],)).fetchone()

    try:
        send_booking_confirmation_email(booking, show)
    except Exception as e:
        app.logger.exception("Booking payment email failed: %s", e)

    return render_template("booking_success.html", booking=booking, show=show)


# ============================================================
# VERIFY RAZORPAY PAYMENT
# ============================================================

@app.route("/book/payment/<int:booking_id>/verify", methods=["POST"])
def verify_razorpay_payment(booking_id):
    razorpay_payment_id = request.form.get("razorpay_payment_id", "").strip()
    razorpay_order_id = request.form.get("razorpay_order_id", "").strip()
    razorpay_signature = request.form.get("razorpay_signature", "").strip()

    if not all([razorpay_payment_id, razorpay_order_id, razorpay_signature]):
        flash("Payment verification information is incomplete.", "danger")
        return redirect(url_for("booking_payment", booking_id=booking_id))

    try:
        client = get_razorpay_client()

        with get_db() as db:
            booking = db.execute("""
                SELECT *
                FROM bookings
                WHERE id = %s
                FOR UPDATE
            """, (booking_id,)).fetchone()

            if not booking:
                flash("Booking not found.", "danger")
                return redirect(url_for("booking_page"))

            if booking["status"] == "CONFIRMED":
                return redirect(url_for("booking_success", booking_id=booking_id))

            if booking["status"] != "PENDING":
                flash("This booking is no longer available for payment.", "danger")
                return redirect(url_for("booking_page"))

            if is_booking_expired(booking["created_at"]):
                db.execute("""
                    UPDATE bookings
                    SET status = 'EXPIRED'
                    WHERE id = %s
                    AND status = 'PENDING'
                """, (booking_id,))
                db.commit()

                flash("This booking has expired.", "danger")
                return redirect(url_for("booking_page"))

            if razorpay_order_id != booking["razorpay_order_id"]:
                flash("Invalid payment order.", "danger")
                return redirect(url_for("booking_page"))

            client.utility.verify_payment_signature({
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature
            })

            payment = client.payment.fetch(razorpay_payment_id)

            expected_amount = money_to_paise(booking["total_amount"])
            received_amount = int(payment.get("amount", 0))
            received_currency = payment.get("currency", "")
            received_order_id = payment.get("order_id", "")
            payment_status = payment.get("status", "")

            if received_order_id != booking["razorpay_order_id"]:
                flash("Payment order verification failed.", "danger")
                return redirect(url_for("booking_payment", booking_id=booking_id))

            if received_amount != expected_amount:
                flash("Payment amount verification failed.", "danger")
                return redirect(url_for("booking_payment", booking_id=booking_id))

            if received_currency != "INR":
                flash("Payment currency verification failed.", "danger")
                return redirect(url_for("booking_payment", booking_id=booking_id))

            if payment_status != "captured":
                flash("Payment has not been captured yet.", "danger")
                return redirect(url_for("booking_payment", booking_id=booking_id))

            payment_method = payment.get("method", "")

            db.execute("""
                UPDATE bookings
                SET
                    razorpay_payment_id = %s,
                    razorpay_signature = %s,
                    payment_verified_at = %s,
                    payment_method = %s,
                    status = 'CONFIRMED'
                WHERE id = %s
                AND status = 'PENDING'
            """, (
                razorpay_payment_id,
                razorpay_signature,
                current_timestamp_string(),
                payment_method,
                booking_id
            ))
            db.commit()

        return redirect(url_for("booking_success", booking_id=booking_id))

    except razorpay.errors.SignatureVerificationError:
        app.logger.error("Razorpay signature verification failed.")
        flash("Payment verification failed. Please contact support if money was deducted.", "danger")
        return redirect(url_for("booking_payment", booking_id=booking_id))

    except Exception as e:
        app.logger.exception("Razorpay verification error: %s", e)
        flash("Unable to verify the payment right now. If money was deducted, please contact support.", "danger")
        return redirect(url_for("booking_payment", booking_id=booking_id))


# ============================================================
# RAZORPAY WEBHOOK
# ============================================================

@app.route("/razorpay/webhook", methods=["POST"])
def razorpay_webhook():
    if not RAZORPAY_WEBHOOK_SECRET:
        return Response("Webhook secret not configured", status=500)

    raw_body = request.get_data()
    received_signature = request.headers.get("X-Razorpay-Signature", "")

    if not received_signature:
        return Response("Missing signature", status=400)

    expected_signature = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, received_signature):
        return Response("Invalid signature", status=400)

    payload = request.get_json(silent=True) or {}
    event = payload.get("event", "")

    if event == "payment.captured":
        try:
            payment_entity = (
                payload.get("payload", {})
                .get("payment", {})
                .get("entity", {})
            )

            razorpay_payment_id = payment_entity.get("id", "")
            razorpay_order_id = payment_entity.get("order_id", "")
            amount = int(payment_entity.get("amount", 0))
            currency = payment_entity.get("currency", "")
            payment_method = payment_entity.get("method", "")

            if not razorpay_payment_id or not razorpay_order_id:
                return Response("Missing identifiers", status=400)

            with get_db() as db:
                booking = db.execute("""
                    SELECT *
                    FROM bookings
                    WHERE razorpay_order_id = %s
                    FOR UPDATE
                """, (razorpay_order_id,)).fetchone()

                if not booking:
                    return Response("Booking not found", status=200)

                expected_amount = money_to_paise(booking["total_amount"])

                if amount != expected_amount or currency != "INR":
                    return Response("Validation mismatch", status=400)

                db.execute("""
                    UPDATE bookings
                    SET
                        razorpay_payment_id = %s,
                        payment_verified_at = %s,
                        payment_method = %s,
                        status = 'CONFIRMED'
                    WHERE id = %s
                    AND status <> 'CONFIRMED'
                """, (
                    razorpay_payment_id,
                    current_timestamp_string(),
                    payment_method,
                    booking["id"]
                ))
                db.commit()

        except Exception as e:
            app.logger.exception("Razorpay webhook error: %s", e)
            return Response("Webhook processing error", status=500)

    return Response("OK", status=200)


# ============================================================
# BOOKING SUCCESS
# ============================================================

@app.route("/book/success/<int:booking_id>")
def booking_success(booking_id):
    with get_db() as db:
        booking = db.execute("""
            SELECT
                b.*,
                s.title,
                s.theatre,
                s.location,
                s.show_date,
                s.show_time,
                s.ticket_price
            FROM bookings b
            JOIN shows s
                ON s.id = b.show_id
            WHERE b.id = %s
        """, (booking_id,)).fetchone()

    if not booking:
        flash("Booking not found.", "danger")
        return redirect(url_for("booking_page"))

    return render_template("booking_success.html", booking=booking)


# ============================================================
# ADMIN LOGIN / LOGOUT
# ============================================================

@app.route("/login", methods=["GET", "POST"])
@app.route("/admin/login", methods=["GET", "POST"])
@app.route("/admin/bookings/login", methods=["GET", "POST"])
@app.route("/booking-admin/login", methods=["GET", "POST"])
def booking_admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if username == BOOKING_ADMIN_USERNAME and password == BOOKING_ADMIN_PASSWORD:
            session["booking_admin_logged_in"] = True
            session["booking_admin"] = True
            return redirect(url_for("booking_admin"))

        flash("Invalid admin credentials.", "danger")

    return render_template("booking_admin_login.html")


@app.route("/logout")
@app.route("/admin/logout")
@app.route("/booking-admin/logout")
def booking_admin_logout():
    session.pop("booking_admin", None)
    session.pop("booking_admin_logged_in", None)
    return redirect(url_for("booking_admin_login"))


# ============================================================
# ADMIN DASHBOARD
# ============================================================

@app.route("/admin")
@app.route("/admin/bookings")
@app.route("/booking-admin", methods=["GET", "POST"])
@admin_required
def booking_admin():
    with get_db() as db:
        expire_old_pending_bookings(db)

        shows = db.execute("""
            SELECT *
            FROM shows
            ORDER BY show_date DESC, show_time DESC
        """).fetchall()

        shows_list = []
        for row in shows:
            show = dict(row)
            booked_row = db.execute("""
                SELECT COALESCE(SUM(quantity), 0) AS booked
                FROM bookings
                WHERE show_id = %s
                AND status IN ('PENDING', 'PAYMENT SUBMITTED', 'CONFIRMED')
            """, (show["id"],)).fetchone()

            show["booked"] = int(booked_row["booked"] or 0)
            show["available"] = max(0, int(show["capacity"]) - show["booked"])
            shows_list.append(show)

        bookings = db.execute("""
            SELECT
                b.*,
                s.title,
                s.theatre,
                s.location,
                s.show_date,
                s.show_time
            FROM bookings b
            JOIN shows s
                ON s.id = b.show_id
            ORDER BY b.created_at DESC
        """).fetchall()

        db.commit()

    return render_template(
        "booking_admin.html",
        shows=shows_list,
        bookings=bookings
    )


# ============================================================
# ADD SHOW ROUTE
# ============================================================

@app.route("/admin/shows/add", methods=["GET", "POST"])
@app.route("/admin/bookings/shows/add", methods=["GET", "POST"])
@app.route("/booking-admin/shows/add", methods=["GET", "POST"])
@admin_required
def booking_admin_add_show():
    if request.method == "GET":
        return render_template("booking_admin_show.html", show=None)

    title = request.form.get("title", "").strip()
    theatre = request.form.get("theatre", "").strip()
    location = request.form.get("location", "").strip()
    show_date = request.form.get("show_date", "").strip()
    show_time = request.form.get("show_time", "").strip()
    ticket_price_raw = request.form.get("ticket_price", "0").strip()
    capacity_raw = request.form.get("capacity", "0").strip()
    payment_link = request.form.get("payment_link", "").strip()

    try:
        ticket_price = Decimal(ticket_price_raw).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )
        capacity = int(capacity_raw)
    except Exception:
        flash("Invalid ticket price or capacity.", "danger")
        return redirect(url_for("booking_admin_add_show"))

    if not title or not theatre or not location or not show_date or not show_time:
        flash("Please fill all required fields.", "danger")
        return redirect(url_for("booking_admin_add_show"))

    if capacity <= 0:
        flash("Capacity must be greater than zero.", "danger")
        return redirect(url_for("booking_admin_add_show"))

    with get_db() as db:
        db.execute("""
            INSERT INTO shows (
                title,
                theatre,
                location,
                show_date,
                show_time,
                ticket_price,
                capacity,
                payment_link,
                active,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s)
        """, (
            title,
            theatre,
            location,
            show_date,
            show_time,
            ticket_price,
            capacity,
            payment_link,
            current_timestamp_string()
        ))
        db.commit()

    flash("Show added successfully.", "success")
    return redirect(url_for("booking_admin"))


def add_booking_show():
    return booking_admin_add_show()


# ============================================================
# EDIT / UPDATE SHOW ROUTE
# ============================================================

@app.route("/admin/shows/edit/<int:show_id>", methods=["GET", "POST"])
@app.route("/admin/bookings/shows/<int:show_id>/update", methods=["GET", "POST"])
@admin_required
def booking_admin_edit_show(show_id):
    with get_db() as db:
        show = db.execute("SELECT * FROM shows WHERE id = %s", (show_id,)).fetchone()

    if not show:
        flash("Show not found.", "danger")
        return redirect(url_for("booking_admin"))

    if request.method == "GET":
        return render_template("booking_admin_show.html", show=show)

    title = request.form.get("title", "").strip()
    theatre = request.form.get("theatre", "").strip()
    location = request.form.get("location", "").strip()
    show_date = request.form.get("show_date", "").strip()
    show_time = request.form.get("show_time", "").strip()
    ticket_price_raw = request.form.get("ticket_price", "0").strip()
    capacity_raw = request.form.get("capacity", "0").strip()
    payment_link = request.form.get("payment_link", "").strip()
    active = 1 if request.form.get("active", "1") in ["1", "on", True] else 0

    try:
        ticket_price = Decimal(ticket_price_raw).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )
        capacity = int(capacity_raw)
    except Exception:
        flash("Invalid show details.", "danger")
        return redirect(url_for("booking_admin_edit_show", show_id=show_id))

    with get_db() as db:
        db.execute("""
            UPDATE shows
            SET
                title = %s,
                theatre = %s,
                location = %s,
                show_date = %s,
                show_time = %s,
                ticket_price = %s,
                capacity = %s,
                payment_link = %s,
                active = %s
            WHERE id = %s
        """, (
            title,
            theatre,
            location,
            show_date,
            show_time,
            ticket_price,
            capacity,
            payment_link,
            active,
            show_id
        ))
        db.commit()

    flash("Show updated successfully.", "success")
    return redirect(url_for("booking_admin"))


def update_booking_show(show_id):
    return booking_admin_edit_show(show_id)


# ============================================================
# TOGGLE SHOW STATUS
# ============================================================

@app.route("/admin/shows/toggle/<int:show_id>", methods=["GET", "POST"])
@admin_required
def booking_admin_toggle_show(show_id):
    with get_db() as db:
        show = db.execute("SELECT * FROM shows WHERE id = %s", (show_id,)).fetchone()
        if not show:
            flash("Show not found.", "danger")
            return redirect(url_for("booking_admin"))

        new_status = 0 if show["active"] == 1 else 1
        db.execute("UPDATE shows SET active = %s WHERE id = %s", (new_status, show_id))
        db.commit()

    flash("Show status toggled successfully.", "info")
    return redirect(url_for("booking_admin"))


# ============================================================
# DELETE SHOW
# ============================================================

@app.route("/admin/bookings/shows/<int:show_id>/delete", methods=["POST"])
@admin_required
def delete_booking_show(show_id):
    with get_db() as db:
        db.execute("DELETE FROM shows WHERE id = %s", (show_id,))
        db.commit()

    flash("Show deleted.", "success")
    return redirect(url_for("booking_admin"))


# ============================================================
# UPDATE BOOKING STATUS ROUTE
# ============================================================

@app.route("/admin/bookings/update-status/<int:booking_id>", methods=["POST"])
@app.route("/admin/bookings/<int:booking_id>/status", methods=["POST"])
@admin_required
def booking_admin_update_booking_status(booking_id):
    status = request.form.get("status", "").strip().upper()
    allowed_statuses = {"PENDING", "PAYMENT SUBMITTED", "CONFIRMED", "CANCELLED", "EXPIRED"}

    if status not in allowed_statuses:
        flash("Invalid booking status.", "danger")
        return redirect(url_for("booking_admin"))

    with get_db() as db:
        booking = db.execute("""
            SELECT *
            FROM bookings
            WHERE id = %s
            FOR UPDATE
        """, (booking_id,)).fetchone()

        if not booking:
            flash("Booking not found.", "danger")
            return redirect(url_for("booking_admin"))

        if status == "CONFIRMED" and not booking["razorpay_payment_id"] and not booking["utr"]:
            flash(
                "This booking cannot be confirmed because no payment verification or UTR was found.",
                "danger"
            )
            return redirect(url_for("booking_admin"))

        db.execute("""
            UPDATE bookings
            SET status = %s
            WHERE id = %s
        """, (status, booking_id))
        db.commit()

    flash("Booking status updated.", "success")
    return redirect(url_for("booking_admin"))


def update_booking_status(booking_id):
    return booking_admin_update_booking_status(booking_id)


# ============================================================
# STATIC / UPLOADS
# ============================================================

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    upload_folder = os.path.join(app.root_path, app.config["UPLOAD_FOLDER"])
    return send_from_directory(upload_folder, filename)


# ============================================================
# GOOGLE CALENDAR PLACEHOLDER ROUTES
# ============================================================

@app.route("/authorize")
def authorize():
    return "Authorization endpoint configured."


@app.route("/oauth2callback")
def oauth2callback():
    return "OAuth callback configured."


# ============================================================
# APP STARTUP
# ============================================================

with app.app_context():
    try:
        init_booking_db()
        print("Booking database initialized.")
    except Exception as e:
        print("Database initialization error:", e)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)