<p align="center">
  <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/c/c6/Moodle-logo.svg/1280px-Moodle-logo.svg.png" alt="Moodle Monitor Bot" width="280">
</p>

<h1 align="center">Moodle Monitor Bot</h1>

<p align="center">
  A feature-rich Telegram bot that monitors your Moodle LMS — tracking courses, assignments, deadlines, and sending smart reminders so you never miss a submission.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?logo=telegram&logoColor=white" alt="Telegram Bot API">
  <img src="https://img.shields.io/badge/Moodle-LMS-F98012?logo=moodle&logoColor=white" alt="Moodle">
</p>

---

## Features

### Core Monitoring
- **Enrolled Courses** — View all your active Moodle courses instantly
- **Assignment Tracking** — Fetches upcoming assignments with due dates, submission links, and completion status
- **Auto Sync** — Background sync every 10 minutes detects new courses, files, and assignments
- **New Content Alerts** — Get notified the moment a new assignment or course material is posted

### Smart Reminders
- **Tiered Deadline Alerts** — Automatic reminders at 24h, 12h, 6h, 3h, 1h, 30min, and 10min before deadline
- **Daily Summary** — 9 AM and 9 PM daily reminders for assignments due within 24 hours
- **Snooze & Mute** — Snooze reminders (30min to 6h) or mute specific assignments entirely
- **Custom Snooze** — Set a precise snooze date and time in `DD/MM/YYYY HH:MM` format
- **Completion Detection** — Automatically detects when you submit an assignment and stops reminders

### Todo System
- **Add Todos** — Batch-add personal todos with due dates in Indian format (`DD/MM/YYYY hh:mm AM/PM`)
- **List / Edit / Delete** — Full CRUD with inline keyboard controls
- **Mark Complete** — Toggle between Done and Pending with one tap
- **Todo Reminders** — Same tiered reminder system as assignments

### Email Notifications
- **OTP Verification** — Secure email setup with 6-digit OTP
- **HTML Emails** — Professional styled email notifications with orange theme
- **Dual Channel** — Receive alerts on both Telegram and email simultaneously
- **Per-Channel Control** — Enable or disable Telegram and email notifications independently

### Notification Preferences
- **Telegram Notifications** — Toggle on/off
- **Email Notifications** — Toggle on/off
- **Independent Control** — Manage each channel separately via inline buttons

### Admin Panel
- **User Management** — View all registered users, block/unblock accounts, delete users
- **Bot Statistics** — Total users, active sessions, blocked count at a glance
- **Password Protected** — Admin access requires username + password authentication

### Security & Reliability
- **Session Persistence** — Moodle sessions are saved and reused; auto re-login on expiry
- **State Persistence** — Conversation states, OTP data, and admin sessions survive bot restarts
- **Atomic File Writes** — All JSON saves use temp-file + replace to prevent data corruption
- **Retry Adapter** — Telegram API calls auto-retry on network failures (5 retries with exponential backoff)
- **Graceful Polling** — Infinite polling with automatic crash recovery

---

## Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and quick start guide |
| `/help` | Full list of commands and features |
| `/signup` | Register your Moodle account |
| `/reset_password` | Update your Moodle password |
| `/set_email` | Set or change email for notifications |
| `/enrolled_courses` | View your enrolled courses |
| `/pending_assignments` | View upcoming assignments with deadlines |
| `/sync` | Trigger a manual sync with Moodle |
| `/status` | Check your account and sync status |
| `/notification_preferences` | Toggle Telegram / Email notifications |
| `/add_todo` | Add personal todos with due dates |
| `/list_todo` | View all your todos |
| `/edit_todo` | Edit title, due date, or mark complete |
| `/delete_todo` | Remove a todo by number |
| `/logout` | Log out and clear your session |
| `/delete_account` | Permanently delete your account and data |
| `/admin_panel` | Admin dashboard (admin only) |
| `/cancel` | Cancel any ongoing operation |

---

## Project Structure

