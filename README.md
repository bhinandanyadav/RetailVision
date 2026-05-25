# RetailVision

AI/ML project + web application for customer tracking and heatmap generation.

## Run locally

1. Create and activate your virtual environment.
 ```bash
python -m venv venv
```
```bash
venv\Scripts\activate
```
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in the values you need.
4. Start the app:

```bash
python app.py
```

## Production deploy

This project is not production-ready until you provide the model weights and environment variables.

Recommended deployment flow:

1. Set `FLASK_DEBUG=0`.
2. Set `STORE_TRACKER_SECRET` to a strong random value.
3. Provide the YOLO weights file named in `MODEL_WEIGHTS`.
4. Run the app behind a production server.

Windows:

```bash
waitress-serve --host=0.0.0.0 --port=5000 wsgi:app
```

Linux/macOS:

```bash
gunicorn -w 2 -b 0.0.0.0:5000 wsgi:app
```

If you use a hosting platform, make sure the weights file is present on the server and the environment variables are configured before start-up.
