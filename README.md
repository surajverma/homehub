
# HomeHub

A lightweight, self-hosted family dashboard for your home network. Includes notes, shopping list, chores, Who is Home, recipes, expiry tracker, URL shortener, QR generator, media downloader, PDF compressor, and expense tracker—all in a simple web UI.

## Features

- Welcome dashboard with Notice Board and Calendar/Reminders
- Live user switcher, admin/owner controls
- Notes, file uploads, shopping list, chores
- Who is Home status board
- Recipe book, expiry tracker, URL shortener
- QR code generator
- Media downloader (mp3/mp4)
- PDF compressor (Ghostscript)
- Expense tracker with recurring rules

## Setup

### 1. Configure
Edit `config.yml`:

```yaml
instance_name: "My Home Hub"
password: "" # leave blank for no login; set a password to require login
admin_name: "Administrator"
feature_toggles:
  shopping_list: true
  media_downloader: true
  pdf_compressor: true
  qr_generator: true
  notes: true
  file_uploader: true
  who_is_home: true
  chores: true
  recipes: true
  expiry_tracker: true
  url_shortener: true
  expense_tracker: true
family_members:
  - Suraj
  - Dad
  - John
  - Alice
  - Mom
```

### 2. Run with Docker Compose

```yaml
# docker-compose.yml
services:
  homehub:
    container_name: homehub
    image: ghcr.io/surajverma/homehub:latest
    ports:
      - "5005:5005"
    environment:
      - FLASK_ENV=production
    volumes:
      - ./uploads:/app/uploads
      - ./media:/app/media
      - ./pdfs:/app/pdfs
      - ./data:/app/data
      - ./config.yml:/app/config.yml:ro
```

Start:
```powershell
docker compose up -d
```
Open [http://localhost:5005](http://localhost:5005)

### 3. Local Development

Python 3.11 required. To run without Docker:
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

## Usage

- Use the sidebar to access each tool.
- Switch users with the dropdown (top right); admin user can edit the Notice Board and delete any entry.
- “Who is Home” lets each member update their status.
- Expense Tracker supports recurring rules, categories, and monthly summaries.
- All config changes (in `config.yml`) are hot-reloaded—no restart needed.

## Storage

- SQLite DB: `data/app.db`
- User files: `uploads/`, `media/`, `pdfs/`

## Troubleshooting

- If you see missing Python packages, run `pip install -r requirements.txt` in your venv.
- For Docker issues, check container logs: `docker compose logs homehub`
- To reset, stop containers and delete `data/app.db` (removes all data).

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! If you have any ideas, suggestions, or bug reports, please open an issue or submit a pull request.

## Thank You
If you like my work, you can [buy me a coffee ☕](https://ko-fi.com/skv)