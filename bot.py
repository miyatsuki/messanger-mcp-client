import json
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from openai import OpenAI

from BotConfig import BotConfig

JST = timezone(timedelta(hours=+9), "JST")


def sort_posts(posts):
    return sorted(posts, key=lambda x: x["create_at"])


def draw_tarots(N: int = 1) -> list[str]:
    tarots = [
        "愚者",
        "魔術師",
        "女教皇",
        "女帝",
        "皇帝",
        "教皇",
        "恋人",
        "戦車",
        "力",
        "隠者",
        "運命の輪",
        "正義",
        "吊るされた男",
        "死神",
        "節制",
        "悪魔",
        "塔",
        "星",
        "月",
        "太陽",
        "審判",
        "世界",
    ]

    # N枚を重複しないようにランダムに選ぶ
    tarots = random.sample(tarots, N)

    # N枚のカードについて、正位置か逆位置かをランダムに決定
    positions = [random.choice(["正位置", "逆位置"]) for _ in range(N)]

    return [f"{tarot}({position})" for tarot, position in zip(tarots, positions)]


# Mattermostユーザー情報取得
def get_mattermost_users(headers: dict[str, str]) -> dict[str, Any]:
    response = requests.get(
        "http://localhost:8065/api/v4/users",
        headers=headers,
    )
    print(response.status_code)
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return response.json()


def get_channels_by_user(
    headers: dict[str, str], user_id: str, team_id: str
) -> list[str]:
    # see: https://developers.mattermost.com/api-documentation/#/operations/GetChannelsForTeamForUser
    response = requests.get(
        f"http://localhost:8065/api/v4/users/{user_id}/teams/{team_id}/channels/members",
        headers=headers,
    )
    return [channel["channel_id"] for channel in response.json()]


# チャンネルの投稿取得
def get_channel_posts(
    headers: dict[str, str], channel_id: str, since: int
) -> dict[str, Any]:
    response = requests.get(
        f"http://localhost:8065/api/v4/channels/{channel_id}/posts",
        params={"since": since},
        headers=headers,
    )
    return response.json()


def get_channel_pinned_posts(
    headers: dict[str, str], channel_id: str
) -> list[dict[str, Any]]:
    response = requests.get(
        f"http://localhost:8065/api/v4/channels/{channel_id}/pinned",
        headers=headers,
    )
    return response.json()["posts"].values()


def check_post(config: BotConfig, post: dict[str, Any]):
    message = post["message"]
    reactions = post["metadata"].get("reactions", [])

    # 特定の絵文字がついていたら、返信済みとみなす
    is_starred = any(
        reaction["emoji_name"] == config.reaction for reaction in reactions
    )
    need_reaction = f"@{config.bot_name}" in message and not is_starred
    return need_reaction


def save_memory(
    client: OpenAI,
    config: BotConfig,
    headers: dict[str, str],
    reply_id: str,
    team_id: str,
    messages: list[dict[str, str]],
    answer: str,
):
    memory_messages = [
        *messages,
        {"role": "assistant", "content": answer},
        {
            "role": "user",
            "content": "このスレッドで話した内容をまとめてください。誰がいつどんなことを話したかに気をつけてメモをしてください。ピン留めの内容や過去の記憶の内容は省いてください。",
        },
    ]
    memory_messages[0] = {
        "role": "system",
        "content": f"""
あなたは {config.bot_name} です。
""",
    }

    memory_str = get_llm_response(client, config.model, memory_messages)
    memory_str = reply_id + "\n" + memory_str

    response = requests.post(
        f"http://localhost:8065/api/v4/teams/{team_id}/posts/search",
        headers=headers,
        json={"terms": f"{reply_id} in: {config.memory_channel_name}"},
    )

    posts = sort_posts(response.json()["posts"].values())

    if len(posts) == 0 or not posts[-1]["message"].startswith(reply_id):
        response = requests.post(
            f"http://localhost:8065/api/v4/posts",
            headers=headers,
            json={"channel_id": config.memory_channel_id, "message": memory_str},
        )
        response.raise_for_status()
    else:
        recent_post = sort_posts(response.json()["posts"].values())[-1]
        response = requests.put(
            f"http://localhost:8065/api/v4/posts/{recent_post['id']}/patch",
            headers=headers,
            json={"message": memory_str},
        )


