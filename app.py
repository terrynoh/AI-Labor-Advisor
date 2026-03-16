# -*- coding: utf-8 -*-
import os
from flask import Flask, request, jsonify, render_template, session
from chatbot import chat, get_initial_message
from calculators import calculate_severance, calculate_leave

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")


@app.route("/")
def index():
    session.clear()
    session["messages"] = []
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat_endpoint():
    user_input = request.json.get("message", "").strip()
    if not user_input:
        return jsonify({"error": "빈 메시지"}), 400

    messages = session.get("messages", [])
    reply, updated, error = chat(messages, user_input)

    if error:
        return jsonify({"error": error}), 500

    session["messages"] = updated
    turn_count = len([m for m in updated if m["role"] == "user"])
    return jsonify({
        "reply": reply,
        "turns_left": 20 - turn_count
    })


@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    session["messages"] = []
    return jsonify({"reply": get_initial_message()})


@app.route("/calculate/severance", methods=["POST"])
def severance():
    data = request.json
    amount, detail = calculate_severance(data["salary"], data["years"])
    return jsonify({"amount": amount, "detail": detail})


@app.route("/calculate/leave", methods=["POST"])
def leave():
    data = request.json
    payout = calculate_leave(data["salary"], data["unused_days"])
    return jsonify({"payout": payout})


if __name__ == "__main__":
    app.run(debug=True)