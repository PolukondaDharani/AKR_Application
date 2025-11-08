# app.py
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
import os
from flask_mail import Mail, Message
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField
from wtforms.validators import DataRequired, Email
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import pickle
import datetime
from flask import request, send_from_directory, flash
import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

app = Flask(__name__)
app.secret_key = "supersecret"

SCOPES = ['https://www.googleapis.com/auth/calendar.events']
CREDENTIALS_FILE = 'credentials.json'



# Upload folder
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# ---------------- Email Config ---------------- #
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'your_email@gmail.com'
app.config['MAIL_PASSWORD'] = 'your_app_password'
app.config['MAIL_DEFAULT_SENDER'] = 'your_email@gmail.com'

mail = Mail(app)

# ---------------- Form ---------------- #
class CollaborationForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    email = StringField("Email", validators=[DataRequired(), Email()])
    organization = StringField("Organization", validators=[DataRequired()])
    message = TextAreaField("Message", validators=[DataRequired()])

# Admin login details
ADMIN = {"username": "admin", "password": "password123"}

# ------------------- Public Routes ------------------- #
@app.route("/")
def home():
    return render_template("home.html")

@app.route('/team')
def team():
    team_members = [
        {
            "name": "John Doe",
            "role": "Actor",
            "image": "john.jpg",
            "bio": "Award-winning actor with 10+ years of experience in cinema.",
            "social": {"instagram":"#", "twitter":"#", "linkedin":"#"}
        },
        {
            "name": "Jane Smith",
            "role": "Director",
            "image": "jane.jpg",
            "bio": "Creative director shaping unique storytelling experiences.",
            "social": {"instagram":"#", "twitter":"#", "linkedin":"#"}
        }
        # Add more team members here
    ]
    return render_template("team.html", team_members=team_members)

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/contact")
def contact():
    return render_template("contact.html")

@app.route("/join", methods=["GET", "POST"])
def join():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        interest = request.form.get("interest")
        portfolio = request.form.get("portfolio")
        resume = request.files.get("resume")

        # Save uploaded file (optional)
        if resume and resume.filename:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], resume.filename)
            resume.save(filepath)
        else:
            filepath = None

        # Send email
        try:
            msg_body = f"""
New 'Join Us' submission:

Name: {name}
Email: {email}
Interest: {interest}
Portfolio/Links: {portfolio}
"""
            msg = Message(
                subject="New Join Us Submission",
                recipients=["your_email@gmail.com"],
                body=msg_body
            )

            if filepath:
                with app.open_resource(filepath) as fp:
                    msg.attach(resume.filename, "application/octet-stream", fp.read())

            mail.send(msg)
            flash("Your submission has been sent successfully!", "success")
        except Exception as e:
            print(f"Error sending email: {e}")
            flash("There was an issue sending your submission.", "danger")

        return redirect(url_for("join"))

    return render_template("join.html")

@app.route("/collaboration", methods=["GET", "POST"])
def collaboration():
    form = CollaborationForm()
    partners = [
        {"name": "Partner 1", "description": "Film Production House", "logo": "partner1.png"},
        {"name": "Partner 2", "description": "Event Management", "logo": "partner2.png"},
        {"name": "Partner 3", "description": "Cultural Foundation", "logo": "partner3.png"}
    ]
    if form.validate_on_submit():
        msg = Message(
            subject="New Collaboration Request",
            recipients=["your_email@gmail.com"],
            body=f"""
New collaboration request:

Name: {form.name.data}
Email: {form.email.data}
Organization: {form.organization.data}
Message: {form.message.data}
"""
        )
        mail.send(msg)
        flash("Thank you! Request sent.", "success")
        return redirect(url_for("collaboration"))

    return render_template("collab.html", form=form, partners=partners)

# ------------------- Authentication ------------------- #
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if (request.form["username"] == ADMIN["username"] and
                request.form["password"] == ADMIN["password"]):
            session["user"] = "admin"
            return redirect(url_for("admin_home"))
        else:
            flash("Invalid login", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

# ------------------- Admin Pages ------------------- #
def admin_required(f):
    def wrap(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    wrap.__name__ = f.__name__
    return wrap

@app.route("/admin")
@admin_required
def admin_home():
    return render_template("admin/admin_home.html")

@app.route("/admin/meetings")
def admin_meetings():
    if 'credentials' not in session:
        return redirect(url_for('authorize'))

    creds = pickle.loads(session['credentials'])
    service = build('calendar', 'v3', credentials=creds)

    # Fetch today's events
    now = datetime.datetime.utcnow().isoformat() + 'Z'
    end_of_day = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).isoformat() + 'Z'

    events_result = service.events().list(
        calendarId='primary',
        timeMin=now,
        timeMax=end_of_day,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])

    return render_template("admin/meetings.html", events=events)


# ---------------- OAuth Flow ----------------
@app.route("/authorize")
def authorize():
    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri=url_for('oauth2callback', _external=True)
    )
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    session['state'] = state
    return redirect(auth_url)


@app.route("/oauth2callback")
def oauth2callback():
    state = session['state']
    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        state=state,
        redirect_uri=url_for('oauth2callback', _external=True)
    )
    flow.fetch_token(authorization_response=request.url)

    creds = flow.credentials
    session['credentials'] = pickle.dumps(creds)
    return redirect(url_for('admin_meetings'))


