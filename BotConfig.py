from pydantic import BaseModel


class BotConfig(BaseModel):
    bot_name: str
    reaction: str
    user_id: str
    token: str
    read_pin: bool
    memory_channel_id: str | None
    memory_channel_name: str | None
    system_message: str
    model: str
