import json
import os
import re
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from bot import main
from BotConfig import BotConfig

JST = timezone(timedelta(hours=+9), "JST")
load_dotenv()

team_name = os.environ["TEAM_NAME"]
team_id = os.environ["TEAM_ID"]


def load_bot_config_from_toml(toml_path: Path) -> BotConfig:
    with open(toml_path, "rb") as f:
        conf = tomllib.load(f)
    return BotConfig(
        bot_name=conf["bot_name"],
        reaction=conf["reaction"],
        user_id=conf["user_id"],
        token=os.environ[conf["token_env"]],
        system_message=Path(conf["system_message_file"]).read_text(),
        model=conf["model"],
        read_pin=conf["read_pin"],
        memory_channel_id=conf.get("memory_channel_id"),
        memory_channel_name=conf.get("memory_channel_name"),
    )


configs: dict[str, BotConfig] = {}
for config_file in Path("bots").glob("*.toml"):
    if not re.match(r"^[a-zA-Z0-9_]+\.toml$", config_file.name):
        continue
    config = load_bot_config_from_toml(config_file)
    configs[config.bot_name] = config

user_id_dict = {
    os.environ["USER_NAME"]: os.environ["USER_ID"],
}
for bot_name, bot_config in configs.items():
    user_id_dict[bot_config.bot_name] = bot_config.user_id


while True:
    begin_time = datetime.now(tz=JST)
    for bot_name, config in configs.items():
        print(
            json.dumps({"time": begin_time.isoformat(), "message": f"start {bot_name}"})
        )

        main(config, team_id, user_id_dict)
        end_time = datetime.now(tz=JST)
        elapsed_time = (end_time - begin_time).seconds

    # 5秒に1回実行
    if elapsed_time < 5:
        sleep_time = 5 - elapsed_time
        time.sleep(sleep_time)
