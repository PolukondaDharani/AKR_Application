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
    send_from_directory
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

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)


# ============================================================
# App Config
# ============================================================

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-key"
)


SCOPES = [
    "https://www.googleapis.com/auth/calendar.events"
]

CREDENTIALS_FILE = "credentials.json"


# ============================================================
# Upload Config
# ============================================================

UPLOAD_FOLDER = "uploads"

ALLOWED_UPLOAD_EXTENSIONS = {
    "pdf"
}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ============================================================
# Resend Email API Configuration
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
# Helper Functions
# ============================================================

def allowed_file(filename):
    """
    Return True only for allowed upload extensions.
    """

    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_UPLOAD_EXTENSIONS
    )


def get_form_data():
    """
    Collect all submitted form fields without hard-coding
    the field names.

    This makes the Contact and Join Us routes work with
    the field names already used by their HTML forms.
    """

    data = {}

    for key, value in request.form.items():

        if key.lower() in {
            "csrf_token",
            "submit"
        }:
            continue

        value = (value or "").strip()

        if value:

            data[
                key.replace("_", " ").title()
            ] = value

    return data


# ============================================================
# Resend Email API - Send Submission Email
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
    # Check Resend configuration
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
    # Plain Text Email
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

    plain_text_body = "\n".join(text_lines)

    # ========================================================
    # HTML Email Rows
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
    # Reply Information
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
    # HTML Email
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
    # Create Resend Email Parameters
    # ========================================================

    params = {
        "from": RESEND_FROM_EMAIL,
        "to": [MAIL_RECEIVER],
        "subject": subject,
        "html": html_body,
        "text": plain_text_body
    }

    # ========================================================
    # Reply-To
    # ========================================================

    if visitor_email:
        params["reply_to"] = [visitor_email]

    # ========================================================
    # PDF Attachment
    # ========================================================

    if uploaded_file and uploaded_filename:
        uploaded_file.stream.seek(0)
        file_data = list(uploaded_file.read())

        params["attachments"] = [
            {
                "filename": secure_filename(uploaded_filename),
                "content": file_data
            }
        ]

    # ========================================================
    # Send Using Resend API
    # ========================================================

    response = resend.Emails.send(params)

    app.logger.info(
        "Email sent successfully through Resend: %s",
        response
    )

    return response


# ============================================================
# Collaboration Form
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
# Admin Login
# ============================================================

ADMIN = {
    "username": "admin",
    "password": "password123"
}


# ============================================================
# Public Routes
# ============================================================

@app.route("/")
def home():

    return render_template(
        "home.html"
    )


# ============================================================
# Team
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

    return render_template(
        "team.html",
        team_members=team_members
    )


# ============================================================
# About
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

    return render_template(
        "about.html",
        team_members=team_members
    )


# ============================================================
# Contact
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

    return render_template(
        "contact.html"
    )


# ============================================================
# Join Us
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

            # ------------------------------------------------
            # Optional Resume
            # ------------------------------------------------

            if resume and resume.filename:

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

    return render_template(
        "join.html"
    )


# ============================================================
# Collaboration
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

    return render_template(
        "collab.html",
        form=form,
        partners=partners
    )


# ============================================================
# Authentication
# ============================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        )

        password = request.form.get(
            "password",
            ""
        )

        if (
            username == ADMIN["username"]
            and password == ADMIN["password"]
        ):

            session["user"] = "admin"

            return redirect(
                url_for("admin_home")
            )

        flash(
            "Invalid login",
            "danger"
        )

    return render_template(
        "login.html"
    )


@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("home")
    )


# ============================================================
# Admin Authentication Decorator
# ============================================================

def admin_required(f):

    @wraps(f)
    def wrap(*args, **kwargs):

        if "user" not in session:

            return redirect(
                url_for("login")
            )

        return f(
            *args,
            **kwargs
        )

    return wrap


# ============================================================
# Admin Home
# ============================================================

@app.route("/admin")
@admin_required
def admin_home():

    return render_template(
        "admin/admin_home.html"
    )