# ---------------- Add Meeting ----------------
@app.route("/admin/meetings/add", methods=["POST"])
def add_meeting():
    if 'credentials' not in session:
        return redirect(url_for('authorize'))

    creds = pickle.loads(session['credentials'])
    service = build('calendar', 'v3', credentials=creds)

    title = request.form.get('title')
    start_time = request.form.get('start_time') + ":00+05:30"
    end_time = request.form.get('end_time') + ":00+05:30"

    # start_time = request.form.get('start_time')  # format: 2025-10-25T15:00
    # end_time = request.form.get('end_time')      # format: 2025-10-25T16:00

    event = {
        'summary': title,
        'start': {'dateTime': start_time, 'timeZone': 'Asia/Kolkata'},
        'end': {'dateTime': end_time, 'timeZone': 'Asia/Kolkata'},
        'conferenceData': {
            'createRequest': {
                'requestId': 'some-random-id',
                'conferenceSolutionKey': {'type': 'hangoutsMeet'}
            }
        }
    }

    created_event = service.events().insert(calendarId='primary', body=event, conferenceDataVersion=1).execute()

    return redirect(url_for('admin_meetings'))

@app.route('/delete_meeting/<event_id>', methods=['GET'])
def delete_meeting(event_id):
    creds = None

    # Load credentials from session
    if 'credentials' not in session:
        return redirect(url_for('authorize'))

    creds = pickle.loads(session['credentials'])

    service = build('calendar', 'v3', credentials=creds)

    try:
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        flash("Meeting deleted successfully!", "success")
    except Exception as e:
        print(e)
        flash("Failed to delete meeting!", "danger")

    return redirect(url_for('meetings'))
# ---------------- Share Meeting Invite ----------------
@app.route("/share_invite", methods=["POST"])
@admin_required
def share_invite():
    title = request.form.get("meeting_title")
    link = request.form.get("meeting_link")
    emails = request.form.get("emails")

    if not emails:
        flash("Please enter at least one email address.", "warning")
        return redirect(url_for("admin_meetings"))

    recipients = [e.strip() for e in emails.split(",") if e.strip()]

    # Prepare the email
    subject = f"Meeting Invite: {title}"
    body = f"""
You are invited to a meeting.

📅 Title: {title}
🔗 Join Link: {link}

Sent via Admin Panel
"""

    # Send email to each recipient
    try:
        for email in recipients:
            msg = Message(subject=subject, recipients=[email], body=body)
            mail.send(msg)
        flash("Meeting invite sent successfully!", "success")
    except Exception as e:
        print("Error sending email:", e)
        flash("Failed to send invites.", "danger")

    return redirect(url_for("admin_meetings"))

@app.route('/admin/docs')
@admin_required
def admin_docs():
    files = os.listdir(app.config['UPLOAD_FOLDER'])

    def get_file_size(filename):
        size = os.path.getsize(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return f"{round(size/1024, 2)} KB"

    return render_template('admin/docs.html', files=files, get_file_size=get_file_size)


@app.route('/upload_document', methods=['POST'])
def upload_document():
    file = request.files['document']

    if file.filename:
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], file.filename))
        flash("Document uploaded successfully!", "success")

    return redirect(url_for('admin_docs'))

@app.route('/documents/<filename>')
def download_document(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/delete_document/<filename>')
def delete_document(filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        flash("Document deleted!", "danger")
    return redirect(url_for('admin_docs'))


# @app.route("/admin/links")
# @admin_required
# def admin_links():
#     return render_template("admin/links.html")
# Temporary Links storage (will replace with DB later)
links = []

@app.route("/admin/links")
@admin_required
def admin_links():
    return render_template("admin/links.html", links=links)

@app.route("/admin/links/add", methods=["POST"])
@admin_required
def add_link():
    title = request.form.get("title")
    url = request.form.get("url")
    description = request.form.get("description")

    if title and url:
        links.append({
            "id": len(links) + 1,
            "title": title,
            "url": url,
            "description": description
        })
        flash("Link added successfully!", "success")

    return redirect(url_for("admin_links"))

@app.route("/admin/links/delete/<int:link_id>")
@admin_required
def delete_link(link_id):
    global links
    links = [l for l in links if l["id"] != link_id]
    flash("Link deleted!", "danger")
    return redirect(url_for("admin_links"))

@app.route("/admin/links/edit/<int:link_id>", methods=["POST"])
@admin_required
def edit_link(link_id):
    title = request.form.get("title")
    url = request.form.get("url")
    description = request.form.get("description")

    for link in links:
        if link["id"] == link_id:
            link["title"] = title
            link["url"] = url
            link["description"] = description
            flash("Link updated successfully!", "info")
            break

    return redirect(url_for("admin_links"))


@app.route("/admin/social")
@admin_required
def admin_social():
    return render_template("admin/social.html")

@app.route("/admin/user_forms")
@admin_required
def admin_user_forms():
    return render_template("admin/user_forms.html")

@app.route("/dashboard")
def dashboard():
    return redirect(url_for('admin_meetings'))

# ------------------- Run ------------------- #
if __name__ == "__main__":
    app.run(debug=True)
