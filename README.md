
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

![homehub](https://github.com/user-attachments/assets/0a170e1c-d21d-4902-ba3d-c0c58bbccbee)


## Getting Started is Easy

The best way to run HomeHub is with Docker. It's quick and keeps everything tidy

1. First, copy the `config-example.yml` to `config.yml`. This is where you'll name your hub and add family members. You can also set an optional password to protect the whole site.

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
  shared_cloud: true
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

## Development Setup

If you're developing HomeHub locally and want to work with styles:

### CSS Build Process

HomeHub uses Tailwind CSS with custom styles consolidated into a single minified output file. All custom CSS (formerly `custom.css`) is now integrated into `static/input.css` for a streamlined build process.

1. **Install Node.js dependencies:**
   ```bash
   npm install
   ```

2. **Build CSS (production):**
   ```bash
   npm run build:css
   ```
   This generates `static/output.css` - a minified file containing Tailwind utilities + all custom styles.

3. **Watch mode for development:**
   ```bash
   npm run watch:css
   ```
   This rebuilds `static/output.css` automatically when you change templates or modify `static/input.css`.

4. **Automated builds:**
   - **Local Docker:** Dockerfile includes Node.js and runs `npm run build:css` during image creation
   - **CI/CD:** GitHub Actions workflow pre-builds CSS before Docker build for faster deployments

### File Structure
- `static/input.css` - Tailwind directives + all custom styles (edit this for styling changes)
- `static/output.css` - Built & minified CSS (git-ignored, auto-generated)
- `static/custom.css` - **Deprecated** (now consolidated into input.css, git-ignored)
- `tailwind.config.js` - Tailwind configuration
- `package.json` - Node dependencies and build scripts

**Note:** Only `output.css` is loaded by the application. Custom styles are now part of the Tailwind build pipeline for optimal minification and caching.

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


## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are always welcome! If you have any ideas, suggestions, or bug reports, please open an issue or submit a pull request.


## Have Fun!

This project was built to be a practical tool for my own family, and I hope it's useful for yours too.

If you find HomeHub useful, you can [buy me a coffee ☕](https://ko-fi.com/skv).
