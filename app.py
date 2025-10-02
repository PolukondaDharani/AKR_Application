from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField
from wtforms.validators import DataRequired, Email
import os

app = Flask(__name__)
app.secret_key = "supersecret"  # replace in production

# Upload folder for dashboard (your existing code)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

from flask_mail import Mail, Message

# ---------------- Email Config ---------------- #

# Step 1: Enable 2-Step Verification

# Go to Google Account → Security
# .

# Under “Signing in to Google”, turn on 2-Step Verification.

# Follow the steps to set it up (usually via phone number or Google Authenticator).

# Step 2: Generate an App Password

# Once 2-Step Verification is enabled, go back to Google Account → Security → App passwords.

# Under “Select app”, choose Mail.

# Under “Select device”, choose Other (Custom name).

# Enter a name like: FlaskCollaborationApp → Click Generate.

# Google will show a 16-character password (e.g., abcd efgh ijkl mnop).

# ------------------------------------------------------------------------------

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'your_email@gmail.com'       # your receiving email
app.config['MAIL_PASSWORD'] = 'your_app_password'          # app password (not Gmail login)
app.config['MAIL_DEFAULT_SENDER'] = 'your_email@gmail.com'

mail = Mail(app)
# ---------------- Form ---------------- #
class CollaborationForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    email = StringField("Email", validators=[DataRequired(), Email()])
    organization = StringField("Organization", validators=[DataRequired()])
    message = TextAreaField("Message", validators=[DataRequired()])

# Dummy user for login
USER = {"username": "admin", "password": "password123"}

# ------------------- Public Routes ------------------- #
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/acting")
def acting():
    return render_template("acting.html")

@app.route("/production")
def production():
    return render_template("production.html")

@app.route("/collaboration", methods=["GET", "POST"])
def collaboration():
    form = CollaborationForm()
    partners = [
        {"name": "Partner 1", "description": "Film Production House", "logo": "partner1.png"},
        {"name": "Partner 2", "description": "Event Management", "logo": "partner2.png"},
        {"name": "Partner 3", "description": "Cultural Foundation", "logo": "partner3.png"}
    ]

    if form.validate_on_submit():
        # Send email instead of saving to DB
        msg = Message(
            subject="New Collaboration Request",
            recipients=["your_email@gmail.com"],  # replace with the email you want to receive notifications
            body=f"""
New collaboration request received:

Name: {form.name.data}
Email: {form.email.data}
Organization: {form.organization.data}
Message: {form.message.data}
"""
        )
        mail.send(msg)

        flash(f"Thank you {form.name.data}, your collaboration request has been sent!", "success")
        return redirect(url_for("collaboration"))

    return render_template("collab.html", form=form, partners=partners)


@app.route("/contact")
def contact():
    return render_template("contact.html")

# ------------------- Authentication ------------------- #
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        if username == USER["username"] and password == USER["password"]:
            session["user"] = username
            flash("Login successful!")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("You have been logged out.")
    return redirect(url_for("home"))

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

# ------------------- Dashboard ------------------- #
@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "user" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        file = request.files["bill"]
        if file:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(filepath)
            flash(f"Uploaded {filename}")
            # TODO: OCR + DB integration
    bills = os.listdir(UPLOAD_FOLDER)
    return render_template("dashboard.html", bills=bills)


if __name__ == "__main__":
    app.run(debug=True)