```
MOODLE/
├── bot.py              # Telegram bot — commands, callbacks, reminders, emails
├── Moodle.py           # Moodle scraping engine — login, API calls, data storage
├── bot_config.json     # Configuration — bot token, SMTP settings, admin credentials
├── README.md
└── moodle_data/        # Runtime data (auto-created)
    ├── users.json          # Registered Moodle accounts + sessions
    ├── bot_users.json      # Telegram chat_id ↔ Moodle username mapping
    ├── conv_states.json    # Conversation state persistence
    ├── otp_store.json      # Pending email OTPs
    ├── admin_sessions.json # Admin authentication sessions
    └── <USERNAME>/         # Per-user subfolder
        ├── data.json           # Course snapshot (sections + files)
        ├── assignments.json    # Assignments + completion state
        ├── reminders.json      # Reminder/snooze/mute state
        └── todos.json          # Personal todos
```

---

## Setup

### Prerequisites

- **Python 3.10+**
- A **Telegram Bot Token** from [@BotFather](https://t.me/BotFather)
- **Moodle LMS** access credentials

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/moodle-monitor-bot.git
   cd moodle-monitor-bot
   ```

2. **Install dependencies**
   ```bash
   pip install pyTelegramBotAPI requests beautifulsoup4
   ```

3. **Configure the bot**

   Edit `bot_config.json`:

   ```json
   {
       "bot_token": "YOUR_TELEGRAM_BOT_TOKEN",
       "sync_interval_minutes": 10,
       "admin_telegram_username": "YOUR_TELEGRAM_USERNAME",
       "admin_password": "YOUR_ADMIN_PASSWORD",
       "email": {
           "enabled": false,
           "smtp_host": "smtp.gmail.com",
           "smtp_port": 587,
           "sender_email": "your_email@gmail.com",
           "sender_password": "your_app_password",
           "from_name": "Moodle Monitor Bot"
       }
   }
   ```

   > **Email setup:** For Gmail, generate an [App Password](https://myaccount.google.com/apppasswords) and use it as `sender_password`. Set `enabled` to `true` to activate email notifications.

4. **Run the bot**
   ```bash
   python bot.py
   ```

---

## Configuration

| Key | Type | Description |
|---|---|---|
| `bot_token` | `string` | Telegram Bot API token |
| `sync_interval_minutes` | `int` | Background sync interval in minutes (default: 10) |
| `admin_telegram_username` | `string` | Telegram username for admin access (without @) |
| `admin_password` | `string` | Password required to access the admin panel |
| `email.enabled` | `bool` | Enable or disable email notifications globally |
| `email.smtp_host` | `string` | SMTP server hostname |
| `email.smtp_port` | `int` | SMTP server port (587 for TLS) |
| `email.sender_email` | `string` | Email address to send notifications from |
| `email.sender_password` | `string` | SMTP password or app-specific password |
| `email.from_name` | `string` | Display name for outgoing emails |

---

## How It Works

```
User ←→ Telegram Bot API ←→ bot.py ←→ Moodle.py ←→ Moodle LMS
                                ↓
                          moodle_data/
                        (JSON storage)
```

1. **User signs up** via `/signup` — bot logs into Moodle, saves session cookies
2. **Background sync** runs every 10 minutes — fetches courses, assignments, and course materials
3. **Change detection** — compares new data with stored snapshots; sends alerts for new items
4. **Reminder loop** runs every 60 seconds — checks all assignments and todos against deadline thresholds
5. **Session management** — automatically re-authenticates when Moodle sessions expire

---

## Tech Stack

| Component | Technology |
|---|---|
| Bot Framework | [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI) (telebot) |
| Web Scraping | [Requests](https://requests.readthedocs.io/) + [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) |
| Data Storage | JSON files with atomic writes |
| Email | SMTP with HTML templates |
| Timezone | IST (UTC+5:30) |
| Network Resilience | urllib3 Retry adapter with exponential backoff |

---
