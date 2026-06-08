#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import random
import ssl
import sys
import time
import urllib.parse
import urllib.request
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parent
BLOCKS = ("morning", "evening")
BLOCK_LABELS = {
    "morning": "утренний план",
    "evening": "вечерний факт",
    "quick": "быстрый план",
}
QUICK_QUESTIONS = [
    {"id": "quick_main_strike", "text": "Главный удар дня?", "type": "text"},
    {"id": "quick_tasks", "text": "Какие задачи минимум выполнить?", "type": "text"},
]
MINIMUM_TEXT = "минимальный день, главное не разорвать цепь"
MORNING_MISSED_TIME = "10:00"
EVENING_MISSED_TIME = "22:30"
REWARD_MIN_COMPLETE_DAYS = 5
STALE_RANDOM_REMINDER_GRACE_MINUTES = 10


def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


@dataclass
class Config:
    token: str
    allowed_chat_id: str
    morning_time: str
    midday_time: str
    evening_time: str
    weekly_report_time: str
    random_reminders_start: str
    random_reminders_end: str
    random_reminders_count: int
    timezone: str
    habits_file: Path
    reminders_file: Path
    state_file: Path
    obsidian_tracking_file: Path
    obsidian_report_file: Path
    github_token: str
    github_repo: str
    github_branch: str
    github_tracking_path: str
    github_report_path: str
    github_state_path: str


def env_path(value: str, fallback: str) -> Path:
    path = Path(value or fallback)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def load_config() -> Config:
    file_env = load_env(BASE_DIR / ".env")
    env = {**file_env, **os.environ}

    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Нет TELEGRAM_BOT_TOKEN. Скопируй .env.example в .env и вставь токен.", file=sys.stderr)
        sys.exit(1)

    return Config(
        token=token,
        allowed_chat_id=env.get("ALLOWED_CHAT_ID", "").strip(),
        morning_time=env.get("MORNING_TIME", env.get("DAILY_TIME", "07:00")).strip(),
        midday_time=env.get("MIDDAY_TIME", "14:00").strip(),
        evening_time=env.get("EVENING_TIME", "21:00").strip(),
        weekly_report_time=env.get("WEEKLY_REPORT_TIME", "21:30").strip(),
        random_reminders_start=env.get("RANDOM_REMINDERS_START", "09:00").strip(),
        random_reminders_end=env.get("RANDOM_REMINDERS_END", "20:00").strip(),
        random_reminders_count=int(env.get("RANDOM_REMINDERS_COUNT", "3").strip() or "3"),
        timezone=env.get("TIMEZONE", "Europe/Moscow").strip(),
        habits_file=env_path(env.get("HABITS_FILE", "habits.json"), "habits.json"),
        reminders_file=env_path(env.get("REMINDERS_FILE", "strategy_reminders.json"), "strategy_reminders.json"),
        state_file=env_path(env.get("STATE_FILE", "state.json"), "state.json"),
        obsidian_tracking_file=env_path(env.get("OBSIDIAN_TRACKING_FILE", "план-факт продуктивности.md"), "план-факт продуктивности.md"),
        obsidian_report_file=env_path(env.get("OBSIDIAN_REPORT_FILE", "отчеты продуктивности.md"), "отчеты продуктивности.md"),
        github_token=env.get("GITHUB_TOKEN", "").strip(),
        github_repo=env.get("GITHUB_REPO", "").strip(),
        github_branch=env.get("GITHUB_BRANCH", "main").strip(),
        github_tracking_path=env.get("GITHUB_TRACKING_PATH", "план-факт продуктивности.md").strip(),
        github_report_path=env.get("GITHUB_REPORT_PATH", "отчеты продуктивности.md").strip(),
        github_state_path=env.get("GITHUB_STATE_PATH", "state.json").strip(),
    )


def github_enabled(config: Config) -> bool:
    return bool(config.github_token and config.github_repo)


class GitHubStorage:
    def __init__(self, config: Config):
        self.token = config.github_token
        self.repo = config.github_repo
        self.branch = config.github_branch

    def api_url(self, path: str) -> str:
        quoted_path = urllib.parse.quote(path.strip("/"))
        return f"https://api.github.com/repos/{self.repo}/contents/{quoted_path}"

    def request(self, method: str, path: str, payload: dict | None = None) -> dict | None:
        url = self.api_url(path)
        if method == "GET":
            url += f"?ref={urllib.parse.quote(self.branch)}"
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code == 404:
                return None
            raise

    def fetch_text(self, path: str, default: str = "") -> str:
        payload = self.request("GET", path)
        if not payload or payload.get("type") != "file":
            return default
        content = payload.get("content", "")
        return base64.b64decode(content).decode("utf-8")

    def put_text(self, path: str, content: str, message: str) -> None:
        current = self.request("GET", path)
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": self.branch,
        }
        if current and current.get("sha"):
            payload["sha"] = current["sha"]
        self.request("PUT", path, payload)


