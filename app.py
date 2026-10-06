import os
import pickle
import datetime
import secrets
from functools import wraps
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_from_directory,
    Response
)

from werkzeug.utils import secure_filename
from markupsafe import escape

import resend
from dotenv import load_dotenv

from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField
from wtforms.validators import DataRequired, Email

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build


# ============================================================
# Required for local Google OAuth development.
# For production, use HTTPS and remove/disable this.
# ============================================================

os.environ.setdefault(
    "OAUTHLIB_INSECURE_TRANSPORT",
    "1"
)

load_dotenv()

app = Flask(__name__)


# ============================================================
# APP CONFIG
# ============================================================

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
# RESEND EMAIL API CONFIGURATION
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
# THEATRE BOOKING CONFIGURATION
# ============================================================

# PostgreSQL connection string.
#
# On Render:
# DATABASE_URL = your PostgreSQL Internal Database URL
#
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
        raise RuntimeError(
            "DATABASE_URL is not configured. "
            "Please add your PostgreSQL connection URL "
            "to the Render environment variables."
        )

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
    )


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
# TIME HELPERS
# ============================================================

def current_datetime():
    """
    Return current UTC time as a naive ISO-compatible datetime.

    The database stores timestamps as TEXT to keep compatibility
    with the existing application structure.
    """
    return datetime.datetime.now(
        datetime.timezone.utc
    ).replace(
        tzinfo=None
    )


def current_timestamp_string():
    """
    Return current UTC timestamp as an ISO string.
    """
    return current_datetime().isoformat(
        timespec="seconds"
    )


def parse_booking_datetime(value):
    """
    Parse a booking timestamp stored in the database.
    """
    if not value:
        return None

    try:
        parsed = datetime.datetime.fromisoformat(
            value
        )

        # Handle timestamps that may contain timezone data.
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(
                datetime.timezone.utc
            ).replace(
                tzinfo=None
            )

        return parsed

    except (TypeError, ValueError):
        return None


# ============================================================
# EXPIRED PENDING BOOKINGS
# ============================================================

def expire_old_pending_bookings(db):
    """
    Mark PENDING bookings older than PENDING_BOOKING_MINUTES
    as EXPIRED.

    EXPIRED bookings no longer reserve seats.
    """

    cutoff = (
        current_datetime()
        - datetime.timedelta(
            minutes=PENDING_BOOKING_MINUTES
        )
    )

    cutoff_string = cutoff.isoformat(
        timespec="seconds"
    )

    db.execute("""
        UPDATE bookings
        SET status = 'EXPIRED'
        WHERE status = 'PENDING'
          AND created_at < %s
    """, (cutoff_string,))


# ============================================================
# SEAT AVAILABILITY
# ============================================================

def booking_available_seats(
    db,
    show_id,
    capacity
):
    """
    Return seats still available.

    Only PENDING and CONFIRMED bookings reserve seats.

    Old PENDING bookings are first marked EXPIRED.
    """

    expire_old_pending_bookings(db)

    row = db.execute("""
        SELECT COALESCE(
            SUM(quantity),
            0
        ) AS booked
        FROM bookings
        WHERE show_id = %s
          AND status IN (
              'PENDING',
              'PAYMENT SUBMITTED',
              'CONFIRMED'
          )
    """, (show_id,)).fetchone()

    booked = int(
        row["booked"] or 0
    )

    return max(
        0,
        int(capacity) - booked
    )


# ============================================================
# GENERATE BOOKING CODE
# ============================================================

def generate_booking_code():
    """
    Generate a short booking reference.
    """
    return (
        "CC-"
        + secrets.token_hex(4).upper()
    )


# ============================================================
# GENERATE UPI PAYMENT URL
# ============================================================

def generate_upi_payment_url(amount):
    """
    Generate a dynamic UPI payment URL.

    Example:
    ₹450 → upi://pay?...&am=450.00&cu=INR
    """

    payee_name = quote(
        UPI_NAME,
        safe=""
    )

    # Keep @ unescaped for better compatibility with UPI apps.
    upi_id = quote(
        UPI_ID,
        safe="@"
    )

    amount_value = f"{float(amount):.2f}"

    return (
        "upi://pay?"
        f"pa={upi_id}"
        f"&pn={payee_name}"
        f"&am={amount_value}"
        "&cu=INR"
    )


# ============================================================
# BOOKING ADMIN AUTHENTICATION
# ============================================================

def booking_admin_required():
    """
    Protect booking-admin pages.
    """

    if not session.get(
        "booking_admin"
    ):
        return redirect(
            url_for(
                "booking_admin_login"
            )
        )

    return None


# ============================================================
# BOOKING EMAIL
# ============================================================

