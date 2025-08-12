# dashboard.py — opcional, se preferir separar (não é usado no main "porcão")
from flask import Flask, jsonify

def make_dashboard(app: Flask, snapshot: dict):
    @app.get("/dash")
    def dash():
        return jsonify(snapshot)