def read_memory(headers: dict[str, str], memory_channel_id: str, reply_id: str) -> str:
    memories = []

    since = 1
    while True:
        response = requests.get(
            f"http://localhost:8065/api/v4/channels/{memory_channel_id}/posts",
            headers=headers,
            params={"since": since},
        )
        posts = sort_posts(response.json()["posts"].values())
        if len(posts) == 0:
            break

        for post in posts:
            if not post["message"].startswith(reply_id):
                post_time = datetime.fromtimestamp(
                    post["create_at"] / 1000, JST
                ).isoformat()
                memories.append(
                    f"{post_time}ごろの会話の記憶\n"
                    + "\n".join(post["message"].split("\n")[1:])
                )
        since = sort_posts(response.json()["posts"].values())[-1]["update_at"] + 1

    return "\n".join(memories)


# 投稿を処理
def process_post(
    config: BotConfig,
    post: dict[str, Any],
    pinned_posts: list[dict[str, Any]],
    headers: dict[str, str],
    team_id: str,
    channel_id: str,
    user_id_dict: dict[str, str],
    client: OpenAI,
):
    bot_user_id = user_id_dict[config.bot_name]

    reply_id: str = post["root_id"] if post["root_id"] != "" else post["id"]
    add_reaction(headers, bot_user_id, post["id"], "eyes")
    llm_messages = generate_bot_response(
        config, post["id"], pinned_posts, headers, user_id_dict
    )
    answer = get_llm_response(client, config.model, llm_messages)

    send_reply(headers, channel_id, reply_id, answer)
    add_reaction(headers, bot_user_id, post["id"], config.reaction)
    remove_reaction(headers, bot_user_id, post["id"], "eyes")

    if config.memory_channel_id:
        save_memory(client, config, headers, reply_id, team_id, llm_messages, answer)


# OpenAIを使ったレスポンス生成
def generate_bot_response(
    config: BotConfig,
    post_id: str,
    pinned_posts: list[dict[str, Any]],
    headers: dict[str, str],
    user_id_dict: dict[str, str],
) -> list[dict[str, str]]:
    id_mention_dict = {v: k for k, v in user_id_dict.items()}

    formatted_system_message = config.system_message.format(
        current_time=datetime.now(JST).isoformat(),
        tarots=", ".join(draw_tarots(1)),
    )
    llm_messages = [
        {
            "role": "system",
            "content": formatted_system_message
            + "\n以下のチャットログに続いて回答をしてください",
        }
    ]

    # スレッドのメッセージ全体を取得
    response = requests.get(
        f"http://localhost:8065/api/v4/posts/{post_id}/thread",
        headers=headers,
    )
    response_threads = response.json()

    chat_logs = []
    if config.read_pin:
        pinned_messages = []
        # ピン留めされていたポストがあったら、それをシステムメッセージに追加
        for pinned_post in pinned_posts:
            pinned_post_user_id = pinned_post["user_id"]
            pinned_post_user_name = id_mention_dict.get(pinned_post_user_id, "不明")
            pinned_messages.append(
                f"発話者: {pinned_post_user_name}\n{pinned_post['message']}"
            )
        if pinned_messages:
            chat_logs.append("ピン留めされたメッセージ:\n" + "\n".join(pinned_messages))

    if config.memory_channel_id:
        memory = read_memory(headers, config.memory_channel_id, post_id)
        if memory:
            chat_logs.append("過去の会話の記憶:\n" + memory)

    for thread_post in sort_posts(response_threads["posts"].values()):
        thread_user_id = thread_post["user_id"]
        thread_message = thread_post["message"]
        post_time = datetime.fromtimestamp(
            thread_post["create_at"] / 1000, JST
        ).isoformat()

        # thread_messageの最初の行が @xxx だけだったら、それを削除
        if thread_message.split("\n")[0].startswith("@"):
            thread_message = "\n".join(thread_message.split("\n")[1:])

        # mattermostのリンクだったら、そのリンク先のメッセージを取得
        thread_embeds = thread_post.get("metadata", {}).get("embeds")
        thread_mattermost_post_urls = []
        for embed in thread_embeds or []:
            if embed["type"] == "link" and embed.get("url", "").startswith(
                "http://localhost:8065/"
            ):
                thread_mattermost_post_urls.append(embed["url"])

        for thread_mattermost_post_url in thread_mattermost_post_urls:
            thread_embed_post_id = thread_mattermost_post_url.split("/")[-1]
            response = requests.get(
                f"http://localhost:8065/api/v4/posts/{thread_embed_post_id}",
                headers=headers,
            )
            if response.status_code == 200:
                thread_embed_post = response.json()
                thread_embed_user_id = thread_embed_post["user_id"]
                thread_embed_user_name = id_mention_dict.get(
                    thread_embed_user_id, thread_embed_user_id
                )
                thread_message += f"\n#### {thread_mattermost_post_url}のメッセージ:\n発話者: {thread_embed_user_name}\n{thread_embed_post['message']}"

        thread_message = f"発話時刻: {post_time}\n発話者: {id_mention_dict.get(thread_user_id, thread_user_id)}\n{thread_message}"
        chat_logs.append(thread_message)

    llm_messages.append({"role": "user", "content": "\n".join(chat_logs)})
    return llm_messages