def send_booking_confirmation_email(
    booking,
    show
):
    """
    Send a customer booking email.

    The email wording changes according to booking status.
    """

    if (
        not RESEND_API_KEY
        or not RESEND_FROM_EMAIL
    ):
        return

    customer_email = booking["email"]

    status = booking["status"]

    if status == "CONFIRMED":

        email_subject = (
            f"C&C Booking Confirmed - "
            f"{booking['booking_code']}"
        )

        main_message = (
            "Your payment has been verified "
            "and your booking is confirmed."
        )

    elif status == "PAYMENT SUBMITTED":

        email_subject = (
            f"C&C Payment Submitted - "
            f"{booking['booking_code']}"
        )

        main_message = (
            "Your payment details have been "
            "submitted successfully. Our team "
            "will verify the payment and confirm "
            "your booking."
        )

    elif status == "CANCELLED":

        email_subject = (
            f"C&C Booking Cancelled - "
            f"{booking['booking_code']}"
        )

        main_message = (
            "Your booking has been cancelled. "
            "Please contact Home Entertainments "
            "if you need assistance."
        )

    elif status == "EXPIRED":

        email_subject = (
            f"C&C Booking Expired - "
            f"{booking['booking_code']}"
        )

        main_message = (
            "Your pending booking expired because "
            "payment details were not submitted "
            "within the reservation period."
        )

    else:

        email_subject = (
            f"C&C Booking Received - "
            f"{booking['booking_code']}"
        )

        main_message = (
            "Your theatre booking has been "
            "received successfully. Please "
            "complete the payment process."
        )

    utr_value = (
        booking["utr"]
        if booking["utr"]
        else "Not submitted"
    )

    html = f"""
    <div style="
        font-family:Arial,sans-serif;
        max-width:650px;
        margin:auto;
    ">

        <h2 style="color:#b89020;">
            Home Entertainments
        </h2>

        <h3>
            C&C – Chiru & Charu | Booking
        </h3>

        <p>
            Hi {escape(booking["customer_name"])},
        </p>

        <p>
            {escape(main_message)}
        </p>

        <table
            cellpadding="8"
            cellspacing="0"
            style="
                border-collapse:collapse;
                width:100%;
            "
        >

            <tr>
                <td>
                    <strong>Booking ID</strong>
                </td>
                <td>
                    {escape(booking["booking_code"])}
                </td>
            </tr>

            <tr>
                <td>
                    <strong>Show</strong>
                </td>
                <td>
                    {escape(show["title"])}
                </td>
            </tr>

            <tr>
                <td>
                    <strong>Theatre</strong>
                </td>
                <td>
                    {escape(show["theatre"])}
                </td>
            </tr>

            <tr>
                <td>
                    <strong>Location</strong>
                </td>
                <td>
                    {escape(show["location"])}
                </td>
            </tr>

            <tr>
                <td>
                    <strong>Date</strong>
                </td>
                <td>
                    {escape(show["show_date"])}
                </td>
            </tr>

            <tr>
                <td>
                    <strong>Time</strong>
                </td>
                <td>
                    {escape(show["show_time"])}
                </td>
            </tr>

            <tr>
                <td>
                    <strong>Tickets</strong>
                </td>
                <td>
                    {booking["quantity"]}
                </td>
            </tr>

            <tr>
                <td>
                    <strong>Total</strong>
                </td>
                <td>
                    ₹{float(booking["total_amount"]):.2f}
                </td>
            </tr>

            <tr>
                <td>
                    <strong>Status</strong>
                </td>
                <td>
                    {escape(status)}
                </td>
            </tr>

            <tr>
                <td>
                    <strong>UTR / Transaction ID</strong>
                </td>
                <td>
                    {escape(utr_value)}
                </td>
            </tr>

        </table>

        <p>
            Please keep your Booking ID for reference.
        </p>

        <p>
            Regards,<br>
            <strong>Home Entertainments</strong>
        </p>

    </div>
    """

    resend.Emails.send({
        "from": RESEND_FROM_EMAIL,
        "to": [customer_email],
        "subject": email_subject,
        "html": html
    })


# ============================================================
# CREATE DATABASE
# ============================================================

init_booking_db()


# ============================================================
# WEBSITE SEO INFORMATION
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


# ============================================================
# SEO DATA
# ============================================================

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


# ============================================================
# SEO RENDER HELPER
# ============================================================

def render_seo_template(
    template_name,
    seo_key,
    **context
):

    seo = SEO_DATA.get(
        seo_key,
        {}
    )

    context["title"] = seo.get(
        "title",
        f"{SITE_NAME} | Kannada Entertainment"
    )

    context["description"] = seo.get(
        "description",
        SITE_DESCRIPTION
    )

    return render_template(
        template_name,
        **context
    )


# ============================================================
# GENERAL HELPER FUNCTIONS
# ============================================================

def allowed_file(filename):

    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(
            ".",
            1
        )[1].lower()
        in ALLOWED_UPLOAD_EXTENSIONS
    )


def get_form_data():

    data = {}

    for key, value in request.form.items():

        if key.lower() in {
            "csrf_token",
            "submit"
        }:
            continue

        value = (
            value or ""
        ).strip()

        if value:

            data[
                key.replace(
                    "_",
                    " "
                ).title()
            ] = value

    return data


# ============================================================
# RESEND WEBSITE FORM EMAIL
# ============================================================

def send_submission_email(
    subject,
    form_data,
    uploaded_file=None,
    uploaded_filename=None
):

    if not RESEND_API_KEY:

        raise RuntimeError(
            "RESEND_API_KEY is not configured."
        )

    if not RESEND_FROM_EMAIL:

        raise RuntimeError(
            "RESEND_FROM_EMAIL is not configured."
        )

    if not MAIL_RECEIVER:

        raise RuntimeError(
            "MAIL_RECEIVER is not configured."
        )

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

        text_lines.append(
            f"{field}: {value}"
        )

    text_lines.extend([
        "-----------------------------------",
        "",
        "Submitted from the Home Entertainments website."
    ])

    plain_text_body = "\n".join(
        text_lines
    )

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

        <meta
            name="viewport"
            content="width=device-width, initial-scale=1.0"
        >

        <title>
            {escape(subject)}
        </title>

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
                background: linear-gradient(
                    135deg,
                    #111827,
                    #374151
                );
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

                <h1 style="
                    margin: 0;
                    font-size: 25px;
                    font-weight: 600;
                ">

                    New Form Submission

                </h1>

                <p style="
                    margin: 8px 0 0;
                    font-size: 14px;
                    color: #d1d5db;
                ">

                    {escape(subject)}

                </p>

            </div>

            <div style="
                padding: 30px;
            ">

                <h2 style="
                    margin: 0 0 18px;
                    font-size: 18px;
                    color: #111827;
                ">

                    Submission Details

                </h2>

                <table
                    width="100%"
                    cellpadding="0"
                    cellspacing="0"
                    style="
                        border: 1px solid #e5e7eb;
                        border-radius: 8px;
                        border-spacing: 0;
                        overflow: hidden;
                        font-size: 14px;
                    "
                >

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

                    This message was submitted through the
                    <strong>Home Entertainments</strong>
                    website.

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

        "to": [
            MAIL_RECEIVER
        ],

        "subject": subject,

        "html": html_body,

        "text": plain_text_body
    }

    if visitor_email:

        params["reply_to"] = [
            visitor_email
        ]

    if (
        uploaded_file
        and uploaded_filename
    ):

        uploaded_file.stream.seek(0)

        file_data = list(
            uploaded_file.read()
        )

        params["attachments"] = [

            {
                "filename":
                    secure_filename(
                        uploaded_filename
                    ),

                "content":
                    file_data
            }

        ]

    response = resend.Emails.send(
        params
    )

    app.logger.info(
        "Email sent successfully through Resend: %s",
        response
    )

    return response