def sync_file_to_github(config: Config, local_path: Path, github_path: str, message: str) -> None:
    if not github_enabled(config):
        return
    GitHubStorage(config).put_text(github_path, local_path.read_text(encoding="utf-8"), message)


def load_state(config: Config) -> dict:
    if github_enabled(config):
        remote_state = GitHubStorage(config).fetch_text(config.github_state_path, "")
        if remote_state.strip():
            return json.loads(remote_state)
    return read_json(config.state_file, initial_state())


def persist_state(config: Config, state: dict, remote: bool = False) -> None:
    write_json(config.state_file, state)
    if remote and github_enabled(config):
        GitHubStorage(config).put_text(
            config.github_state_path,
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            "Update bot state",
        )


class TelegramBot:
    def __init__(self, config: Config):
        self.api_base = f"https://api.telegram.org/bot{config.token}"
        self.ssl_context = ssl.create_default_context()
        self.insecure_ssl_context = ssl._create_unverified_context()

    def call(self, method: str, data: dict | None = None) -> dict:
        body = urllib.parse.urlencode(data or {}).encode("utf-8")
        request = urllib.request.Request(f"{self.api_base}/{method}", data=body)
        try:
            with urllib.request.urlopen(request, timeout=60, context=self.ssl_context) as response:
                payload = response.read().decode("utf-8")
        except URLError as error:
            if "CERTIFICATE_VERIFY_FAILED" not in str(error):
                raise
            # Локальный fallback для проблемы сертификатов в Python/macOS внутри Codex.
            with urllib.request.urlopen(request, timeout=60, context=self.insecure_ssl_context) as response:
                payload = response.read().decode("utf-8")

        result = json.loads(payload)
        if not result.get("ok"):
            raise RuntimeError(result)
        return result

    def get_updates(self, offset: int | None) -> list:
        data = {"timeout": 30}
        if offset is not None:
            data["offset"] = offset
        return self.call("getUpdates", data)["result"]

    def send_message(self, chat_id: str | int, text: str, keyboard: list[str] | None = None) -> None:
        data = {
            "chat_id": str(chat_id),
            "text": text,
            "parse_mode": "Markdown",
        }
        if keyboard:
            data["reply_markup"] = json.dumps(
                {
                    "keyboard": [[{"text": option}] for option in keyboard],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                },
                ensure_ascii=False,
            )
        else:
            data["reply_markup"] = json.dumps({"remove_keyboard": True})
        self.call("sendMessage", data)


def today_key(timezone: str) -> str:
    return datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d")


def now_hm(timezone: str) -> str:
    return datetime.now(ZoneInfo(timezone)).strftime("%H:%M")


def now_dt(timezone: str) -> datetime:
    return datetime.now(ZoneInfo(timezone))


