# RetailVision

RetailVision is an AI/ML + web application for customer tracking and heatmap generation.

## What you need

- Python 3.10+ installed
- A YOLO weights file (see Configuration)

## Local setup

1. Create and activate a virtual environment:

```bash
python -m venv venv
```

Windows:

```bash
venv\Scripts\activate
```

Linux/macOS:

```bash
source venv/bin/activate
```

<<<<<<< HEAD
=======
1. Create and activate your virtual environment.
 ```bash
python -m venv venv
```
```bash
venv\Scripts\activate
```
>>>>>>> 55729b926afa92d6fb674dac30a64ed6eccbe19c
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create your environment file:

```bash
copy .env.example .env
```

4. Update `.env` with your values (see Configuration).

5. Start the app:

```bash
python app.py
```

Open http://127.0.0.1:5000 in your browser.

## Configuration

The app reads settings from `.env`. At minimum, set:

- `MODEL_WEIGHTS`: Path to your YOLO weights file.
- `STORE_TRACKER_SECRET`: A strong secret key for session security.

Recommended for local development:

- `FLASK_DEBUG=1`

Recommended for production:

- `FLASK_DEBUG=0`

## Production deploy

This project is not production-ready until you provide the model weights file and required environment variables.

Suggested steps:

1. Set `FLASK_DEBUG=0` in `.env`.
2. Set `STORE_TRACKER_SECRET` to a strong random value.
3. Ensure `MODEL_WEIGHTS` points to a valid file on the server.
4. Run the app behind a production server.

Windows:

```bash
waitress-serve --host=0.0.0.0 --port=5000 wsgi:app
```

Linux/macOS:

```bash
gunicorn -w 2 -b 0.0.0.0:5000 wsgi:app
```

If you use a hosting platform, make sure the weights file is present and the environment variables are configured before start-up.

## Troubleshooting

- App fails to start with missing weights: confirm `MODEL_WEIGHTS` in `.env` points to an existing file.
- Session or login issues: set a strong `STORE_TRACKER_SECRET` and restart the app.
- Import errors after install: ensure the virtual environment is active and run `pip install -r requirements.txt` again.

## Notes

- Do not commit your `.env` or model weights file to version control.