# ============================================================
# COLLABORATION FORM
# ============================================================

class CollaborationForm(FlaskForm):

    name = StringField(
        "Name",
        validators=[
            DataRequired()
        ]
    )

    email = StringField(
        "Email",
        validators=[
            DataRequired(),
            Email()
        ]
    )

    organization = StringField(
        "Organization"
    )

    message = TextAreaField(
        "Message",
        validators=[
            DataRequired()
        ]
    )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return render_seo_template(
        "home.html",
        "home"
    )


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
            "bio": (
                "Award-winning actor with "
                "10+ years of experience in cinema."
            ),
            "social": {
                "instagram": "#",
                "twitter": "#",
                "linkedin": "#"
            }
        },

        {
            "name": "Jane Smith",
            "role": "Director",
            "image": "jane.jpg",
            "bio": (
                "Creative director shaping "
                "unique storytelling experiences."
            ),
            "social": {
                "instagram": "#",
                "twitter": "#",
                "linkedin": "#"
            }
        },

        {
            "name": "John Doe",
            "role": "Actor",
            "image": "john.jpg",
            "bio": (
                "Award-winning actor with "
                "10+ years of experience in cinema."
            ),
            "social": {
                "instagram": "#",
                "twitter": "#",
                "linkedin": "#"
            }
        },

        {
            "name": "John Doe",
            "role": "Actor",
            "image": "john.jpg",
            "bio": (
                "Award-winning actor with "
                "10+ years of experience in cinema."
            ),
            "social": {
                "instagram": "#",
                "twitter": "#",
                "linkedin": "#"
            }
        },

        {
            "name": "John Doe",
            "role": "Actor",
            "image": "john.jpg",
            "bio": (
                "Award-winning actor with "
                "10+ years of experience in cinema."
            ),
            "social": {
                "instagram": "#",
                "twitter": "#",
                "linkedin": "#"
            }
        }

    ]

    return render_seo_template(
        "team.html",
        "team",
        team_members=team_members
    )


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
                "linkedin": (
                    "https://www.linkedin.com/in/"
                    "agraharam-kodanda-ram-0a4991297/"
                )
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
                "instagram": (
                    "https://www.instagram.com/"
                    "bhargavi.s.babu/"
                ),
                "facebook": (
                    "https://www.facebook.com/"
                    "bhargavi.babu.3/"
                ),
                "linkedin": (
                    "https://www.linkedin.com/in/"
                    "bhargavi-s-babu-85188b173/"
                )
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
                "instagram": (
                    "https://www.instagram.com/"
                    "harshith_b_gowda/"
                ),
                "facebook": (
                    "https://www.facebook.com/"
                    "harshith.bgowda.9/"
                ),
                "linkedin": (
                    "https://www.linkedin.com/in/"
                    "harshith-b-gowda-b10670206/"
                )
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

    return render_seo_template(
        "about.html",
        "about",
        team_members=team_members
    )


# ============================================================
# CONTACT
# ============================================================

@app.route(
    "/contact",
    methods=["GET", "POST"]
)
def contact():

    if request.method == "POST":

        try:

            form_data = get_form_data()

            send_submission_email(
                subject="New Contact Form Submission",
                form_data=form_data
            )

            flash(
                "Thank you! Your message has been sent successfully.",
                "success"
            )

        except Exception as e:

            app.logger.exception(
                "Contact email failed: %s",
                e
            )

            flash(
                "There was an issue sending your message. Please try again.",
                "danger"
            )

        return redirect(
            url_for("contact")
        )

    return render_seo_template(
        "contact.html",
        "contact"
    )


# ============================================================
# JOIN US
# ============================================================

@app.route(
    "/join",
    methods=["GET", "POST"]
)
def join():

    if request.method == "POST":

        try:

            form_data = get_form_data()

            resume = request.files.get(
                "resume"
            )

            if (
                resume
                and resume.filename
            ):

                if not allowed_file(
                    resume.filename
                ):

                    flash(
                        "Only PDF files are allowed for the resume.",
                        "danger"
                    )

                    return redirect(
                        url_for("join")
                    )

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

            flash(
                "Your submission has been sent successfully!",
                "success"
            )

        except Exception as e:

            app.logger.exception(
                "Join Us email failed: %s",
                e
            )

            flash(
                "There was an issue sending your submission.",
                "danger"
            )

        return redirect(
            url_for("join")
        )

    return render_seo_template(
        "join.html",
        "join"
    )