# ============================================================
# Admin Meetings
# ============================================================

@app.route("/admin/meetings")
@admin_required
def admin_meetings():

    if "credentials" not in session:

        return redirect(
            url_for("authorize")
        )

    creds = pickle.loads(
        session["credentials"]
    )

    service = build(
        "calendar",
        "v3",
        credentials=creds
    )

    # --------------------------------------------------------
    # Fetch today's events
    # --------------------------------------------------------

    now = (
        datetime.datetime.utcnow()
        .isoformat()
        + "Z"
    )

    end_of_day = (
        datetime.datetime.utcnow()
        + datetime.timedelta(days=1)
    ).isoformat() + "Z"

    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now,
            timeMax=end_of_day,
            singleEvents=True,
            orderBy="startTime"
        )
        .execute()
    )

    events = events_result.get(
        "items",
        []
    )

    return render_template(
        "admin/meetings.html",
        events=events
    )


# ============================================================
# Google OAuth
# ============================================================

@app.route("/authorize")
def authorize():

    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri=url_for(
            "oauth2callback",
            _external=True
        )
    )

    auth_url, state = (
        flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true"
        )
    )

    session["state"] = state

    return redirect(
        auth_url
    )


@app.route("/oauth2callback")
def oauth2callback():

    state = session["state"]

    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        state=state,
        redirect_uri=url_for(
            "oauth2callback",
            _external=True
        )
    )

    flow.fetch_token(
        authorization_response=request.url
    )

    creds = flow.credentials

    session["credentials"] = pickle.dumps(
        creds
    )

    return redirect(
        url_for("admin_meetings")
    )


# ============================================================
# Add Meeting
# ============================================================

@app.route(
    "/admin/meetings/add",
    methods=["POST"]
)
@admin_required
def add_meeting():

    if "credentials" not in session:

        return redirect(
            url_for("authorize")
        )

    creds = pickle.loads(
        session["credentials"]
    )

    service = build(
        "calendar",
        "v3",
        credentials=creds
    )

    title = request.form.get(
        "title"
    )

    start_value = request.form.get(
        "start_time"
    )

    end_value = request.form.get(
        "end_time"
    )

    if not title or not start_value or not end_value:

        flash(
            "Please provide meeting title, start time and end time.",
            "danger"
        )

        return redirect(
            url_for("admin_meetings")
        )

    start_time = (
        start_value
        + ":00+05:30"
    )

    end_time = (
        end_value
        + ":00+05:30"
    )

    event = {

        "summary": title,

        "start": {
            "dateTime": start_time,
            "timeZone": "Asia/Kolkata"
        },

        "end": {
            "dateTime": end_time,
            "timeZone": "Asia/Kolkata"
        },

        "conferenceData": {

            "createRequest": {

                "requestId": (
                    f"meeting-{datetime.datetime.now().timestamp()}"
                ),

                "conferenceSolutionKey": {
                    "type": "hangoutsMeet"
                }

            }

        }

    }

    created_event = (
        service.events()
        .insert(
            calendarId="primary",
            body=event,
            conferenceDataVersion=1
        )
        .execute()
    )

    flash(
        "Meeting created successfully!",
        "success"
    )

    return redirect(
        url_for("admin_meetings")
    )


# ============================================================
# Delete Meeting
# ============================================================

@app.route(
    "/delete_meeting/<event_id>",
    methods=["GET"]
)
@admin_required
def delete_meeting(event_id):

    if "credentials" not in session:

        return redirect(
            url_for("authorize")
        )

    creds = pickle.loads(
        session["credentials"]
    )

    service = build(
        "calendar",
        "v3",
        credentials=creds
    )

    try:

        service.events().delete(
            calendarId="primary",
            eventId=event_id
        ).execute()

        flash(
            "Meeting deleted successfully!",
            "success"
        )

    except Exception as e:

        app.logger.exception(
            "Meeting deletion failed: %s",
            e
        )

        flash(
            "Failed to delete meeting!",
            "danger"
        )

    return redirect(
        url_for("admin_meetings")
    )


