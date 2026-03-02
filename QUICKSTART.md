# Quick Start Guide - Book Stitch

## Goal
Get your reading progress syncing across services in 10 minutes.

---

## Step 1: Choose Your Services

Book Stitch syncs progress between any combination of these services:

| Service | Type | What It Does |
|---------|------|-------------|
| **Audiobookshelf** | Audiobook server | Tracks audiobook listening progress |
| **KOSync** | E-reader sync | Syncs KOReader/Calibre reading position |
| **Storyteller** | Ebook server | Tracks ebook reading progress |
| **Booklore** | Library manager | Ebook organization and shelf management |
| **Hardcover** | Social reading | Updates reading status and page progress |

You need **at least two services** to sync between. Common setups:

- **Audiobook + Ebook**: Audiobookshelf + KOSync (the classic setup)
- **Ebook-only**: KOSync + Storyteller or KOSync + Booklore
- **Audio-only**: Audiobookshelf + Hardcover

---

## Step 2: Prepare Your Folders

Create a directory for the app:
```bash
mkdir ~/book-stitch
cd ~/book-stitch
```

---

## Step 3: Create docker-compose.yml

Copy this template and fill in the services you use:

```yaml
services:
  book-stitch:
    image: ghcr.io/your-org/book-stitch:latest
    container_name: book_stitch
    restart: unless-stopped

    environment:
      # Audiobookshelf (optional — enable if you use ABS)
      # - ABS_SERVER=https://YOUR_ABS_SERVER.com
      # - ABS_KEY=YOUR_API_TOKEN_HERE

      # KOSync (optional — enable if you use KOReader)
      # - KOSYNC_ENABLED=true
      # - KOSYNC_SERVER=https://YOUR_KOSYNC_SERVER.com/api/koreader
      # - KOSYNC_USER=YOUR_USERNAME
      # - KOSYNC_KEY=YOUR_PASSWORD
      # - KOSYNC_HASH_METHOD=content

      # General settings
      - TZ=America/New_York
      - LOG_LEVEL=INFO
      - SYNC_PERIOD_MINS=5

    volumes:
      - ./data:/data
      # Mount your ebooks directory if using local files:
      # - /path/to/your/ebooks:/books

    ports:
      - "4477:4477"
```

Uncomment and fill in the sections for your services. Additional integrations (Storyteller, Booklore, Hardcover, etc.) can be configured in the web UI after startup.

---

## Step 4: Start the Container

```bash
docker compose up -d
```

Check if it's running:
```bash
docker compose logs -f
```

Look for connection success messages for your configured services.

Press `Ctrl+C` to exit logs.

---

## Step 5: Open the Web UI

Open your browser to: **http://localhost:4477**

You should see the Book Stitch dashboard.

---

## Step 6: Configure in the Web UI

1. Go to **Settings** to enable and configure your services
2. Click **"Single Match"** to create your first book mapping
3. Select an audiobook, ebook, or both depending on your setup
4. Click **"Create Mapping"**

That's it! The sync will start automatically.

---

## Success!

Your progress should now sync between your configured services.
The system checks every 5 minutes by default.

---

## Setup Paths

### Audiobook + Ebook (Linked)
1. Configure Audiobookshelf (ABS_SERVER + ABS_KEY)
2. Configure KOSync or mount /books volume
3. Use "Single Match" to pair an audiobook with its ebook
4. Progress syncs both directions

### Ebook-Only
1. Configure at least one ebook source (KOSync, Storyteller, or Booklore)
2. Use "Single Match" > "Ebook Only" to import an ebook
3. Progress syncs between all configured ebook services

### Audio-Only
1. Configure Audiobookshelf
2. Optionally enable Hardcover for social tracking
3. Use "Single Match" > "Audio Only" to import an audiobook
4. Progress syncs to Hardcover (if configured)

---

## Troubleshooting

### Container won't start?
```bash
docker compose logs
```
Look for error messages about API keys or server connections.

### Can't access web UI?
- Check if port 4477 is available: `docker compose ps`
- Try http://localhost:4477 or http://YOUR_SERVER_IP:4477

### Sync not working?
- Wait 5 minutes (default sync period)
- Check the dashboard - does it show progress?
- Make sure you're using the same ebook file in both systems

---

## What's Next?

Once basic sync is working, you can add more integrations via the Settings page:
- **Storyteller** for three-way sync
- **Booklore** for shelf organization
- **Hardcover** for social reading tracking

See the full README.md for advanced features.

---

## Need Help?

- Check the logs: `docker compose logs -f`
- Read the full README.md
- Open an issue on GitHub with your logs