# ============================================================
# COLLABORATION
# ============================================================

@app.route(
    "/collaboration",
    methods=["GET", "POST"]
)
def collaboration():

    form = CollaborationForm()

    partners = [

        {
            "name": "Partner 1",
            "description": "Film Production House",
            "logo": "partner1.png"
        },

        {
            "name": "Partner 2",
            "description": "Event Management",
            "logo": "partner2.png"
        },

        {
            "name": "Partner 3",
            "description": "Cultural Foundation",
            "logo": "partner3.png"
        }

    ]

    if form.validate_on_submit():

        try:

            form_data = get_form_data()

            collaboration_file = request.files.get(
                "file"
            )

            if (
                collaboration_file
                and collaboration_file.filename
            ):

                if not allowed_file(
                    collaboration_file.filename
                ):

                    flash(
                        "Only PDF files are allowed for the collaboration attachment.",
                        "danger"
                    )

                    return redirect(
                        url_for("collaboration")
                    )

                send_submission_email(
                    subject="New Collaboration Request",
                    form_data=form_data,
                    uploaded_file=collaboration_file,
                    uploaded_filename=collaboration_file.filename
                )

            else:

                send_submission_email(
                    subject="New Collaboration Request",
                    form_data=form_data
                )

            flash(
                "Thank you! Your collaboration request has been sent.",
                "success"
            )

            return redirect(
                url_for("collaboration")
            )

        except Exception as e:

            app.logger.exception(
                "Collaboration email failed: %s",
                e
            )

            flash(
                "There was an issue sending your collaboration request.",
                "danger"
            )

    return render_seo_template(
        "collab.html",
        "collaboration",
        form=form,
        partners=partners
    )


# ============================================================
# MUSIC
# ============================================================

@app.route("/music")
def music():

    return render_seo_template(
        "music.html",
        "music"
    )


# ============================================================
# MOVIES
# ============================================================

@app.route("/movies")
def movies():

    MOVIES = []

    return render_seo_template(
        "movies.html",
        "movies",
        movies=MOVIES
    )


# ============================================================
# THEATRE TICKET BOOKING
# ============================================================

@app.route("/book")
def booking():
    """
    Show all currently active theatre shows.
    """

    db = get_booking_db()

    try:

        # Expire old bookings before displaying availability.
        expire_old_pending_bookings(db)

        db.commit()

        rows = db.execute("""
            SELECT *
            FROM shows
            WHERE active = 1
            ORDER BY show_date ASC,
                     show_time ASC,
                     id ASC
        """).fetchall()

        shows = []

        for row in rows:

            show = dict(row)

            show["available"] = (
                booking_available_seats(
                    db,
                    show["id"],
                    show["capacity"]
                )
            )

            shows.append(show)

        db.commit()

    except Exception:

        db.rollback()
        raise

    finally:

        db.close()

    return render_seo_template(
        "booking.html",
        "booking",
        shows=shows
    )


# ============================================================
# BOOK SHOW
# ============================================================

