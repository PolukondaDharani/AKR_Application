from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "supersecret"  # replace in production

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

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

@app.route("/hire", methods=["GET", "POST"])
def hire():
    if request.method == "POST":
        name = request.form["name"]
        role = request.form["role"]
        flash(f"Hiring request submitted by {name} for {role}")
        # TODO: Save to DB or send email
        return redirect(url_for("hire"))
    return render_template("hire.html")

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