def minutes_from_hm(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def hm_from_minutes(value: int) -> str:
    hour = value // 60
    minute = value % 60
    return f"{hour:02d}:{minute:02d}"


def load_habits(path: Path) -> dict:
    if not path.exists():
        example = BASE_DIR / "habits.example.json"
        if example.exists():
            path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            raise FileNotFoundError(f"Не найден файл привычек: {path}")

    data = read_json(path, {})
    if "questions" in data:
        data = {
            "morning": {
                "intro": "Утренний план.",
                "questions": [{"id": "plan", "text": "Каков мой план на день?", "type": "text"}],
            },
            "evening": {
                "intro": data.get("intro", "Вечерний факт."),
                "questions": data["questions"],
            },
        }

    for block in BLOCKS:
        if block not in data or not data[block].get("questions"):
            raise ValueError(f"В habits.json нет блока {block}.questions")
    return data


def load_reminders(path: Path) -> dict:
    if not path.exists():
        return {"assets": []}
    data = read_json(path, {"assets": []})
    if not isinstance(data.get("assets"), list):
        raise ValueError("В strategy_reminders.json поле assets должно быть списком")
    return data


def initial_state() -> dict:
    return {
        "offset": None,
        "processed_updates": [],
        "sessions": {},
        "entries": {},
        "last_sent": {},
        "random_reminders": {},
    }


def normalize_state(state: dict) -> dict:
    state.setdefault("offset", None)
    state.setdefault("processed_updates", [])
    state.setdefault("sessions", {})
    state.setdefault("entries", {})
    state.setdefault("last_sent", {})
    state.setdefault("random_reminders", {})
    if not isinstance(state["last_sent"], dict):
        state["last_sent"] = {}
    if not isinstance(state["random_reminders"], dict):
        state["random_reminders"] = {}
    if not isinstance(state["processed_updates"], list):
        state["processed_updates"] = []
    for entry in state["entries"].values():
        morning = entry.get("morning", {})
        old_sleep = morning.pop("wake_sleep_time", "")
        if old_sleep and not morning.get("sleep_time") and not morning.get("wake_time"):
            sleep_time, wake_time = split_sleep_answer(old_sleep)
            morning["sleep_time"] = sleep_time
            morning["wake_time"] = wake_time
    return state


def mark_update_processed(state: dict, update_id: int) -> None:
    processed = state.setdefault("processed_updates", [])
    processed.append(update_id)
    state["processed_updates"] = processed[-200:]


def update_already_processed(state: dict, update_id: int) -> bool:
    return update_id in set(state.get("processed_updates", []))


def refresh_state_from_github(config: Config, state: dict) -> dict:
    if not github_enabled(config):
        return state
    try:
        remote_state = normalize_state(load_state(config))
    except Exception as error:
        print(f"Не удалось подтянуть state из GitHub: {error}", file=sys.stderr)
        return state
    if int(remote_state.get("offset") or 0) > int(state.get("offset") or 0):
        return remote_state
    remote_processed = set(remote_state.get("processed_updates", []))
    local_processed = set(state.get("processed_updates", []))
    state["processed_updates"] = list((remote_processed | local_processed))[-200:]
    return state


def is_allowed(config: Config, chat_id: str | int) -> bool:
    return not config.allowed_chat_id or str(chat_id) == config.allowed_chat_id


def get_session(state: dict, chat_id: str | int) -> dict | None:
    return state.get("sessions", {}).get(str(chat_id))


def set_session(state: dict, chat_id: str | int, session: dict | None) -> None:
    state.setdefault("sessions", {})
    if session is None:
        state["sessions"].pop(str(chat_id), None)
    else:
        state["sessions"][str(chat_id)] = session


def block_questions(habits: dict, block: str) -> list[dict]:
    if block == "quick":
        return QUICK_QUESTIONS
    return habits[block]["questions"]


def current_question(habits: dict, session: dict) -> dict:
    return block_questions(habits, session["block"])[session["index"]]


def ask_question(bot: TelegramBot, chat_id: str | int, habits: dict, session: dict) -> None:
    question = current_question(habits, session)
    keyboard = list(question.get("options", [])) if question.get("type") == "choice" else []
    if question.get("type") != "choice":
        keyboard.append("минимум")
    bot.send_message(chat_id, question["text"], keyboard=keyboard)


def start_session(bot: TelegramBot, config: Config, state: dict, habits: dict, chat_id: str | int, block: str) -> None:
    if block not in (*BLOCKS, "quick"):
        raise ValueError(f"Неизвестный блок: {block}")

    session = {
        "date": today_key(config.timezone),
        "block": block,
        "index": 0,
        "answers": {},
    }
    set_session(state, chat_id, session)
    intro = habits.get(block, {}).get("intro", f"Начинаем {BLOCK_LABELS[block]}.")
    bot.send_message(chat_id, intro)
    ask_question(bot, chat_id, habits, session)


def markdown_cell(value: str) -> str:
    value = str(value or "").replace("\n", "<br>").replace("\r", " ")
    return value.replace("|", "\\|").strip()


def table_columns(habits: dict) -> list[tuple[str, str, str]]:
    columns = [("date", "date", "Дата")]
    for block in BLOCKS:
        for question in block_questions(habits, block):
            columns.append((block, question["id"], question["text"]))
    return columns


def split_sleep_answer(value: str) -> tuple[str, str]:
    lowered = str(value or "").lower()
    parts = []
    for token in lowered.replace(",", " ").replace(";", " ").split():
        cleaned = token.strip(". ")
        if cleaned.replace(":", "").isdigit() and any(char.isdigit() for char in cleaned):
            parts.append(cleaned)
    if len(parts) >= 2:
        return normalize_time(parts[0]), normalize_time(parts[1])
    return str(value or ""), ""


def normalize_time(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = raw.replace(".", ":")
    digits = "".join(char for char in raw if char.isdigit() or char == ":")
    if ":" in digits:
        hour, minute = digits.split(":", 1)
        if hour.isdigit() and minute[:2].isdigit():
            return f"{int(hour):02d}:{int(minute[:2]):02d}"
    if digits.isdigit():
        hour = int(digits)
        if 0 <= hour <= 23:
            return f"{hour:02d}:00"
    return raw


def normalize_choice_text(value: str, options: list[str]) -> str:
    raw = str(value or "").strip().lower()
    yes_values = {"да", "ага", "угу", "сделал", "сделана", "сделано", "есть", "выполнено", "прочитал", "было", "+"}
    no_values = {"нет", "не", "неа", "не сделал", "не было", "не прочитал", "пропустил", "сорвался", "-"}
    partial_values = {"частично", "часть", "наполовину", "немного", "чуть-чуть", "почти"}

    if "частично" in options and raw in partial_values:
        return "частично"
    if "да" in options and raw in yes_values:
        return "да"
    if "нет" in options and raw in no_values:
        return "нет"
    return raw


def normalize_work_hours(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"1", "2", "1-2", "1 2", "до 2", "пару"}:
        return "1-2"
    if raw in {"3", "4", "3-4", "3 4"}:
        return "3-4"
    if raw in {"5", "6", "5-6", "5 6"}:
        return "5-6"
    if raw in {"7", "8", "9", "10", "7+", "много"}:
        return "7+"
    return raw


def normalize_answer(question: dict, text: str) -> str:
    question_id = question.get("id")
    raw = str(text or "").strip()
    if raw.lower() == "минимум":
        return minimum_answer(question)
    if question_id in {"sleep_time", "wake_time"}:
        return normalize_time(raw)
    if question_id == "planned_work_hours":
        return normalize_work_hours(raw)
    if question.get("type") == "choice":
        return normalize_choice_text(raw, question.get("options", []))
    return raw


def write_tracking_table(config: Config, habits: dict, state: dict) -> None:
    config.obsidian_tracking_file.parent.mkdir(parents=True, exist_ok=True)
    columns = table_columns(habits)
    entries = state.setdefault("entries", {})

    header = "| " + " | ".join(markdown_cell(column[2]) for column in columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    rows = []

    for date in sorted(entries):
        entry = entries[date]
        cells = []
        for block, question_id, _title in columns:
            if block == "date":
                cells.append(date)
            else:
                cells.append(entry.get(block, {}).get(question_id, ""))
        rows.append("| " + " | ".join(markdown_cell(cell) for cell in cells) + " |")

    text = "\n".join(
        [
            "# План-факт продуктивности",
            "",
            "Таблица генерируется Telegram-ботом. Один день = одна строка.",
            "",
            header,
            separator,
            *rows,
            "",
        ]
    )
    config.obsidian_tracking_file.write_text(text, encoding="utf-8")
    sync_file_to_github(
        config,
        config.obsidian_tracking_file,
        config.github_tracking_path,
        "Update productivity tracking table",
    )


def save_session_to_state(state: dict, session: dict) -> None:
    date = session["date"]
    block = session["block"]
    state.setdefault("entries", {})
    state["entries"].setdefault(date, {"morning": {}, "evening": {}})
    if block == "quick":
        answers = session.get("answers", {})
        state["entries"][date].setdefault("morning", {})
        state["entries"][date]["morning"]["main_strike"] = answers.get("quick_main_strike", "")
        state["entries"][date]["morning"]["plan"] = answers.get("quick_tasks", "")
        return

    state["entries"][date].setdefault(block, {})
    state["entries"][date][block].update(session.get("answers", {}))


def completed_day(entry: dict) -> bool:
    morning = entry.get("morning", {})
    evening = entry.get("evening", {})
    has_plan = bool(morning.get("main_strike") or morning.get("plan"))
    has_fact = bool(evening.get("fact"))
    return has_plan and has_fact


def streak_days(state: dict, timezone: str) -> int:
    entries = state.get("entries", {})
    current = now_dt(timezone).date()
    streak = 0
    while True:
        key = current.strftime("%Y-%m-%d")
        if not completed_day(entries.get(key, {})):
            return streak
        streak += 1
        current -= timedelta(days=1)


def minimum_answer(question: dict) -> str:
    question_id = question.get("id")
    if question_id in {"main_strike", "plan", "fact", "tomorrow_first_step"}:
        return MINIMUM_TEXT
    if question_id == "planned_work_hours":
        return "1-2"
    if question_id == "main_strike_done":
        return "частично"
    if question_id == "goal_step":
        return "частично"
    if question.get("type") == "choice":
        options = question.get("options", [])
        if "частично" in options:
            return "частично"
        if "нет" in options:
            return "нет"
        return options[0] if options else MINIMUM_TEXT
    return MINIMUM_TEXT


def finish_with_minimum(bot: TelegramBot, config: Config, state: dict, habits: dict, chat_id: str | int, session: dict) -> None:
    questions = block_questions(habits, session["block"])
    for index in range(session["index"], len(questions)):
        question = questions[index]
        session["answers"].setdefault(question["id"], minimum_answer(question))
    finish_session(bot, config, state, habits, chat_id, session)


def record_minimum_day(bot: TelegramBot, config: Config, state: dict, habits: dict, chat_id: str | int) -> None:
    date = today_key(config.timezone)
    state.setdefault("entries", {})
    state["entries"].setdefault(date, {"morning": {}, "evening": {}})

    for question in block_questions(habits, "morning"):
        state["entries"][date]["morning"].setdefault(question["id"], minimum_answer(question))
    for question in block_questions(habits, "evening"):
        state["entries"][date]["evening"].setdefault(question["id"], minimum_answer(question))

    write_tracking_table(config, habits, state)
    set_session(state, chat_id, None)
    streak = streak_days(state, config.timezone)
    bot.send_message(
        chat_id,
        f"Минимальный день записан. Цепь не разорвана.\n\nТекущая серия план-факт: {streak} дн.",
    )


def finish_session(bot: TelegramBot, config: Config, state: dict, habits: dict, chat_id: str | int, session: dict) -> None:
    save_session_to_state(state, session)
    write_tracking_table(config, habits, state)
    set_session(state, chat_id, None)
    streak = streak_days(state, config.timezone)
    bot.send_message(
        chat_id,
        f"Готово. {BLOCK_LABELS[session['block']].capitalize()} записан в таблицу.\n\nТекущая серия план-факт: {streak} дн.\n{config.obsidian_tracking_file}",
    )


def handle_answer(bot: TelegramBot, config: Config, state: dict, habits: dict, chat_id: str | int, text: str) -> None:
    session = get_session(state, chat_id)
    if not session or "block" not in session:
        set_session(state, chat_id, None)
        bot.send_message(chat_id, "Сейчас нет активного опроса. Напиши /plan утром или /fact вечером.")
        return

    lowered = text.lower().strip()
    if lowered == "минимум":
        finish_with_minimum(bot, config, state, habits, chat_id, session)
        return

    question = current_question(habits, session)
    normalized_text = normalize_answer(question, text)
    if question.get("type") == "choice" and normalized_text not in question.get("options", []):
        options = list(question.get("options", []))
        bot.send_message(chat_id, "Выбери один из вариантов ниже.", keyboard=options)
        return

    session["answers"][question["id"]] = normalized_text
    session["index"] += 1

    if session["index"] >= len(block_questions(habits, session["block"])):
        finish_session(bot, config, state, habits, chat_id, session)
        return

    set_session(state, chat_id, session)
    ask_question(bot, chat_id, habits, session)


def answer(entry: dict, block: str, key: str) -> str:
    return entry.get(block, {}).get(key, "")


def yesish(value: str) -> bool:
    return str(value).strip().lower() in {"да", "частично"}


def average(values: list[int]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def build_report(config: Config, state: dict) -> str:
    entries = state.get("entries", {})
    report_date = today_key(config.timezone)
    dates = sorted(entries)[-7:]

    if not dates:
        return f"## Отчет за {report_date}\n\nПока нет записей в таблице план-факт.\n"

    lines = [
        f"## Отчет за {report_date}",
        "",
        f"Период: последние {len(dates)} дней.",
        "",
    ]

    complete_days = 0
    main_strike_done = 0
    goal_steps = 0
    missing_facts = []
    tomorrow_steps = []

    for date in dates:
        entry = entries[date]
        plan = answer(entry, "morning", "plan")
        main_strike = answer(entry, "morning", "main_strike")
        planned_hours = answer(entry, "morning", "planned_work_hours")
        fact = answer(entry, "evening", "fact")
        strike_done = answer(entry, "evening", "main_strike_done")
        goal_step = answer(entry, "evening", "goal_step")
        tomorrow_step = answer(entry, "evening", "tomorrow_first_step")

        if plan and fact:
            complete_days += 1
        if yesish(strike_done):
            main_strike_done += 1
        if yesish(goal_step):
            goal_steps += 1
        if plan and not fact:
            missing_facts.append(date)
        if tomorrow_step:
            tomorrow_steps.append(tomorrow_step)

        lines.extend(
            [
                f"### {date}",
                f"- План: {plan or 'не заполнено'}",
                f"- Главный удар: {main_strike or 'не заполнено'}",
                f"- План рабочих часов: {planned_hours or 'не заполнено'}",
                f"- Факт: {fact or 'не заполнено'}",
                f"- Главный удар выполнен: {strike_done or 'не заполнено'}",
                f"- Шаг к Цели: {goal_step or 'не заполнено'}",
                "",
            ]
        )

    weakest_point = "пока нет данных"
    if missing_facts:
        weakest_point = "есть утренний план, но нет вечернего факта"
    elif main_strike_done < len(dates):
        weakest_point = "главный удар не всегда доводится до конца"
    elif goal_steps < len(dates):
        weakest_point = "шаг к Цели делается не каждый день"

    lines.extend(
        [
            "### Сводка",
            f"- Главное число недели: {complete_days}/{len(dates)} дней с планом-фактом",
            f"- Главный удар выполнен или частично выполнен: {main_strike_done}/{len(dates)}",
            f"- Шаг к Цели был или частично был: {goal_steps}/{len(dates)}",
            f"- Главная поломка недели: {weakest_point}",
            "- Смысл отчета: увидеть разницу между намерением утром и реальностью вечером.",
            "",
            "### Первый шаг на завтра",
            f"- {tomorrow_steps[-1] if tomorrow_steps else 'не заполнено'}",
            "",
            "### Награда",
            f"- {'Можно выбрать небольшую награду: 5+ дней система держалась.' if complete_days >= REWARD_MIN_COMPLETE_DAYS else 'Пока главная награда - не бросить систему. Цель: 5 дней план-факт за неделю.'}",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(config: Config, state: dict) -> str:
    config.obsidian_report_file.parent.mkdir(parents=True, exist_ok=True)
    if not config.obsidian_report_file.exists():
        config.obsidian_report_file.write_text("# Отчеты продуктивности\n\n", encoding="utf-8")

    report = build_report(config, state)
    with config.obsidian_report_file.open("a", encoding="utf-8") as file:
        file.write(report)
        file.write("\n---\n\n")
    sync_file_to_github(
        config,
        config.obsidian_report_file,
        config.github_report_path,
        "Update productivity report",
    )
    return report


def handle_command(bot: TelegramBot, config: Config, state: dict, habits: dict, chat_id: str | int, text: str) -> None:
    command = text.split()[0].lower()

    if command == "/start":
        message = (
            "Я бот продуктивности по схеме план-факт.\n\n"
            "Автоопросы:\n"
            f"07:00 - план дня\n"
            f"14:00 - проверка главного удара\n"
            f"21:00 - факт дня\n\n"
            "Команды:\n"
            "/plan - заполнить утренний план сейчас\n"
            "/fact - заполнить вечерний факт сейчас\n"
            "/quick - быстрый план: главный удар + задачи\n"
            "/minimum - записать минимальный день, чтобы не разорвать цепь\n"
            "/report - записать отчет по последним дням\n"
            "/sos - быстрый протокол возвращения к делу\n"
            "/status - показать статус\n"
            "/cancel - отменить текущий опрос\n"
            "/chatid - показать ID этого чата"
        )
        bot.send_message(chat_id, message)
        return

    if command == "/chatid":
        bot.send_message(chat_id, f"ID этого чата: `{chat_id}`")
        return

    if command in ("/plan", "/today"):
        start_session(bot, config, state, habits, chat_id, "morning")
        return

    if command == "/quick":
        start_session(bot, config, state, habits, chat_id, "quick")
        return

    if command == "/minimum":
        record_minimum_day(bot, config, state, habits, chat_id)
        return

    if command in ("/fact", "/evening"):
        start_session(bot, config, state, habits, chat_id, "evening")
        return

    if command in ("/report", "/week"):
        report = write_report(config, state)
        bot.send_message(
            chat_id,
            f"Отчет записан в Obsidian:\n{config.obsidian_report_file}\n\n{report[:2500]}",
        )
        return

    if command == "/sos":
        bot.send_message(
            chat_id,
            "\n".join(
                [
                    "Протокол `/sos` на 5 минут:",
                    "",
                    "1. Закрой все лишнее.",
                    "2. Встань и выпей воды.",
                    "3. Сделай 10 медленных вдохов.",
                    "4. Запиши одну микрозадачу.",
                    "5. Поставь таймер на 5 минут.",
                    "6. Делай только эту микрозадачу.",
                    "",
                    "Вопрос: какой один следующий шаг?",
                ]
            ),
        )
        return

    if command == "/status":
        session = get_session(state, chat_id)
        if session:
            questions_count = len(block_questions(habits, session["block"]))
            bot.send_message(
                chat_id,
                f"Активный опрос: {BLOCK_LABELS[session['block']]}, вопрос {session['index'] + 1}/{questions_count}.",
            )
        else:
            streak = streak_days(state, config.timezone)
            bot.send_message(chat_id, f"Активного опроса нет. Серия план-факт: {streak} дн. Команды: /plan, /quick, /fact, /minimum, /report.")
        return

    if command == "/cancel":
        set_session(state, chat_id, None)
        bot.send_message(chat_id, "Текущий опрос отменен.")
        return

    bot.send_message(chat_id, "Не знаю такую команду. Напиши /start.")


def handle_update(bot: TelegramBot, config: Config, state: dict, habits: dict, update: dict) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        return

    if not is_allowed(config, chat_id):
        bot.send_message(chat_id, "Этот бот личный. Доступ закрыт.")
        return

    if text.startswith("/"):
        handle_command(bot, config, state, habits, chat_id, text)
    else:
        handle_answer(bot, config, state, habits, chat_id, text)


def last_sent_for(state: dict, chat_id: str | int, block: str) -> str | None:
    sent = state.setdefault("last_sent", {})
    chat_sent = sent.get(str(chat_id))
    if not isinstance(chat_sent, dict):
        chat_sent = {}
        sent[str(chat_id)] = chat_sent
    return chat_sent.get(block)


def mark_sent(state: dict, chat_id: str | int, block: str, date: str) -> None:
    state.setdefault("last_sent", {}).setdefault(str(chat_id), {})[block] = date


def reminder_pool(reminders: dict) -> list[dict]:
    pool = []
    for asset in reminders.get("assets", []):
        name = asset.get("name", "Актив")
        for message in asset.get("messages", []):
            if message:
                pool.append({"asset": name, "message": message})
    return pool


def ensure_random_reminders_for_day(config: Config, state: dict, reminders: dict, date: str) -> list[dict]:
    random_state = state.setdefault("random_reminders", {})
    planned = random_state.get(date)
    if isinstance(planned, list) and planned:
        return planned

    pool = reminder_pool(reminders)
    if not pool or config.random_reminders_count <= 0:
        random_state[date] = []
        return []

    start = minutes_from_hm(config.random_reminders_start)
    end = minutes_from_hm(config.random_reminders_end)
    if end <= start:
        end = start + 60
    if date == today_key(config.timezone):
        current_minute = minutes_from_hm(now_hm(config.timezone))
        start = max(start, current_minute + 5)
        if start > end:
            random_state[date] = []
            return []

    count = min(config.random_reminders_count, len(pool), end - start + 1)
    chosen_messages = random.sample(pool, count)
    chosen_times = sorted(random.sample(range(start, end + 1), count))

    planned = []
    for planned_time, item in zip(chosen_times, chosen_messages):
        planned.append(
            {
                "time": hm_from_minutes(planned_time),
                "asset": item["asset"],
                "message": item["message"],
                "sent": False,
            }
        )
    random_state[date] = planned
    return planned


def maybe_send_random_reminder(bot: TelegramBot, config: Config, state: dict, reminders: dict) -> bool:
    chat_id = config.allowed_chat_id
    if not chat_id or get_session(state, chat_id):
        return False

    current_dt = now_dt(config.timezone)
    current_time = current_dt.strftime("%H:%M")
    current_minute = minutes_from_hm(current_time)
    date = current_dt.strftime("%Y-%m-%d")
    planned = ensure_random_reminders_for_day(config, state, reminders, date)

    for item in planned:
        if not item.get("sent") and current_time >= item["time"]:
            planned_minute = minutes_from_hm(item["time"])
            if current_minute - planned_minute > STALE_RANDOM_REMINDER_GRACE_MINUTES:
                item["sent"] = True
                continue
            bot.send_message(
                chat_id,
                f"Напоминание: {item['asset']}\n\n{item['message']}\n\nСверка: это помогает сегодняшнему главному удару?",
            )
            item["sent"] = True
            return True
    return False


def maybe_send_scheduled(bot: TelegramBot, config: Config, state: dict, habits: dict, reminders: dict) -> None:
    chat_id = config.allowed_chat_id
    if not chat_id or get_session(state, chat_id):
        return

    current_dt = now_dt(config.timezone)
    current_time = current_dt.strftime("%H:%M")
    date = current_dt.strftime("%Y-%m-%d")
    schedule = {
        "morning": config.morning_time,
        "evening": config.evening_time,
    }

    today_entry = state.get("entries", {}).get(date, {})

    if current_time == MORNING_MISSED_TIME and last_sent_for(state, chat_id, "morning_missed") != date:
        if not (answer(today_entry, "morning", "main_strike") or answer(today_entry, "morning", "plan")):
            bot.send_message(
                chat_id,
                "План еще можно зафиксировать. Система продолжается.\n\nВыбери быстрый вариант: /quick или /minimum.",
                keyboard=["/quick", "/plan", "/minimum"],
            )
            mark_sent(state, chat_id, "morning_missed", date)
            return

    if current_time == EVENING_MISSED_TIME and last_sent_for(state, chat_id, "evening_missed") != date:
        if not answer(today_entry, "evening", "fact"):
            bot.send_message(
                chat_id,
                "Можно закрыть день коротко. Пропуск - не провал, просто ставим точку.\n\nВыбери: /fact или /minimum.",
                keyboard=["/fact", "/minimum"],
            )
            mark_sent(state, chat_id, "evening_missed", date)
            return

    if current_time == config.midday_time and last_sent_for(state, chat_id, "midday") != date:
        main_strike = answer(today_entry, "morning", "main_strike")
        message = "14:00. Ты сейчас делаешь главный удар дня или ушел в суету?"
        if main_strike:
            message += f"\n\nГлавный удар на сегодня: {main_strike}"
        message += "\n\nЕсли унесло, напиши `/sos`."
        bot.send_message(chat_id, message)
        mark_sent(state, chat_id, "midday", date)
        return

    if current_dt.weekday() == 6 and current_time == config.weekly_report_time and last_sent_for(state, chat_id, "weekly_report") != date:
        report = write_report(config, state)
        bot.send_message(
            chat_id,
            f"Воскресный отчет записан в Obsidian:\n{config.obsidian_report_file}\n\n{report[:2500]}",
        )
        mark_sent(state, chat_id, "weekly_report", date)
        return

    for block, planned_time in schedule.items():
        if current_time == planned_time and last_sent_for(state, chat_id, block) != date:
            start_session(bot, config, state, habits, chat_id, block)
            mark_sent(state, chat_id, block, date)
            return

    maybe_send_random_reminder(bot, config, state, reminders)


def main() -> None:
    config = load_config()
    habits = load_habits(config.habits_file)
    reminders = load_reminders(config.reminders_file)
    state = normalize_state(load_state(config))
    bot = TelegramBot(config)

    print("Бот запущен. Остановить: Ctrl+C")
    print(f"Утренний опрос: {config.morning_time} ({config.timezone})")
    print(f"Срединное напоминание: {config.midday_time} ({config.timezone})")
    print(f"Вечерний опрос: {config.evening_time} ({config.timezone})")
    print(f"Воскресный отчет: {config.weekly_report_time} ({config.timezone})")
    print(f"Случайные напоминания: {config.random_reminders_count} раз(а) с {config.random_reminders_start} до {config.random_reminders_end}")
    print(f"Таблица Obsidian: {config.obsidian_tracking_file}")
    print(f"Файл отчетов: {config.obsidian_report_file}")
    if github_enabled(config):
        print(f"GitHub-синхронизация: {config.github_repo}")

    while True:
        try:
            state = refresh_state_from_github(config, state)
            before_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
            maybe_send_scheduled(bot, config, state, habits, reminders)
            updates = bot.get_updates(state.get("offset"))
            for update in updates:
                update_id = update["update_id"]
                if update_already_processed(state, update_id):
                    state["offset"] = max(int(state.get("offset") or 0), update_id + 1)
                    continue
                state["offset"] = update_id + 1
                handle_update(bot, config, state, habits, update)
                mark_update_processed(state, update_id)
            after_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
            persist_state(config, state, remote=after_state != before_state)
            time.sleep(1)
        except KeyboardInterrupt:
            persist_state(config, state, remote=True)
            print("\nБот остановлен.")
            return
        except Exception as error:
            print(f"Ошибка: {error}", file=sys.stderr)
            persist_state(config, state)
            time.sleep(5)


if __name__ == "__main__":
    main()