@app.route(
    "/book/<int:show_id>",
    methods=["GET", "POST"]
)
def book_show(show_id):
    """
    Display a show and create an atomic pending booking.

    Important:
    The show row is locked with FOR UPDATE before checking
    available seats.

    This prevents simultaneous customers from overselling
    the available capacity.
    """

    if request.method == "GET":

        db = get_booking_db()

        try:

            expire_old_pending_bookings(db)

            db.commit()

            show = db.execute("""
                SELECT *
                FROM shows
                WHERE id = %s
                  AND active = 1
            """, (show_id,)).fetchone()

            if not show:

                flash(
                    "This show is not available.",
                    "danger"
                )

                return redirect(
                    url_for("booking")
                )

            available = booking_available_seats(
                db,
                show["id"],
                show["capacity"]
            )

            db.commit()

        finally:

            db.close()

        return render_template(
            "book_show.html",
            show=show,
            available=available
        )

    # ========================================================
    # POST
    # ========================================================

    customer_name = request.form.get(
        "customer_name",
        ""
    ).strip()

    phone = request.form.get(
        "phone",
        ""
    ).strip()

    email = request.form.get(
        "email",
        ""
    ).strip()

    try:

        quantity = int(
            request.form.get(
                "quantity",
                "0"
            )
        )

    except (
        TypeError,
        ValueError
    ):

        quantity = 0

    if not customer_name:

        flash(
            "Please enter your name.",
            "danger"
        )

        return redirect(
            url_for(
                "book_show",
                show_id=show_id
            )
        )

    if not phone:

        flash(
            "Please enter your phone number.",
            "danger"
        )

        return redirect(
            url_for(
                "book_show",
                show_id=show_id
            )
        )

    if not email:

        flash(
            "Please enter your email address.",
            "danger"
        )

        return redirect(
            url_for(
                "book_show",
                show_id=show_id
            )
        )

    if quantity < 1:

        flash(
            "Please select at least 1 ticket.",
            "danger"
        )

        return redirect(
            url_for(
                "book_show",
                show_id=show_id
            )
        )

    db = get_booking_db()

    try:

        # ----------------------------------------------------
        # Start transaction.
        #
        # PostgreSQL locks the show row.
        # Another booking for the same show must wait.
        # ----------------------------------------------------

        db.execute(
            "BEGIN"
        )

        # ----------------------------------------------------
        # Lock show row.
        # ----------------------------------------------------

        show = db.execute("""
            SELECT *
            FROM shows
            WHERE id = %s
              AND active = 1
            FOR UPDATE
        """, (show_id,)).fetchone()

        if not show:

            db.rollback()

            flash(
                "This show is not available.",
                "danger"
            )

            return redirect(
                url_for("booking")
            )

        # ----------------------------------------------------
        # Expire old pending bookings while holding lock.
        # ----------------------------------------------------

        expire_old_pending_bookings(
            db
        )

        # ----------------------------------------------------
        # Calculate currently reserved seats.
        # ----------------------------------------------------

        booked_row = db.execute("""
            SELECT COALESCE(
                SUM(quantity),
                0
            ) AS booked
            FROM bookings
            WHERE show_id = %s
              AND status IN (
                  'PENDING',
                  'PAYMENT SUBMITTED',
                  'CONFIRMED'
              )
        """, (show_id,)).fetchone()

        booked = int(
            booked_row["booked"] or 0
        )

        available = max(
            0,
            int(show["capacity"]) - booked
        )

        # ----------------------------------------------------
        # Final atomic availability check.
        # ----------------------------------------------------

        if quantity > available:

            db.rollback()

            flash(
                f"Only {available} ticket(s) are currently available.",
                "danger"
            )

            return redirect(
                url_for(
                    "book_show",
                    show_id=show_id
                )
            )

        # ----------------------------------------------------
        # Calculate amount.
        # ----------------------------------------------------

        total_amount = (
            float(show["ticket_price"])
            * quantity
        )

        # ----------------------------------------------------
        # Generate unique booking code.
        # ----------------------------------------------------

        booking_code = (
            generate_booking_code()
        )

        while db.execute("""
            SELECT 1
            FROM bookings
            WHERE booking_code = %s
        """, (
            booking_code,
        )).fetchone():

            booking_code = (
                generate_booking_code()
            )

        # ----------------------------------------------------
        # Create PENDING booking.
        #
        # This booking reserves the seats for 10 minutes.
        # ----------------------------------------------------

        created_at = (
            current_timestamp_string()
        )

        cursor = db.execute("""
            INSERT INTO bookings (
                booking_code,
                show_id,
                customer_name,
                phone,
                email,
                quantity,
                total_amount,
                status,
                utr,
                payment_submitted_at,
                created_at
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                'PENDING',
                '',
                '',
                %s
            )
            RETURNING id
        """, (
            booking_code,
            show_id,
            customer_name,
            phone,
            email,
            quantity,
            total_amount,
            created_at
        ))

        booking_id = cursor.fetchone()["id"]

        # ----------------------------------------------------
        # Commit atomic reservation.
        # ----------------------------------------------------

        db.commit()

        # ----------------------------------------------------
        # Fetch created booking.
        # ----------------------------------------------------

        booking = db.execute("""
            SELECT *
            FROM bookings
            WHERE id = %s
        """, (
            booking_id,
        )).fetchone()

        # ----------------------------------------------------
        # Generate dynamic UPI URL.
        # ----------------------------------------------------

        upi_url = generate_upi_payment_url(
            total_amount
        )

    except Exception:

        db.rollback()

        app.logger.exception(
            "Booking creation failed."
        )

        flash(
            "We could not create your booking. Please try again.",
            "danger"
        )

        return redirect(
            url_for(
                "book_show",
                show_id=show_id
            )
        )

    finally:

        db.close()

    # --------------------------------------------------------
    # Do NOT send confirmation email yet.
    #
    # Customer still needs to pay and submit UTR.
    # --------------------------------------------------------

    return render_template(
        "booking_payment.html",
        booking=booking,
        show=show,
        upi_url=upi_url,
        upi_id=UPI_ID,
        pending_minutes=PENDING_BOOKING_MINUTES
    )


# ============================================================
# PAYMENT PAGE
# ============================================================

@app.route(
    "/book/payment/<int:booking_id>",
    methods=["GET"]
)
def booking_payment(booking_id):

    db = get_booking_db()

    try:

        booking = db.execute("""
            SELECT *
            FROM bookings
            WHERE id = %s
        """, (
            booking_id,
        )).fetchone()

        if not booking:

            flash(
                "Booking not found.",
                "danger"
            )

            return redirect(
                url_for("booking")
            )

        # ----------------------------------------------------
        # Automatically expire this booking if needed.
        # ----------------------------------------------------

        if booking["status"] == "PENDING":

            created_at = parse_booking_datetime(
                booking["created_at"]
            )

            if created_at:

                expiry_time = (
                    created_at
                    + datetime.timedelta(
                        minutes=PENDING_BOOKING_MINUTES
                    )
                )

                if current_datetime() >= expiry_time:

                    db.execute("""
                        UPDATE bookings
                        SET status = 'EXPIRED'
                        WHERE id = %s
                          AND status = 'PENDING'
                    """, (
                        booking_id,
                    ))

                    db.commit()

                    booking = db.execute("""
                        SELECT *
                        FROM bookings
                        WHERE id = %s
                    """, (
                        booking_id,
                    )).fetchone()

        show = db.execute("""
            SELECT *
            FROM shows
            WHERE id = %s
        """, (
            booking["show_id"],
        )).fetchone()

        if not show:

            flash(
                "Show information is no longer available.",
                "danger"
            )

            return redirect(
                url_for("booking")
            )

        upi_url = generate_upi_payment_url(
            booking["total_amount"]
        )

        return render_template(
            "booking_payment.html",
            booking=booking,
            show=show,
            upi_url=upi_url,
            upi_id=UPI_ID,
            pending_minutes=PENDING_BOOKING_MINUTES
        )

    finally:

        db.close()


# ============================================================
# SUBMIT PAYMENT / UTR
# ============================================================

