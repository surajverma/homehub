# Reminders in your calendar app

HomeHub can publish your reminders as a calendar subscription link (an `.ics` feed). Calendar apps on your phone or computer follow the link, show the reminders next to your other events, alert you when they're due, and pick up changes on their own.

It's one-way: you add and change reminders in HomeHub. Nothing is sent to, or read from, any other calendar, and changes made in a calendar app don't come back.

## What's in the feed

- Reminders from 60 days ago to a year ahead.
- Recurring reminders appear as their individual dates, exactly the dates HomeHub's own calendar shows (including month-end dates such as the 31st becoming the 30th).
- Reminders marked done stay in the feed but don't alert. Reminders in the trash are left out.
- Chores and expenses aren't included.

## Turn it on

1. Make a long random token:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. Add it to `config.yml`. HomeHub reads the change on the next request, so no restart is needed:

   ```yaml
   calendar_feed:
     token: "paste-the-token-here"   # at least 16 characters; empty turns the feed off
     alerts: true                    # false = events without phone alerts
   ```

3. Your link is:

   ```
   http://<your-homehub-address>:<port>/calendar.ics?token=<token>
   ```

   Use the address your devices use to open HomeHub: an IP address, or a local name if your network has one. Open the link in a browser first; you should see calendar text. A wrong or missing token gets a plain "not found", as does every link while the feed is off.

The link works with or without a site password, because calendar apps can't log in. The token is what protects it.

## Subscribe

Always choose **subscribe** (also called "from URL" or "on the network"). **Import** or "upload from file" copies the events into your calendar once: they never update, and you'd have to delete each one by hand to get rid of them.

### iPhone and iPad

1. Settings → Calendar → Accounts → Add Account → Other → **Add Subscribed Calendar**. (On newer iOS versions, Calendar is under Settings → Apps.)
2. Paste the link and tap Next, then Save.
3. **To get alerts:** open the new subscription in the same Accounts list and turn **Remove Alerts** off. It's on by default for subscribed calendars, and while it's on, HomeHub's alerts are ignored.

The iPhone fetches the feed itself, so this works on your home network. Away from home it keeps the last copy it fetched, or keeps updating if you're connected through a VPN such as Tailscale.

### Apple Calendar on Mac

1. File → **New Calendar Subscription**, paste the link, and click Subscribe.
2. Set **Location** to **On My Mac**. With iCloud as the location, Apple's servers fetch the feed instead of your Mac, and they can't reach a HomeHub on your home network.
3. For alerts, untick **Alerts** next to "Remove".
4. Pick how often it refreshes (every hour is a good choice).

### Android (ICSx⁵)

1. Install [ICSx⁵](https://icsx5.bitfire.at/) from F-Droid or Google Play.
2. Tap +, paste the link, and follow the steps.
3. Set how often it syncs in ICSx⁵'s settings.

The events then show in the Google Calendar app, and any other calendar app, on that phone. ICSx⁵ keeps the feed's alerts. The events stay on that phone: they aren't added to your Google account, so they don't appear on calendar.google.com or your other devices.

### Thunderbird

1. In the calendar, choose New Calendar → **On the Network**.
2. Paste the link as the location. If asked for a format, choose iCalendar (ICS).
3. To get alerts, make sure **Show Reminders** is ticked in the calendar's properties.

### Google Calendar website and Outlook

Google Calendar on the web ("Other calendars → From URL") and Outlook (new Outlook, Outlook on the web, and classic Outlook with an Outlook.com or Microsoft 365 account) don't fetch the feed from your device. Google's or Microsoft's servers fetch it. Those servers can't reach a HomeHub that's only on your home network, whether you give them `localhost`, an IP address or a local domain name. The feed works; it's just out of their reach.

They only work if your HomeHub is reachable from the internet over HTTPS, for example through a reverse proxy or a tunnel. HomeHub is built for a home network and isn't hardened for the internet, so this isn't recommended. Google also refreshes subscribed calendars slowly, often only every 12-24 hours.

On Android, ICSx⁵ is the way to see HomeHub reminders in the Google Calendar app without any of this.

## Alerts

With `alerts: true` (the default), each event carries one alert:

- Reminders with a time alert at their start time.
- All-day reminders alert at 09:00 on their first day, in the device's own time zone.
- Reminders already marked done don't alert.

Set `alerts: false` to send the events without alerts. Each app still decides whether to honour them: iPhone and Mac subscriptions have a "Remove Alerts" setting (see above), and apps may skip alerts for events in the past.

## Times

- Reminders with a time use the container's `TZ` setting (see "Timezone" in the README). Without `TZ`, times are sent as "floating" times, which calendar apps show at the same clock time in whatever time zone the device is in.
- Reminders without a start or end time show as all-day events, across every day from the start date to the end date.
- A reminder with a start time but no end lasts 30 minutes.
- A reminder with an end time but no start time starts at midnight on its start date, the same way HomeHub reads it.
- A reminder with an end date but no end time runs to the end of that day.

## Refreshing

The feed asks apps to check for changes about every hour. Each app decides for itself how often it actually does, and most let you set it.

## Keeping the link private

- Anyone who has the link can read your reminders, including their descriptions and who added them. Share it only with the devices that need it.
- The full link, token included, appears in HomeHub's access log (`docker logs homehub`) and in the logs of any proxy in front of HomeHub.
- If the link leaks, change the token in `config.yml`. Old links stop working straight away, and each device needs the new link.
