import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from postgrest import APIError
from supabase import Client, create_client

load_dotenv()

app = Flask(__name__)

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
supabase: Client | None = (
    create_client(supabase_url, supabase_key) if supabase_url and supabase_key else None
)


@app.after_request
def allow_cross_origin(response):
    """Allow browser clients from any origin to call this API."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def db():
    if supabase is None:
        raise RuntimeError("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY must be configured")
    return supabase


def api_error(error, status=500):
    message = getattr(error, "message", str(error))
    return jsonify({"error": message}), status


@app.get("/")
def index():
    return jsonify({"message": "Events API", "endpoints": [
        "/api/events", "/api/events/<id>", "/api/registrations",
        "/api/me/registrations", "/api/events/<id>/registrations",
    ]})


@app.get("/api/events")
def list_events():
    try:
        result = db().table("events").select("*").execute()
        return jsonify(result.data)
    except (APIError, RuntimeError) as error:
        return api_error(error)


@app.get("/api/events/<event_id>")
def get_event(event_id):
    try:
        result = db().table("events").select("*").eq("id", event_id).limit(1).execute()
        if not result.data:
            return jsonify({"error": "Event not found"}), 404
        return jsonify(result.data[0])
    except (APIError, RuntimeError) as error:
        return api_error(error)


@app.post("/api/registrations")
def create_registration():
    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id")
    event_id = payload.get("event_id")
    if not user_id or not event_id:
        return jsonify({"error": "user_id and event_id are required"}), 400

    try:
        client = db()
        event_result = client.table("events").select("id, capacity").eq("id", event_id).limit(1).execute()
        if not event_result.data:
            return jsonify({"error": "Event not found"}), 404

        existing = (client.table("registrations").select("id")
                    .eq("user_id", user_id).eq("event_id", event_id).limit(1).execute())
        if existing.data:
            return jsonify({"error": "User is already registered for this event"}), 409

        capacity = event_result.data[0].get("capacity")
        if capacity is not None:
            count_result = (client.table("registrations").select("id", count="exact")
                            .eq("event_id", event_id).execute())
            if count_result.count is not None and count_result.count >= capacity:
                return jsonify({"error": "Event is full"}), 409

        created = client.table("registrations").insert({
            "user_id": user_id,
            "event_id": event_id,
        }).execute()
        return jsonify(created.data[0] if created.data else {}), 201
    except APIError as error:
        # A unique constraint can reject simultaneous duplicate registrations.
        code = getattr(error, "code", None)
        return api_error(error, 409 if code == "23505" else 500)
    except RuntimeError as error:
        return api_error(error)


@app.get("/api/me/registrations")
def my_registrations():
    # With authentication intentionally disabled, callers identify themselves
    # by passing their user UUID as a query parameter.
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id query parameter is required"}), 400
    try:
        result = (db().table("registrations").select("*, events(*)")
                  .eq("user_id", user_id).execute())
        return jsonify(result.data)
    except (APIError, RuntimeError) as error:
        return api_error(error)


@app.get("/api/events/<event_id>/registrations")
def event_registrations(event_id):
    # This is only a caller-supplied organizer check, not authentication.
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id query parameter is required"}), 400
    try:
        client = db()
        event_result = (client.table("events").select("id, organizer_id")
                        .eq("id", event_id).limit(1).execute())
        if not event_result.data:
            return jsonify({"error": "Event not found"}), 404
        if event_result.data[0].get("organizer_id") != user_id:
            return jsonify({"error": "Only the event organizer can view attendees"}), 403
        result = (client.table("registrations")
                  .select("*, users(id, email, role)")
                  .eq("event_id", event_id).execute())
        return jsonify(result.data)
    except (APIError, RuntimeError) as error:
        return api_error(error)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