@app.route(
    "/book/payment/<int:booking_id>",
    methods=["POST"]
)
def submit_booking_payment(booking_id):

    utr = request.form.get(
        "utr",
        ""
    ).strip()

    if not utr:

        flash(
            "Please enter your UTR / Transaction ID.",
            "danger"
        )

        return redirect(
            url_for(
                "booking_payment",
                booking_id=booking_id
            )
        )

    if len(utr) < 6:

        flash(
            "Please enter a valid UTR / Transaction ID.",
            "danger"
        )

        return redirect(
            url_for(
                "booking_payment",
                booking_id=booking_id
            )
        )

    db = get_booking_db()

    try:

        # ----------------------------------------------------
        # Lock booking row.
        # ----------------------------------------------------

        db.execute(
            "BEGIN"
        )

        booking = db.execute("""
            SELECT *
            FROM bookings
            WHERE id = %s
            FOR UPDATE
        """, (
            booking_id,
        )).fetchone()

        if not booking:

            db.rollback()

            flash(
                "Booking not found.",
                "danger"
            )

            return redirect(
                url_for("booking")
            )

        # ----------------------------------------------------
        # Check expiry.
        # ----------------------------------------------------

        if booking["status"] == "PENDING":

            created_at = parse_booking_datetime(
                booking["created_at"]
            )

            if created_at:

                expiry_time = (
                    created_at
                    + datetime.timedelta(
                        minutes=PENDING_BOOKING_MINUTES
                    )
                )

                if current_datetime() >= expiry_time:

                    db.execute("""
                        UPDATE bookings
                        SET status = 'EXPIRED'
                        WHERE id = %s
                    """, (
                        booking_id,
                    ))

                    db.commit()

                    flash(
                        "Your booking reservation has expired. "
                        "Please start a new booking.",
                        "danger"
                    )

                    return redirect(
                        url_for("booking")
                    )

        # ----------------------------------------------------
        # Already submitted.
        # ----------------------------------------------------

        if booking["status"] == "PAYMENT SUBMITTED":

            db.rollback()

            flash(
                "Payment details have already been submitted.",
                "warning"
            )

            return redirect(
                url_for(
                    "booking_payment",
                    booking_id=booking_id
                )
            )

        # ----------------------------------------------------
        # Confirmed/cancelled/expired cannot be modified.
        # ----------------------------------------------------

        if booking["status"] in {
            "CONFIRMED",
            "CANCELLED",
            "EXPIRED"
        }:

            db.rollback()

            flash(
                "This booking can no longer be modified.",
                "danger"
            )

            return redirect(
                url_for("booking")
            )

        # ----------------------------------------------------
        # Save UTR.
        # ----------------------------------------------------

        payment_submitted_at = (
            current_timestamp_string()
        )

        db.execute("""
            UPDATE bookings
            SET
                utr = %s,
                payment_submitted_at = %s,
                status = 'PAYMENT SUBMITTED'
            WHERE id = %s
        """, (
            utr,
            payment_submitted_at,
            booking_id
        ))

        db.commit()

        booking = db.execute("""
            SELECT *
            FROM bookings
            WHERE id = %s
        """, (
            booking_id,
        )).fetchone()

        show = db.execute("""
            SELECT *
            FROM shows
            WHERE id = %s
        """, (
            booking["show_id"],
        )).fetchone()

    except Exception:

        db.rollback()

        app.logger.exception(
            "Payment submission failed."
        )

        flash(
            "We could not submit your payment details. "
            "Please try again.",
            "danger"
        )

        return redirect(
            url_for(
                "booking_payment",
                booking_id=booking_id
            )
        )

    finally:

        db.close()

    # --------------------------------------------------------
    # Email customer that payment details were submitted.
    # --------------------------------------------------------

    try:

        send_booking_confirmation_email(
            booking,
            show
        )

    except Exception:

        app.logger.exception(
            "Booking payment email failed."
        )

    return render_template(
        "booking_success.html",
        booking=booking,
        show=show
    )


# ============================================================
# BOOKING ADMIN LOGIN
# ============================================================

@app.route(
    "/booking-admin/login",
    methods=["GET", "POST"]
)
def booking_admin_login():

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        if (
            username == BOOKING_ADMIN_USERNAME
            and password == BOOKING_ADMIN_PASSWORD
        ):

            session["booking_admin"] = True

            return redirect(
                url_for("booking_admin")
            )

        flash(
            "Invalid booking admin username or password.",
            "danger"
        )

    return render_template(
        "booking_admin_login.html"
    )


# ============================================================
# BOOKING ADMIN LOGOUT
# ============================================================

@app.route(
    "/booking-admin/logout"
)
def booking_admin_logout():

    session.pop(
        "booking_admin",
        None
    )

    return redirect(
        url_for("booking_admin_login")
    )


# ============================================================
# BOOKING ADMIN DASHBOARD
# ============================================================

@app.route("/booking-admin")
def booking_admin():

    auth_redirect = (
        booking_admin_required()
    )

    if auth_redirect:
        return auth_redirect

    db = get_booking_db()

    try:

        # Expire old reservations first.
        expire_old_pending_bookings(
            db
        )

        db.commit()

        rows = db.execute("""
            SELECT *
            FROM shows
            ORDER BY show_date ASC,
                     show_time ASC,
                     id ASC
        """).fetchall()

        shows = []

        for row in rows:

            show = dict(row)

            booked_row = db.execute("""
                SELECT COALESCE(
                    SUM(quantity),
                    0
                ) AS booked
                FROM bookings
                WHERE show_id = %s
                  AND status IN (
                      'PENDING',
                      'PAYMENT SUBMITTED',
                      'CONFIRMED'
                  )
            """, (
                show["id"],
            )).fetchone()

            show["booked"] = int(
                booked_row["booked"] or 0
            )

            show["available"] = max(
                0,
                int(show["capacity"])
                - show["booked"]
            )

            shows.append(show)

        bookings = db.execute("""
            SELECT
                bookings.*,
                shows.title,
                shows.theatre,
                shows.location,
                shows.show_date,
                shows.show_time
            FROM bookings
            JOIN shows
                ON shows.id = bookings.show_id
            ORDER BY bookings.id DESC
        """).fetchall()

    finally:

        db.close()

    return render_template(
        "booking_admin.html",
        shows=shows,
        bookings=bookings
    )


