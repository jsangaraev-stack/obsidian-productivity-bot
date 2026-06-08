# Railway deploy

Этот набор файлов нужен, чтобы Railway смог собрать и запустить Telegram-бота.

## Что загрузить в GitHub

Загрузи в корень репозитория все файлы из этой папки:

- `bot.py`
- `habits.json`
- `strategy_reminders.json`
- `requirements.txt`
- `Procfile`
- `railway.json`
- `.env.railway.example`

## Что не загружать

Не загружай:

- `.env`
- `.github_token`
- `.github_curl_headers`
- `state.json`

## Переменные Railway

В Railway открой сервис -> `Variables` и добавь:

```text
TELEGRAM_BOT_TOKEN=токен Telegram-бота
ALLOWED_CHAT_ID=твой Telegram chat id
TIMEZONE=Europe/Moscow
MORNING_TIME=07:00
MIDDAY_TIME=14:00
EVENING_TIME=21:00
WEEKLY_REPORT_TIME=21:30
RANDOM_REMINDERS_START=09:00
RANDOM_REMINDERS_END=20:00
RANDOM_REMINDERS_COUNT=3
HABITS_FILE=habits.json
REMINDERS_FILE=strategy_reminders.json
STATE_FILE=state.json
OBSIDIAN_TRACKING_FILE=план-факт продуктивности.md
OBSIDIAN_REPORT_FILE=отчеты продуктивности.md
GITHUB_REPO=jsangaraev-stack/obsidian-productivity-bot
GITHUB_BRANCH=main
GITHUB_TRACKING_PATH=план-факт продуктивности.md
GITHUB_REPORT_PATH=отчеты продуктивности.md
GITHUB_STATE_PATH=state.json
GITHUB_TOKEN=твой GitHub token
```

После добавления переменных нажми `Redeploy`.