# ============================================================
# Share Meeting Invite
# ============================================================

@app.route(
    "/share_invite",
    methods=["POST"]
)
@admin_required
def share_invite():

    title = request.form.get(
        "meeting_title",
        "Meeting"
    )

    link = request.form.get(
        "meeting_link",
        ""
    )

    emails = request.form.get(
        "emails",
        ""
    )

    if not emails:

        flash(
            "Please enter at least one email address.",
            "warning"
        )

        return redirect(
            url_for("admin_meetings")
        )

    recipients = [
        email.strip()
        for email in emails.split(",")
        if email.strip()
    ]

    subject = f"Meeting Invite: {title}"

    body = f"""
You are invited to a meeting.

Meeting Title:
{title}

Join Link:
{link}

Sent via Home Entertainments Admin Panel.
"""

    html_body = f"""
    <!DOCTYPE html>

    <html>

    <body style="
        margin: 0;
        padding: 30px;
        background-color: #f3f4f6;
        font-family: Arial, Helvetica, sans-serif;
    ">

        <div style="
            max-width: 600px;
            margin: auto;
            background: white;
            border-radius: 12px;
            overflow: hidden;
        ">

            <div style="
                padding: 25px;
                background-color: #111827;
                color: white;
            ">

                <h2 style="
                    margin: 0;
                ">
                    Home Entertainments
                </h2>

            </div>

            <div style="
                padding: 30px;
            ">

                <h2>
                    You are invited to a meeting
                </h2>

                <p>
                    <strong>Meeting Title:</strong>
                    {escape(title)}
                </p>

                <p>
                    Click the button below to join the meeting.
                </p>

                <p style="
                    margin: 30px 0;
                ">

                    <a
                        href="{escape(link)}"
                        style="
                            display: inline-block;
                            padding: 14px 24px;
                            background-color: #111827;
                            color: white;
                            text-decoration: none;
                            border-radius: 6px;
                            font-weight: 600;
                        "
                    >
                        Join Meeting
                    </a>

                </p>

                <p style="
                    color: #666;
                    font-size: 13px;
                ">
                    If the button does not work, use this link:
                </p>

                <p>
                    {escape(link)}
                </p>

            </div>

            <div style="
                padding: 18px;
                text-align: center;
                background-color: #f9fafb;
                color: #999;
                font-size: 12px;
            ">

                Home Entertainments

            </div>

        </div>

    </body>

    </html>
    """

    try:

        for email in recipients:

            params = {
                "from": RESEND_FROM_EMAIL,
                "to": [email],
                "subject": subject,
                "text": body,
                "html": html_body
            }

            resend.Emails.send(params)

        flash(
            "Meeting invite sent successfully!",
            "success"
        )

    except Exception as e:

        app.logger.exception(
            "Error sending meeting invite: %s",
            e
        )

        flash(
            "Failed to send invites.",
            "danger"
        )

    return redirect(
        url_for("admin_meetings")
    )


# ============================================================
# Admin Documents
# ============================================================

@app.route("/admin/docs")
@admin_required
def admin_docs():

    files = os.listdir(
        app.config["UPLOAD_FOLDER"]
    )

    def get_file_size(filename):

        file_path = os.path.join(
            app.config["UPLOAD_FOLDER"],
            filename
        )

        size = os.path.getsize(
            file_path
        )

        return f"{round(size / 1024, 2)} KB"

    return render_template(
        "admin/docs.html",
        files=files,
        get_file_size=get_file_size
    )


# ============================================================
# Upload Document
# ============================================================

@app.route(
    "/upload_document",
    methods=["POST"]
)
@admin_required
def upload_document():

    file = request.files.get(
        "document"
    )

    if not file or not file.filename:

        flash(
            "Please select a document.",
            "warning"
        )

        return redirect(
            url_for("admin_docs")
        )

    if not allowed_file(
        file.filename
    ):

        flash(
            "Only PDF files are allowed.",
            "danger"
        )

        return redirect(
            url_for("admin_docs")
        )

    filename = secure_filename(
        file.filename
    )

    if not filename:

        flash(
            "Invalid filename.",
            "danger"
        )

        return redirect(
            url_for("admin_docs")
        )

    file.save(
        os.path.join(
            app.config["UPLOAD_FOLDER"],
            filename
        )
    )

    flash(
        "Document uploaded successfully!",
        "success"
    )

    return redirect(
        url_for("admin_docs")
    )