# ============================================================
# ADD SHOW
# ============================================================

@app.route(
    "/booking-admin/shows/add",
    methods=["GET", "POST"]
)
def booking_admin_add_show():

    auth_redirect = (
        booking_admin_required()
    )

    if auth_redirect:
        return auth_redirect

    if request.method == "POST":

        title = request.form.get(
            "title",
            ""
        ).strip()

        theatre = request.form.get(
            "theatre",
            ""
        ).strip()

        location = request.form.get(
            "location",
            ""
        ).strip()

        show_date = request.form.get(
            "show_date",
            ""
        ).strip()

        show_time = request.form.get(
            "show_time",
            ""
        ).strip()

        payment_link = request.form.get(
            "payment_link",
            ""
        ).strip()

        try:

            ticket_price = float(
                request.form.get(
                    "ticket_price",
                    "0"
                )
            )

            capacity = int(
                request.form.get(
                    "capacity",
                    "0"
                )
            )

        except (
            TypeError,
            ValueError
        ):

            flash(
                "Ticket price and capacity must be valid numbers.",
                "danger"
            )

            return redirect(
                url_for(
                    "booking_admin_add_show"
                )
            )

        if (
            not title
            or not theatre
            or not location
            or not show_date
            or not show_time
            or ticket_price < 0
            or capacity < 1
        ):

            flash(
                "Please fill all required fields correctly.",
                "danger"
            )

            return redirect(
                url_for(
                    "booking_admin_add_show"
                )
            )

        db = get_booking_db()

        try:

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
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    1,
                    %s
                )
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

        except Exception:

            db.rollback()

            app.logger.exception(
                "Failed to add show."
            )

            flash(
                "Could not add the show.",
                "danger"
            )

            return redirect(
                url_for(
                    "booking_admin_add_show"
                )
            )

        finally:

            db.close()

        flash(
            "Show added successfully.",
            "success"
        )

        return redirect(
            url_for("booking_admin")
        )

    return render_template(
        "booking_admin_show.html",
        show=None
    )


# ============================================================
# EDIT SHOW / CHANGE CAPACITY
# ============================================================

