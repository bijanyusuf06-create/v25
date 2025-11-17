#!/bin/bash
pip install -r requirements.txt
gunicorn -b 0.0.0.0:8080 bot:app_flask