# LLMからのレスポンス取得
def get_llm_response(client: OpenAI, model: str, messages: list[dict[str, str]]) -> str:
    response = client.chat.completions.create(
        model=model, messages=messages, temperature=1.0
    )
    answer = response.choices[0].message.content
    print(json.dumps({"time": datetime.now(JST).isoformat(), "answer": answer}))
    assert answer is not None
    return answer


# Mattermostへの返信
def send_reply(
    headers: dict[str, str], channel_id: str, reply_id: str, message: str
) -> None:
    response = requests.post(
        "http://localhost:8065/api/v4/posts",
        headers=headers,
        json={
            "channel_id": channel_id,
            "message": message,
            "root_id": reply_id,
        },
    )
    response.raise_for_status()


# リアクションの追加
def add_reaction(
    headers: dict[str, str], user_id: str, post_id: str, emoji: str
) -> None:
    response = requests.post(
        f"http://localhost:8065/api/v4/reactions",
        headers=headers,
        json={"user_id": user_id, "post_id": post_id, "emoji_name": emoji},
    )
    response.raise_for_status()


# リアクションの削除
def remove_reaction(
    headers: dict[str, str], user_id: str, post_id: str, emoji: str
) -> None:
    response = requests.delete(
        f"http://localhost:8065/api/v4/users/{user_id}/posts/{post_id}/reactions/{emoji}",
        headers=headers,
    )
    response.raise_for_status()


def main(config: BotConfig, team_id: str, user_id_dict: dict[str, str]):
    headers = {"Authorization": f"Bearer {config.token}"}

    # ユーザー情報取得
    # get_mattermost_users(headers)

    # チーム情報取得
    # response = requests.get(
    #     f"http://localhost:8065/api/v4/teams/name/uragami-note", headers=headers
    # )
    # print(response.json())

    # 参加しているチャンネルのIDリスト
    joining_channel_ids = get_channels_by_user(
        headers, user_id_dict[config.bot_name], team_id
    )

    # 現在時刻の1時間前まで
    for channel_id in joining_channel_ids:
        since = int((datetime.now().timestamp() - 3600) * 1000)
        posts = get_channel_posts(headers, channel_id, since)
        pinned_posts = get_channel_pinned_posts(headers, channel_id)

        # 各投稿を処理
        reaction_needed_posts = []
        for post_id in posts["order"]:
            if check_post(config, posts["posts"][post_id]):
                reaction_needed_posts.append(posts["posts"][post_id])

        if reaction_needed_posts:
            if config.model.startswith("deepseek"):
                client = OpenAI(
                    api_key=os.environ["DEEPSEEK_API_KEY"],
                    base_url="https://api.deepseek.com",
                )
            else:
                client = OpenAI()

            for post in reaction_needed_posts:
                process_post(
                    config,
                    post,
                    pinned_posts,
                    headers=headers,
                    team_id=team_id,
                    channel_id=channel_id,
                    user_id_dict=user_id_dict,
                    client=client,
                )


if __name__ == "__main__":
    main()