@app.route(
    "/booking-admin/shows/<int:show_id>/edit",
    methods=["GET", "POST"]
)
def booking_admin_edit_show(show_id):

    auth_redirect = (
        booking_admin_required()
    )

    if auth_redirect:
        return auth_redirect

    db = get_booking_db()

    try:

        show = db.execute("""
            SELECT *
            FROM shows
            WHERE id = %s
        """, (
            show_id,
        )).fetchone()

        if not show:

            flash(
                "Show not found.",
                "danger"
            )

            return redirect(
                url_for("booking_admin")
            )

        if request.method == "POST":

            title = request.form.get(
                "title",
                ""
            ).strip()

            theatre = request.form.get(
                "theatre",
                ""
            ).strip()

            location = request.form.get(
                "location",
                ""
            ).strip()

            show_date = request.form.get(
                "show_date",
                ""
            ).strip()

            show_time = request.form.get(
                "show_time",
                ""
            ).strip()

            payment_link = request.form.get(
                "payment_link",
                ""
            ).strip()

            try:

                ticket_price = float(
                    request.form.get(
                        "ticket_price",
                        "0"
                    )
                )

                new_capacity = int(
                    request.form.get(
                        "capacity",
                        "0"
                    )
                )

            except (
                TypeError,
                ValueError
            ):

                flash(
                    "Ticket price and capacity must be valid numbers.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "booking_admin_edit_show",
                        show_id=show_id
                    )
                )

            # Expire old pending bookings before checking capacity.
            expire_old_pending_bookings(
                db
            )

            booked_row = db.execute("""
                SELECT COALESCE(
                    SUM(quantity),
                    0
                ) AS booked
                FROM bookings
                WHERE show_id = %s
                  AND status IN (
                      'PENDING',
                      'PAYMENT SUBMITTED',
                      'CONFIRMED'
                  )
            """, (
                show_id,
            )).fetchone()

            booked = int(
                booked_row["booked"] or 0
            )

            if new_capacity < booked:

                flash(
                    f"Capacity cannot be lower than {booked}, "
                    "because those tickets are already reserved.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "booking_admin_edit_show",
                        show_id=show_id
                    )
                )

            if (
                not title
                or not theatre
                or not location
                or not show_date
                or not show_time
                or ticket_price < 0
                or new_capacity < 1
            ):

                flash(
                    "Please fill all required fields correctly.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "booking_admin_edit_show",
                        show_id=show_id
                    )
                )

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
                    payment_link = %s
                WHERE id = %s
            """, (
                title,
                theatre,
                location,
                show_date,
                show_time,
                ticket_price,
                new_capacity,
                payment_link,
                show_id
            ))

            db.commit()

            flash(
                "Show updated successfully.",
                "success"
            )

            return redirect(
                url_for("booking_admin")
            )

        return render_template(
            "booking_admin_show.html",
            show=show
        )

    except Exception:

        db.rollback()

        app.logger.exception(
            "Failed to edit show."
        )

        flash(
            "Could not update the show.",
            "danger"
        )

        return redirect(
            url_for("booking_admin")
        )

    finally:

        db.close()


# ============================================================
# ENABLE / DISABLE SHOW
# ============================================================

@app.route(
    "/booking-admin/shows/<int:show_id>/toggle"
)
def booking_admin_toggle_show(show_id):

    auth_redirect = (
        booking_admin_required()
    )

    if auth_redirect:
        return auth_redirect

    db = get_booking_db()

    try:

        db.execute("""
            UPDATE shows
            SET active =
                CASE
                    WHEN active = 1 THEN 0
                    ELSE 1
                END
            WHERE id = %s
        """, (
            show_id,
        ))

        db.commit()

    except Exception:

        db.rollback()

        app.logger.exception(
            "Failed to toggle show."
        )

        flash(
            "Could not change show status.",
            "danger"
        )

    finally:

        db.close()

    return redirect(
        url_for("booking_admin")
    )


# ============================================================
# UPDATE BOOKING STATUS
# ============================================================

@app.route(
    "/booking-admin/bookings/<int:booking_id>/status",
    methods=["POST"]
)
def booking_admin_update_booking_status(
    booking_id
):

    auth_redirect = (
        booking_admin_required()
    )

    if auth_redirect:
        return auth_redirect

    status = request.form.get(
        "status",
        ""
    ).upper().strip()

    allowed_statuses = {
        "PENDING",
        "PAYMENT SUBMITTED",
        "CONFIRMED",
        "CANCELLED"
    }

    if status not in allowed_statuses:

        flash(
            "Invalid booking status.",
            "danger"
        )

        return redirect(
            url_for("booking_admin")
        )

    db = get_booking_db()

    try:

        db.execute(
            "BEGIN"
        )

        booking = db.execute("""
            SELECT *
            FROM bookings
            WHERE id = %s
            FOR UPDATE
        """, (
            booking_id,
        )).fetchone()

        if not booking:

            db.rollback()

            flash(
                "Booking not found.",
                "danger"
            )

            return redirect(
                url_for("booking_admin")
            )

        # ----------------------------------------------------
        # Do not manually restore an expired booking to PENDING
        # unless you intentionally want to.
        # ----------------------------------------------------

        if (
            booking["status"] == "EXPIRED"
            and status == "PENDING"
        ):

            db.rollback()

            flash(
                "An expired booking cannot be changed back to PENDING.",
                "danger"
            )

            return redirect(
                url_for("booking_admin")
            )

        # ----------------------------------------------------
        # If confirming a booking, verify it has payment info.
        # ----------------------------------------------------

        if status == "CONFIRMED":

            if not booking["utr"]:

                db.rollback()

                flash(
                    "Cannot confirm this booking because no UTR / "
                    "Transaction ID has been submitted.",
                    "danger"
                )

                return redirect(
                    url_for("booking_admin")
                )

        db.execute("""
            UPDATE bookings
            SET status = %s
            WHERE id = %s
        """, (
            status,
            booking_id
        ))

        db.commit()

        updated_booking = db.execute("""
            SELECT *
            FROM bookings
            WHERE id = %s
        """, (
            booking_id,
        )).fetchone()

        show = db.execute("""
            SELECT *
            FROM shows
            WHERE id = %s
        """, (
            updated_booking["show_id"],
        )).fetchone()

    except Exception:

        db.rollback()

        app.logger.exception(
            "Failed to update booking status."
        )

        flash(
            "Could not update booking status.",
            "danger"
        )

        return redirect(
            url_for("booking_admin")
        )

    finally:

        db.close()

    # --------------------------------------------------------
    # Send status update email.
    # --------------------------------------------------------

    try:

        send_booking_confirmation_email(
            updated_booking,
            show
        )

    except Exception:

        app.logger.exception(
            "Booking status email failed."
        )

    flash(
        f"Booking status changed to {status}.",
        "success"
    )

    return redirect(
        url_for("booking_admin")
    )


# ============================================================
# XML SITEMAP
# ============================================================

@app.route("/sitemap.xml")
def sitemap():

    pages = [

        {
            "loc": url_for(
                "home",
                _external=True
            ),
            "priority": "1.0"
        },

        {
            "loc": url_for(
                "about",
                _external=True
            ),
            "priority": "0.8"
        },

        {
            "loc": url_for(
                "movies",
                _external=True
            ),
            "priority": "0.9"
        },

        {
            "loc": url_for(
                "music",
                _external=True
            ),
            "priority": "0.9"
        },

        {
            "loc": url_for(
                "collaboration",
                _external=True
            ),
            "priority": "0.7"
        },

        {
            "loc": url_for(
                "join",
                _external=True
            ),
            "priority": "0.6"
        },

        {
            "loc": url_for(
                "contact",
                _external=True
            ),
            "priority": "0.7"
        },

        {
            "loc": url_for(
                "team",
                _external=True
            ),
            "priority": "0.6"
        },

        {
            "loc": url_for(
                "booking",
                _external=True
            ),
            "priority": "0.9"
        }

    ]

    sitemap_xml = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]

    for page in pages:

        sitemap_xml.append(
            f"""
    <url>
        <loc>{page["loc"]}</loc>
        <changefreq>weekly</changefreq>
        <priority>{page["priority"]}</priority>
    </url>
"""
        )

    sitemap_xml.append(
        "</urlset>"
    )

    return Response(
        "\n".join(sitemap_xml),
        mimetype="application/xml"
    )


# ============================================================
# ROBOTS.TXT
# ============================================================

@app.route("/robots.txt")
def robots():

    robots_txt = f"""User-agent: *
Allow: /

Sitemap: {SITE_URL}/sitemap.xml
"""

    return Response(
        robots_txt,
        mimetype="text/plain"
    )


# ============================================================
# RUN APPLICATION
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )