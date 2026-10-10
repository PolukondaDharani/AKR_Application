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