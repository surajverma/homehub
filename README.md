
# HomeHub

A lightweight, self-hosted family utility hub for your home network. Includes notes, shopping list, chores, Who is Home, recipes, expiry tracker, URL shortener, QR generator, media downloader, PDF compressor, and expense tracker—all in a simple responsive web UI.

## Features

- Dashboard with Notice Board and Calendar/Reminders
- Live user switcher, admin/owner controls
- Notes, file uploads, shopping list, chores
- Who is Home status board on welcome screen/dashboard
- Recipe book, expiry tracker, URL shortener
- QR code generator
- Media downloader (mp3/mp4)
- PDF compressor
- Expense tracker with recurring rules (for recurring payments like milk/newspaper/utility bills etc.)

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
  - Mom
  - Dad
  - Dipanshu
  - Vivek
  - India
```

### 2. Run with Docker Compose

```yaml
# docker-compose.yml
services:
  homehub:
    container_name: homehub
    image: ghcr.io/surajverma/homehub:latest
    ports:
      - "5000:5000" #app listens internally on port 5000
    environment:
      - FLASK_ENV=production
      - SECRET_KEY=${SECRET_KEY:-} # set via .env; falls back to random if not provided
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
Open [http://localhost:5000](http://localhost:5000)

### 3. Local Development

Python 3.12 required. To run without Docker:
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

### Security notes

- App authentication is optional, controlled via `config.yml > password`. If you set a password, it’s hashed on startup and the plain value is removed from memory.
- Set a strong `SECRET_KEY` in production. With Docker, create a `.env` file next to your compose file:

```env
SECRET_KEY=generate-a-long-random-string-here
```

Compose picks it up automatically. If unset, the app generates a random key at runtime (sessions will invalidate when the container restarts).

## Usage

- Use the sidebar to access each tool.
- Switch users with the dropdown (top right); admin user can edit the Notice Board and delete any entry.
- “Who is Home” lets each member update their status.
- Expense Tracker supports recurring rules, categories, and monthly summaries.
- All config changes (in `config.yml`) are hot-reloaded—no restart needed.

## Theming

HomeHub follows your system dark/light mode automatically (using `prefers-color-scheme`) and updates live without refresh. You can customize colors via `config.yml > theme`. No rebuild is needed; changes hot-reload on the next request.

Configurable keys:

```yaml
theme:
  # Accent colors
  primary_color: "#1d4ed8"
  secondary_color: "#a0aec0"

  # Surfaces & text
  background_color: "#f7fafc"
  card_background_color: "#ffffff"
  text_color: "#333333"

  # Sidebar palette
  sidebar_background_color: "#2563eb"
  sidebar_text_color: "#ffffff"                # text color used for the sidebar title and labels
  sidebar_link_color: "rgba(255,255,255,0.95)" # link text color in sidebar items
  sidebar_link_border_color: "rgba(255,255,255,0.18)" # subtle border around sidebar links
```

Tips:
- Want higher contrast in the sidebar? Increase `sidebar_link_border_color` opacity (e.g., `rgba(255,255,255,0.3)`).
- Prefer lighter/darker accents? Tweak `primary_color` and `secondary_color`.
- Dark mode palette adapts automatically; the variables above apply to light mode, while dark mode uses tuned counterparts for good contrast.

Advanced: You can further adjust styles in `static/custom.css`. Those styles read the same CSS variables emitted from `config.yml`.

## Storage

- SQLite DB: `data/app.db`
- User files: `uploads/`, `media/`, `pdfs/`

Back up `data/` regularly if the instance holds important data.

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