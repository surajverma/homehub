[![CI/CD](https://github.com/surajverma/homehub/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/surajverma/homehub/actions/workflows/docker-publish.yml)
![Latest Release](https://img.shields.io/github/v/release/surajverma/homehub?include_prereleases)
[![GitHub last commit](https://img.shields.io/github/last-commit/surajverma/homehub)](https://github.com/surajverma/homehub/commits/main)
[![GitHub issues](https://img.shields.io/github/issues/surajverma/homehub)](https://github.com/surajverma/homehub/issues)
[![GitHub closed issues](https://img.shields.io/github/issues-closed/surajverma/homehub?color=brightgreen)](https://github.com/surajverma/homehub/issues?q=is%3Aissue+is%3Aclosed)
[![GitHub issues by-label](https://img.shields.io/github/issues/surajverma/homehub/in%20progress?color=darkgreen)](https://github.com/surajverma/homehub/issues?q=is%3Aissue+is%3Aopen+label%3A"in+progress")
[![GitHub Stars](https://img.shields.io/github/stars/surajverma/homehub?style=social)](https://github.com/surajverma/homehub/stargazers)

> **Maintainer Note**  
> Thank you for your interest in this project! I originally started it as a personal utility and never expected it to grow so quickly—I’m genuinely thrilled and grateful that it’s become helpful to you and your family.  
>  
> Please note that I am currently the sole maintainer and manage this repository alongside a full-time job, which means the time I can give to this project is somewhat limited. Responses to issues, pull requests, or questions may be delayed, especially during busy periods at work or at home.  
>  
> I typically work on the project after office hours or on weekends, depending on my availability and energy. Your patience, understanding, and support mean a lot—thank you for helping make this project better!


# 🏡 HomeHub: Your All-In-One Family Dashboard

Ever wanted a simple, private spot on your home network for your family's daily stuff? That's HomeHub. It's a lightweight, self-hosted web app that turns any computer (even a Raspberry Pi!) into a central hub for shared notes, shopping lists, chores, a media downloader, and even a family expense tracker.

It’s designed to be easy to use for everyone in the family, with a clean interface that works great on any device.

## What Can It Do?

HomeHub is packed with useful tools to make family life a little more organized:

* **📝 Shared Notes**: A simple place to jot down quick notes for everyone to see.
* **☁️ Shared Cloud**: Easily upload and share files across your home network.
* **🛒 Shopping List**: A collaborative list so you never forget the milk again. Comes with suggestions based on your history!
* **✅ Chore Tracker**: A simple to-do list for household tasks.
* **🗓️ Calendar & Reminders**: A shared calendar to keep track of important dates.
* **👋 Who's Home?**: See at a glance who is currently home.
* **💰 Expense Tracker**: A powerful tool to track family spending, with support for recurring bills like newspapers, milk, or subscriptions.
* **🎬 Media Downloader**: Save videos or music from popular sites directly to your server.
* ...and more, including a **Recipe Book**, **Expiry Tracker**, **URL Shortener**, **PDF Compressor**, and **QR Code Generator**!

## Salient Features
* **Private & Self-Hosted**: All your data stays on your network. No cloud, no tracking.
* **Simple & Lightweight**: Runs smoothly on minimal hardware.
* **Family-Focused**: Designed to be intuitive for users of all technical skill levels.
* **Customizable**: Toggle features on or off and even change the color theme right from the `config.yml` file.

![homehub](https://github.com/user-attachments/assets/55b1c580-8897-4073-9e51-2a892a2bdcd4)

## Getting Started is Easy

The best way to run HomeHub is with Docker. It's quick and keeps everything tidy

1. First, copy the `config-example.yml` to `config.yml`. This is where you'll name your hub and add family members. You can also set an optional password to protect the whole site.

```yaml
instance_name: "My Home Hub"
password: "" #leave blank for password less access
admin_name: "Administrator"
feature_toggles:
  shopping_list: true
  media_downloader: true
  pdf_compressor: true
  qr_generator: true
  notes: true
  shared_cloud: true
  who_is_home: true
  personal_status: true
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

reminders:
  # time_format controls how reminder times are displayed in the UI.
  # Allowed values: "12h" (default) or "24h". Remove or leave blank to fall back to 12h.
  time_format: 12h

  # calendar_start_day controls which day the reminders calendar starts on.
  # Accepts full weekday names (sunday, saturday).  
  calendar_start_day: monday #default is Sunday, comment this line to switch to default

  # Example reminder categories (keys lowercase no spaces recommended)
  categories:
    - key: health
      label: Health
      color: "#dc2626"
    - key: bills
      label: Bills
      color: "#0d9488"
    - key: school
      label: School
      color: "#7c3aed"
    - key: family
      label: Family
      color: "#2563eb"
theme:
  primary_color: "#1d4ed8"
  secondary_color: "#a0aec0"
  background_color: "#f7fafc"
  card_background_color: "#fff"
  text_color: "#333"
  sidebar_background_color: "#2563eb"
  sidebar_text_color: "#ffffff"
  sidebar_link_color: "rgba(255,255,255,0.95)"
  sidebar_link_border_color: "rgba(255,255,255,0.18)"
  sidebar_active_color: "#3b82f6"
```

**2. Run with Docker Compose**

Use the provided `compose.yml` file to get started in seconds:

```yaml
# compose.yml
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

```bash
docker compose up -d
```
That's it! Open your browser and head to [http://localhost:5000](http://localhost:5000)

## Theming

HomeHub follows your system dark/light mode. You can customize colors via `config.yml > theme`.

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

## Development Setup

To contribute or run & build HomeHub locally, follow these steps:

### 1. Clone the Repository
```bash
git clone https://github.com/surajverma/homehub.git
cd homehub
```

### 2. Python Environment Setup
```bash
python -m venv venv
venv\Scripts\activate  # On Windows
pip install -r requirements.txt
```

### 3. Configuration
- Copy `config-example.yml` to `config.yml` and edit as needed for your family, features, and theme.

### 4. CSS Build (Tailwind + Custom Styles)
```bash
npm install
npm run build:css
```
- For live CSS rebuilds during development:
```bash
npm run watch:css
```

### 5. Running the App
- **With Docker (recommended):**
  ```bash
  docker compose up -d
  ```
- **Locally (for development):**
  ```bash
  python run.py
  ```
  (Ensure you have built CSS and set up your config.)

### 6. Troubleshooting
- If you see missing dependency errors, ensure you have run both `pip install -r requirements.txt` and `npm install`.
- If port 5000 is in use, stop the conflicting service or change the port in `compose.yml` and `config.yml`.
- For Docker issues, try `docker compose down` then `docker compose up -d`.


## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are always welcome! If you have any ideas, suggestions, or bug reports, please open an issue or submit a pull request.


## Have Fun!

This project was built to be a practical tool for my own family, and I hope it's useful for yours too.

If you find HomeHub useful, you can [buy me a coffee ☕](https://ko-fi.com/skv).