# ============================================================
# Download Document
# ============================================================

@app.route(
    "/documents/<filename>"
)
@admin_required
def download_document(filename):

    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        filename
    )


# ============================================================
# Delete Document
# ============================================================

@app.route(
    "/delete_document/<filename>"
)
@admin_required
def delete_document(filename):

    safe_filename = secure_filename(
        filename
    )

    file_path = os.path.join(
        app.config["UPLOAD_FOLDER"],
        safe_filename
    )

    if os.path.exists(file_path):

        os.remove(file_path)

        flash(
            "Document deleted!",
            "danger"
        )

    return redirect(
        url_for("admin_docs")
    )


# ============================================================
# Admin Links
# ============================================================

links = []


@app.route("/admin/links")
@admin_required
def admin_links():

    return render_template(
        "admin/links.html",
        links=links
    )


@app.route(
    "/admin/links/add",
    methods=["POST"]
)
@admin_required
def add_link():

    title = request.form.get(
        "title"
    )

    url = request.form.get(
        "url"
    )

    description = request.form.get(
        "description"
    )

    if title and url:

        links.append({

            "id": len(links) + 1,

            "title": title,

            "url": url,

            "description": description

        })

        flash(
            "Link added successfully!",
            "success"
        )

    return redirect(
        url_for("admin_links")
    )


@app.route(
    "/admin/links/delete/<int:link_id>"
)
@admin_required
def delete_link(link_id):

    global links

    links = [
        link
        for link in links
        if link["id"] != link_id
    ]

    flash(
        "Link deleted!",
        "danger"
    )

    return redirect(
        url_for("admin_links")
    )


@app.route(
    "/admin/links/edit/<int:link_id>",
    methods=["POST"]
)
@admin_required
def edit_link(link_id):

    title = request.form.get(
        "title"
    )

    url = request.form.get(
        "url"
    )

    description = request.form.get(
        "description"
    )

    for link in links:

        if link["id"] == link_id:

            link["title"] = title

            link["url"] = url

            link["description"] = description

            flash(
                "Link updated successfully!",
                "info"
            )

            break

    return redirect(
        url_for("admin_links")
    )


# ============================================================
# Admin Social
# ============================================================

@app.route("/admin/social")
@admin_required
def admin_social():

    return render_template(
        "admin/social.html"
    )


# ============================================================
# Admin User Forms
# ============================================================

@app.route("/admin/user_forms")
@admin_required
def admin_user_forms():

    return render_template(
        "admin/user_forms.html"
    )


# ============================================================
# Dashboard
# ============================================================

@app.route("/dashboard")
def dashboard():

    return redirect(
        url_for("admin_meetings")
    )


# ============================================================
# Movies
# ============================================================

@app.route("/movies")
def movies():

    MOVIES = [

        {
            "id": 1,

            "title": (
                "Project X: The Awakening"
            ),

            "poster": "postercard1.jpeg",

            "video_url": (
                "https://www.youtube.com/embed/kzBoieupnV4"
            ),

            "synopsis": (
                "A gripping journey through "
                "time and space, where a group "
                "of explorers discovers an "
                "ancient secret."
            ),

            "director": "Christopher Nolan",

            "writer": "Jonathan Nolan",

            "music": "Hans Zimmer",

            "cast": (
                "Leonardo DiCaprio, Elliot Page"
            ),

            "actors": (
                "Tom Hardy, Cillian Murphy"
            ),

            "release": "Summer 2026"
        }

    ]

    return render_template(
        "movies.html",
        movies=MOVIES
    )


# ============================================================
# Run Application
# ============================================================

if __name__ == "__main__":

    app.run(
        debug=True
    )