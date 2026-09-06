import os
import pickle
import datetime
from functools import wraps

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

# Load environment variables
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


# Configure Resend SDK

if RESEND_API_KEY:

    resend.api_key = RESEND_API_KEY


# ============================================================
# WEBSITE SEO INFORMATION
# ============================================================

SITE_NAME = "Home Entertainments"

SITE_DESCRIPTION = (
    "Home Entertainments is a Kannada entertainment "
    "production house creating movies, web series, "
    "music and original entertainment content."
)

SITE_URL = "https://homeentertainments.in"


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
    }

}


# ============================================================
# HELPER - SEO RENDER
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
# HELPER FUNCTIONS
# ============================================================

def allowed_file(filename):

    """
    Return True only for allowed upload extensions.
    """

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

    """
    Collect all submitted form fields without
    hard-coding field names.
    """

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
# RESEND EMAIL API
# ============================================================

def send_submission_email(
    subject,
    form_data,
    uploaded_file=None,
    uploaded_filename=None
):

    """
    Send website form submission using Resend Email API.

    Supports:
    - HTML email
    - Plain-text fallback
    - Reply-To
    - PDF attachment
    """

    # --------------------------------------------------------
    # Check configuration
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # Find visitor email
    # --------------------------------------------------------

    visitor_email = (
        form_data.get("Email")
        or form_data.get("Email Address")
        or form_data.get("E-Mail")
    )


    # ========================================================
    # PLAIN TEXT EMAIL
    # ========================================================

    text_lines = [

        "NEW WEBSITE SUBMISSION",

        "",

        f"Form: {subject}",

        "",

        "-----------------------------------",

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


    # ========================================================
    # HTML EMAIL ROWS
    # ========================================================

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


    # ========================================================
    # REPLY INFORMATION
    # ========================================================

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


    # ========================================================
    # HTML EMAIL
    # ========================================================

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

            <!-- Header -->

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


            <!-- Content -->

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


            <!-- Footer -->

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


    # ========================================================
    # RESEND PARAMETERS
    # ========================================================

    params = {

        "from": RESEND_FROM_EMAIL,

        "to": [
            MAIL_RECEIVER
        ],

        "subject": subject,

        "html": html_body,

        "text": plain_text_body

    }


    # ========================================================
    # REPLY-TO
    # ========================================================

    if visitor_email:

        params["reply_to"] = [
            visitor_email
        ]


    # ========================================================
    # PDF ATTACHMENT
    # ========================================================

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


    # ========================================================
    # SEND EMAIL
    # ========================================================

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


            # Optional Resume

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