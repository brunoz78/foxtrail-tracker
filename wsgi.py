# -*- coding: utf-8 -*-
"""WSGI-Einstieg fuer gunicorn:  gunicorn -b 127.0.0.1:8080 wsgi:app"""
from foxtrail import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
